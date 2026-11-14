"""uvicorn --factory services.freezer.main:app_factory --port $FREEZER_PORT"""

import os

from services.config import LabConfig, http_emit
from services.freezer.app import create_app


def create(config: LabConfig, emit):
    return create_app(dict(config.inventory), emit, config.freezer_readers)


def app_factory():
    config = LabConfig.load(os.environ.get("LAB_CONFIG", ".lab_config.json"))
    return create(config, http_emit(os.environ["SCANNER_URL"]))
