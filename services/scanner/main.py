"""uvicorn --factory services.scanner.main:app_factory --port $SCANNER_PORT

The only process that writes custody_events, and it does so as scanner_svc.
"""

import os

from agent.db import client, database_name
from services.config import LabConfig
from services.scanner.app import create_app
from services.scanner.store import MongoEventStore


def create(config: LabConfig, store, secret: str):
    return create_app(store, config.readers, config.sample_ids, secret)


def app_factory():
    config = LabConfig.load(os.environ.get("LAB_CONFIG", ".lab_config.json"))
    db = client(os.environ["MONGODB_URI_SCANNER"])[database_name()]
    return create(config, MongoEventStore(db), os.environ["SCANNER_HMAC_SECRET"])
