"""uvicorn --factory services.scheduler.main:app_factory --port $SCHEDULER_PORT"""

from services.config import LabConfig
from services.scheduler.app import create_app


def create(config: LabConfig, emit=None):
    return create_app()  # nothing physical happens when a station is booked: no scans


def app_factory():
    return create(None)
