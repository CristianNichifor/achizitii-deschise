"""Rate-limited HTTP client for the SEAP `api-pub` endpoints.

The endpoints are undocumented but public. They are the same ones the e-licitatie.ro
Angular front-end calls, so no browser automation is needed — only a Referer header.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Self

import httpx

from . import config

log = logging.getLogger(__name__)


class RateLimiter:
    """Simple thread-safe minimum-interval limiter."""

    def __init__(self, max_rps: float) -> None:
        self._interval = 1.0 / max_rps
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next - now
            self._next = max(now, self._next) + self._interval
        if sleep_for > 0:
            time.sleep(sleep_for)


class SeapClient:
    """Thin wrapper with retries, backoff and rate limiting.

    Retries on connection errors, 429 and 5xx. Does NOT retry 4xx other than 429 —
    those mean we sent something wrong and repeating it is just rude.
    """

    def __init__(self, max_rps: float = config.MAX_RPS) -> None:
        self._limiter = RateLimiter(max_rps)
        self._client = httpx.Client(
            http2=True,
            timeout=config.TIMEOUT,
            headers=config.HEADERS,
            follow_redirects=True,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        url = f"{config.API}{path}"
        last: Exception | None = None
        for attempt in range(config.RETRIES):
            self._limiter.wait()
            try:
                r = self._client.request(method, url, **kw)
            except httpx.HTTPError as exc:  # connect/read/protocol errors
                last = exc
                log.warning("%s %s failed (%s), attempt %d", method, path, exc, attempt + 1)
            else:
                if r.status_code == 200:
                    return r
                if r.status_code == 429 or r.status_code >= 500:
                    last = httpx.HTTPStatusError(
                        f"HTTP {r.status_code}", request=r.request, response=r
                    )
                    log.warning("%s %s -> %d, attempt %d", method, path, r.status_code, attempt + 1)
                else:
                    r.raise_for_status()
            time.sleep(2**attempt)
        assert last is not None
        raise last

    def get(self, path: str) -> Any:
        return self._request("GET", path).json()

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("POST", path, json=payload).json()

    # -- endpoints -------------------------------------------------------------

    def direct_acquisition_list(
        self, finalization_date: str, page_index: int = 0, page_size: int | None = None
    ) -> dict[str, Any]:
        """One page of direct acquisitions finalized on `finalization_date` (YYYY-MM-DD)."""
        return self.post(
            "/DirectAcquisitionCommon/GetDirectAcquisitionList/",
            {
                "pageSize": page_size or config.LIST_PAGE_SIZE,
                "showOngoingDa": False,
                "cookieContext": None,
                "pageIndex": page_index,
                "sysDirectAcquisitionStateId": None,
                "publicationDateStart": None,
                "publicationDateEnd": None,
                "finalizationDateStart": finalization_date,
                "finalizationDateEnd": finalization_date,
            },
        )

    def direct_acquisition(self, da_id: int) -> dict[str, Any]:
        """Full detail including `directAcquisitionItems[]` (the line items)."""
        return self.get(f"/PublicDirectAcquisition/getView/{da_id}")

    def notice_list(
        self, start_date: str, end_date: str, page_index: int = 0, page_size: int | None = None
    ) -> dict[str, Any]:
        """Tender notices published in a date window."""
        return self.post(
            "/NoticeCommon/GetCANoticeList/",
            {
                "sysNoticeTypeIds": [3, 13, 18, 16, 8],
                "sortProperties": [],
                "pageSize": page_size or config.LIST_PAGE_SIZE,
                "sysNoticeStateId": None,
                "contractingAuthorityId": None,
                "winnerId": None,
                "cPVCategoryId": None,
                "sysContractAssigmentTypeId": None,
                "cPVId": None,
                "assignedUserId": None,
                "sysAcquisitionContractTypeId": None,
                "pageIndex": page_index,
                "startPublicationDate": start_date,
                "endPublicationDate": end_date,
            },
        )
