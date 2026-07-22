from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class IngestionTransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class UrllibTransport:
    def __init__(self, *, base_url: str, api_key: str, timeout: float = 20.0) -> None:
        if not base_url.startswith(("https://", "http://")):
            raise ValueError("base_url must be an HTTP(S) URL")
        if not api_key:
            raise ValueError("api_key must not be empty")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = float(timeout)

    def request(
        self,
        *,
        method: str,
        endpoint: str,
        body: bytes,
        idempotency_key: Optional[str],
    ) -> HttpResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = Request(
            self.base_url + endpoint,
            data=body if method != "GET" else None,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return HttpResponse(
                    int(response.status),
                    response.read(),
                    dict(response.headers.items()),
                )
        except HTTPError as exc:
            return HttpResponse(int(exc.code), exc.read(), dict(exc.headers.items()))
        except URLError as exc:
            raise IngestionTransportError(str(exc.reason)) from exc
