#!/usr/bin/env python3

from flask import Flask, Response, request, abort
from sesamutils.flask import serve
from sesamutils import sesam_logger
from sesamutils import VariablesConfig
import json
import requests
import sys
import time

# Activate logging
logger = sesam_logger("sap-odata-source")

# Get env.vars
required_env_vars = ["SERVICE_URL"]
optional_env_vars = [
    "LOG_LEVEL",
    ("AUTH_TYPE", "basic"),
    "USERNAME",
    "PASSWORD",
    "TOKEN_URL",
    "TOKEN_REQUEST_HEADERS",
    "TOKEN_REQUEST_BODY"
]

env_vars = VariablesConfig(required_env_vars, optional_env_vars=optional_env_vars)

# Check that all required env.vars are supplied
if not env_vars.validate():
    sys.exit(1)

# Authentication
auth = None


def get_access_token(token_url, headers, body):
    logger.debug(f"token request url    : {token_url}")
    logger.debug(f"token request headers: {headers}")
    logger.debug(f"token request body   : {body}")

    access_token_response = requests.post(token_url, headers=headers, json=body)
    tokens = json.loads(access_token_response.text)
    access_token = tokens['access_token']

    return access_token


if env_vars.AUTH_TYPE.lower() == "basic":
    logger.info("Using basic authentication")
    auth = (env_vars.USERNAME, env_vars.PASSWORD)

elif env_vars.AUTH_TYPE.lower() == "token":
    logger.info(f"Using token authentication")

    # token_url = "https://banenorsf-stage.plateau.com/learning/oauth-api/rest/v1/token"
    token_url = env_vars.TOKEN_URL

    # base_url = "https://banenorsf-stage.plateau.com/learning/odatav4/"
    base_url = env_vars.SERVICE_URL
    # data_api = "searchCurriculum/v1/Curricula"

    headers = json.loads(env_vars.TOKEN_REQUEST_HEADERS)
    body = json.loads(env_vars.TOKEN_REQUEST_BODY)

    auth = get_access_token(token_url, headers, body)

else:
    logger.error(f"Unsupported authentication type: {env_vars.AUTH_TYPE}")
    sys.exit(1)

# Start the service
app = Flask(__name__)


@app.route("/<path:entity_set>", methods=["GET"])
def get_entity_set(entity_set):
    """Service entry point."""

    query = get_url_query(request)
    since_property = request.args.get("since_property") or "lastModifiedDateTime"
    since_enabled = request.args.get("since") is not None

    url = f"{env_vars.SERVICE_URL}{entity_set}?$format=json&{query}"

    if since_enabled and since_property:
        since = request.args.get("since")
        url += f"&$filter={since_property} gt '{since}'"

    return Response(process_request(url=url, since_enabled=since_enabled, since_property=since_property),
                    mimetype="application/json")


def get_url_query(req):
    """Get the 'query' part of the request."""
    args = dict(req.args)
    query = ""
    for key in args.keys():
        if len(query):
            query += "&"

        query += f"{key}={args[key]}"

    return query


def process_request(url, since_enabled, since_property):
    """Fetch entities from the given Odata url and dump them back to client as a JSON stream."""

    logger.debug(f"since_enabled: {since_enabled}")
    logger.debug(f"since_property: {since_property}")

    yield '['
    first = True
    count = 0  # number of entities fetched

    while url:
        logger.info(f"Request url: {url}")

        if env_vars.AUTH_TYPE.lower() == "token":
            logger.debug("Token auth")
            headers = {'Authorization': 'Bearer ' + auth}
            response = requests.get(url, headers=headers, verify=True)
        else:
            response = requests.get(url, auth=auth)

        if not response.ok:
            abort(response.status_code)

        data = response.json()
        entities = None

        # Entities of interest are either returned as { "d": { <entities> } }
        # or as { "d": { "results": [<entities>] } }

        # Try to fetch entities from "d.results" first
        if "d" in data:
            if "results" in data.get("d"):
                entities = data["d"].get("results")

        # Then try to fetch from "d"
        if entities is None:  # explicitly check on None to not overwrite empty "results" list
            entities = data.get("d")

        # Then try to fetch from "value"
        if entities is None:
            entities = data.get("value")

        # Stop if there are no entities to process
        if not entities:
            break

        # Make single entity a list so that the for-loop below can be used regardless
        if not isinstance(entities, type(list())):
            entities = [entities]

        for entity in entities:

            if not first:
                yield ','
            else:
                first = False

            # Convert dates one level deep
            for key in entity:

                value = entity.get(key)

                # Dates are represented as strings in SAP so all non-string values can be skipped
                if not isinstance(value, str):
                    continue

                if value and "/Date(" in value:
                    iso_date = sap_epoch_to_iso_date(value)
                    # logger.debug(f"{key}: {value} --> {iso_date}")
                    entity[key] = iso_date

            # entity["_updated"] = entity.get(since_property)
            # entity["_updated"] = time.gmtime('%Y-%m-%dT%H:%M:%S')  # set current GMT time
            entity["_updated"] = time.strftime('%Y-%m-%dT%H:%M:%S')  # set current local time

            count += 1
            yield json.dumps(entity)

        if "d" in data:
            url = data["d"].get("__next")
        else:
            url = None

    logger.info(f"Fetched {count} entities")
    yield ']'


def sap_epoch_to_iso_date(sap_epoch):
    """Convert SAP date string in UTC milliseconds to local ISO date."""

    epoch_string = str(sap_epoch).replace("/Date(", "").replace(")/", "")  # isolate epoch time
    epoch_ms = epoch_string.replace("+0000", "")  # epoch in ms (UTC)
    epoch_timestamp = int(epoch_ms) / 1000  # epoch in seconds (UTC)
    # dt = time.gmtime(epoch_timestamp)  # GMT time
    dt = time.localtime(epoch_timestamp)  # local time
    dt_formatted = time.strftime('%Y-%m-%dT%H:%M:%S', dt)  # ISO formatted

    return dt_formatted


if __name__ == "__main__":
    serve(app)
