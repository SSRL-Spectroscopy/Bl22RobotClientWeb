"""Client for the mail-in XAS portal's instrument API (the "BL22 REST API").

Described in reference/instrument-api.md. The portal is where users define
what to measure; this client reads the queue and reports progress.

The API key is a secret. It is sent only in the Authorization header to the
configured portal host and never appears in logs, errors or browser traffic.
"""

import re
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import SecretStr

# New barcodes are ABC-DEF, old ones ABCD-EFGH; the portal ignores case.
_BARCODE = re.compile(r"^(?:[A-Za-z]{3}-[A-Za-z]{3}|[A-Za-z]{4}-[A-Za-z]{4})$")
_OBJECT_ID = re.compile(r"^[0-9a-f]{24}$")

FRAME_STATES_SETTABLE = ("scheduled", "loaded", "internal_storage")
MEASUREMENT_STATUSES = ("pending", "done", "error")


class PortalError(Exception):
    """The portal could not be reached, or it refused the request."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class PortalNotConfigured(PortalError):
    def __init__(self) -> None:
        super().__init__(
            "The BL22 API is not configured. Set ROBOTWEB_BL22_API_URL and "
            "ROBOTWEB_BL22_API_KEY in backend/.env and restart the backend."
        )


def clean_barcode(barcode: str) -> str:
    barcode = barcode.strip().upper()
    if not _BARCODE.fullmatch(barcode):
        raise PortalError(f"{barcode!r} is not a frame barcode", 422)
    return barcode


class PortalClient:
    def __init__(
        self,
        base_url: str,
        api_key: SecretStr | None,
        *,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._configured = bool(base_url) and api_key is not None and bool(api_key.get_secret_value())
        headers = {"Accept": "application/json"}
        if self._configured:
            headers["Authorization"] = f"Bearer {api_key.get_secret_value()}"
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") if base_url else "http://not-configured.invalid",
            headers=headers,
            timeout=timeout_s,
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return self._configured

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        if not self._configured:
            raise PortalNotConfigured()
        try:
            response = await self._http.request(method, path, json=body)
        except httpx.HTTPError as exc:
            # str(exc) names the host but never the headers, so no key leaks.
            raise PortalError(f"The portal cannot be reached: {type(exc).__name__}: {exc}") from exc
        if response.is_success:
            try:
                return response.json()
            except ValueError as exc:
                raise PortalError("The portal sent an answer that is not JSON.", response.status_code) from exc
        raise PortalError(_error_text(response), response.status_code)

    # ---- reading ----------------------------------------------------------

    async def health(self) -> dict:
        return await self._request("GET", "/health")

    async def queue(self) -> list[dict]:
        """Frames in internal storage that are locked and have pending measurements."""
        return await self._request("GET", "/instrument/queue")

    async def frame(self, barcode: str) -> dict:
        return await self._request("GET", f"/instrument/frames/{quote(clean_barcode(barcode))}")

    async def internal_storage(self) -> list[dict]:
        return await self._request("GET", "/instrument/storage/internal")

    # ---- reporting progress (used by sequences) -----------------------------

    async def set_frame_state(self, barcode: str, state: str) -> dict:
        if state not in FRAME_STATES_SETTABLE:
            raise PortalError(f"a frame cannot be moved to state {state!r}", 422)
        return await self._request(
            "POST", f"/instrument/frames/{quote(clean_barcode(barcode))}/state", {"state": state}
        )

    async def set_measurement_status(
        self, measurement_id: str, status: str, error_message: str | None = None
    ) -> dict:
        if not _OBJECT_ID.fullmatch(measurement_id):
            raise PortalError("not a measurement id", 422)
        if status not in MEASUREMENT_STATUSES:
            raise PortalError(f"unknown measurement status {status!r}", 422)
        body: dict = {"status": status}
        if status == "error" and error_message:
            body["error_message"] = error_message[:2000]
        return await self._request("PUT", f"/instrument/measurements/{measurement_id}/status", body)


def _error_text(response: httpx.Response) -> str:
    """The portal's "detail" is a string for most errors and a list for 422."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list):
        detail = "; ".join(str(entry.get("msg", entry)) if isinstance(entry, dict) else str(entry) for entry in detail)
    if not detail:
        detail = "no details given (the request may have gone through the public proxy)" if response.status_code == 403 else "no details given"
    return f"The portal answered {response.status_code}: {detail}"
