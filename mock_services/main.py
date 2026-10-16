"""Mock external services: payments, shipping and ticketing in one process.

Run: ``uvicorn mock_services.main:app --port 8001``

Admin endpoints (``X-Admin-Token``) reset data and configure faults so failure
recovery can be exercised deterministically.
"""

from mock_services.app_factory import create_app

app = create_app()
