"""This module contains a small read-only client for the Leanheat Monitor External API v2.

"""

import time
from datetime import datetime, timezone

import requests

API_TIMEOUT_SECONDS = 60
API_RETRY_COUNT = 4
RETRY_STATUS_CODES = (429, 500, 502, 503, 504)


class LeanheatClient:
    """A read-only client for the Leanheat Monitor External API v2."""

    def __init__(self, api_key: str, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "X-API-KEY": api_key,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        })

    def _request(self, method: str, path: str, json_body: dict | None = None) -> dict:
        """Send a request and retry with backoff on rate limiting, server errors and connection errors."""
        url = f"{self.base_url}{path}"
        for attempt in range(1, API_RETRY_COUNT + 1):
            try:
                response = self.session.request(method, url, json=json_body, timeout=API_TIMEOUT_SECONDS)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == API_RETRY_COUNT:
                    raise
            else:
                if response.status_code not in RETRY_STATUS_CODES or attempt == API_RETRY_COUNT:
                    response.raise_for_status()
                    return response.json()
            time.sleep(5 * 2 ** (attempt - 1))
        raise RuntimeError("Unreachable")

    def get_devices(self) -> list[dict]:
        """Get all devices for the tenant."""
        data = self._request("GET", "/devices")
        devices = data.get("devices")
        if not isinstance(devices, list):
            raise ValueError("API-svaret indeholder ikke det forventede felt 'devices'.")
        return [d for d in devices if isinstance(d, dict)]

    def get_subjects(self, device_id: str) -> dict:
        """Get the subjects (measurements) that can be read from a device."""
        return self._request("GET", f"/devices/{device_id}/subjects")

    def get_series(self, device_ids: list[str], subjects: list[str], from_utc: datetime, to_utc: datetime) -> dict:
        """Get series data for max 8 devices, 10 subjects and 7 days.

        Returns:
            The 'data' object of the response: {"subjects": ["ts", ...], "values": {device_id: [[ts_ms, v1, ...], ...]}}
        """
        body = {
            "devices": device_ids,
            "subjects": subjects,
            "from": format_api_time(from_utc),
            "to": format_api_time(to_utc),
        }
        return self._request("POST", "/devices/data/series/q", body)["data"]


def format_api_time(moment: datetime) -> str:
    """Format a datetime as the 24 character ISO UTC string the API expects, e.g. 2026-10-27T23:00:00.000Z"""
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
