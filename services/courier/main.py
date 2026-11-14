"""uvicorn --factory services.courier.main:app_factory --port $COURIER_PORT"""

import os

from services.config import LabConfig, http_emit
from services.courier.app import create_app


def create(config: LabConfig, emit):
    speed = float(os.environ.get("DEMO_SPEED", "60"))  # 1 real second = DEMO_SPEED/60 sim minutes
    return create_app(config.travel, config.courier_readers, emit, demo_speed=speed)


def app_factory():
    config = LabConfig.load(os.environ.get("LAB_CONFIG", ".lab_config.json"))
    return create(config, http_emit(os.environ["SCANNER_URL"]))
