from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class HttpClientError(RuntimeError):
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body[:500]}")


class JsonHttpClient:
    def get_json(self, url: str, *, access_token: str | None = None, headers: dict[str, str] | None = None) -> Any:
        return self._request_json("GET", url, access_token=access_token, headers=headers)

    def post_json(
        self,
        url: str,
        *,
        payload: dict[str, Any],
        access_token: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self._request_json(
            "POST",
            url,
            body=body,
            access_token=access_token,
            headers={"Content-Type": "application/json", **(headers or {})},
        )

    def patch_json(
        self,
        url: str,
        *,
        payload: dict[str, Any],
        access_token: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self._request_json(
            "PATCH",
            url,
            body=body,
            access_token=access_token,
            headers={"Content-Type": "application/json", **(headers or {})},
        )

    def post_form(self, url: str, *, payload: dict[str, str]) -> dict[str, Any]:
        body = urlencode(payload).encode("utf-8")
        return self._request_json(
            "POST",
            url,
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        access_token: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        request_headers = {"Accept": "application/json", **(headers or {})}
        if access_token:
            request_headers["Authorization"] = f"Bearer {access_token}"
        request = Request(url, data=body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
                if not raw:
                    return {}
                return json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise HttpClientError(exc.code, raw) from exc
