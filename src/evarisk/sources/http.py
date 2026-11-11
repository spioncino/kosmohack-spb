import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from evarisk.timeutil import utc_now


class FetchError(RuntimeError):
    def __init__(self, url: str, status: int | None, detail: str) -> None:
        super().__init__(f"{url}: {status}: {detail}")
        self.url = url
        self.status = status


def http_date(value: str | None) -> datetime | None:
    """Время из заголовка HTTP-date (Last-Modified) как tz-aware UTC; без заголовка или при нечитаемом значении None."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content: bytes
    content_type: str
    fetched_at: datetime
    last_modified: datetime | None = None


class HttpFetcher:
    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        retries: int = 3,
        backoff_s: float = 1.0,
        timeout_s: float = 60.0,
        user_agent: str = "evarisk/0.1 (kosmohackathon-2026)",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(timeout=timeout_s, follow_redirects=True)
        self._retries = max(1, retries)
        self._backoff_s = backoff_s
        self._headers = {"User-Agent": user_agent}
        self._sleep = sleep

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        last_detail = ""
        for attempt in range(1, self._retries + 1):
            try:
                resp = self._client.get(url, params=params, headers={**self._headers, **(headers or {})})
            except httpx.HTTPError as exc:
                last_detail = f"{type(exc).__name__}: {exc}"
            else:
                if 200 <= resp.status_code < 300:
                    return FetchResult(
                        url=str(resp.url),
                        status=resp.status_code,
                        content=resp.content,
                        content_type=resp.headers.get("content-type", "application/octet-stream"),
                        last_modified=http_date(resp.headers.get("last-modified")),
                        fetched_at=utc_now(),
                    )
                if resp.status_code < 500:
                    raise FetchError(url, resp.status_code, resp.text[:200])
                last_detail = f"HTTP {resp.status_code}"
            if attempt < self._retries:
                self._sleep(self._backoff_s * attempt)
        raise FetchError(url, None, f"после {self._retries} попыток: {last_detail}")

    def post(self, url: str, *, data: Mapping[str, str]) -> FetchResult:
        try:
            resp = self._client.post(url, data=dict(data), headers=self._headers)
        except httpx.HTTPError as exc:
            raise FetchError(url, None, f"{type(exc).__name__}: {exc}") from exc
        if not 200 <= resp.status_code < 300:
            raise FetchError(url, resp.status_code, resp.text[:200])
        return FetchResult(
            url=str(resp.url),
            status=resp.status_code,
            content=resp.content,
            content_type=resp.headers.get("content-type", "application/octet-stream"),
            last_modified=http_date(resp.headers.get("last-modified")),
            fetched_at=utc_now(),
        )
