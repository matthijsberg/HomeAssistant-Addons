"""
Open HEMS: Model Domain Router Facade
======================================
Delegates operational forecasting to `api/routes_forecast.py` and
self-learning calibration governance to `api/routes_calibration.py`.
Adheres to Single Responsibility (SOLID) and Invariant #1.
"""

from api import routes_forecast, routes_calibration


def handle_get(handler, path: str, qp: dict) -> bool:
    """Delegates GET requests to forecast or calibration sub-routers."""
    if routes_forecast.handle_get(handler, path, qp):
        return True
    if routes_calibration.handle_get(handler, path, qp):
        return True
    return False


def handle_post(handler, path: str, body: dict) -> bool:
    """Delegates POST requests to calibration sub-router."""
    if routes_calibration.handle_post(handler, path, body):
        return True
    return False
