from __future__ import annotations

from http import cookies
from typing import Iterable


def parse_cookie_header(header: str | None) -> dict[str, str]:
    jar = cookies.SimpleCookie()
    if header:
        jar.load(header)
    return {key: morsel.value for key, morsel in jar.items()}


def build_cookie(
    name: str,
    value: str,
    *,
    max_age: int | None = None,
    path: str = "/",
    http_only: bool = True,
    secure: bool = False,
    same_site: str = "Lax",
) -> str:
    jar = cookies.SimpleCookie()
    jar[name] = value
    morsel = jar[name]
    morsel["path"] = path
    morsel["samesite"] = same_site
    if max_age is not None:
        morsel["max-age"] = str(max_age)
    if http_only:
        morsel["httponly"] = True
    if secure:
        morsel["secure"] = True
    return morsel.OutputString()


def clear_cookie(name: str, *, path: str = "/", secure: bool = False) -> str:
    return build_cookie(name, "", max_age=0, path=path, secure=secure)


def merge_cookie_headers(*headers: str | None) -> list[str]:
    return [header for header in headers if header]


def cookie_headers(headers: Iterable[str]) -> list[tuple[str, str]]:
    return [("Set-Cookie", header) for header in headers]

