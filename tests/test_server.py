from __future__ import annotations

import io
import unittest
from types import SimpleNamespace

from app.server import ZampRequestHandler


class FakeSocket:
    def __init__(self, request: bytes) -> None:
        self.reader = io.BytesIO(request)
        self.writer = io.BytesIO()

    def makefile(self, mode: str, buffering: int | None = None) -> io.BytesIO:
        if "r" in mode:
            return self.reader
        return self.writer

    def sendall(self, data: bytes) -> None:
        self.writer.write(data)


class ServerRouteTests(unittest.TestCase):
    def test_get_logout_is_non_mutating(self) -> None:
        class Handler(ZampRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

        request = (
            b"GET /logout HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Cookie: zamp_session=sealed\r\n"
            b"\r\n"
        )
        fake_socket = FakeSocket(request)
        Handler(fake_socket, ("127.0.0.1", 12345), SimpleNamespace())
        response = fake_socket.writer.getvalue().decode("iso-8859-1")

        self.assertIn(" 405 ", response.splitlines()[0])
        self.assertIn("Allow: POST\r\n", response)
        self.assertNotIn("Set-Cookie:", response)
