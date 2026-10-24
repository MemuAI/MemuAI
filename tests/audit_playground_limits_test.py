"""Audit regressions: request bodies are bounded while reading, not after."""
import asyncio
import json
import unittest

from starlette.requests import Request


def _chunked_request(chunks):
    """A request without Content-Length whose body arrives in pieces."""
    queue = list(chunks)

    async def receive():
        if queue:
            body = queue.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(queue)}
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "method": "POST", "path": "/", "headers": [],
             "query_string": b"", "http_version": "1.1"}
    return Request(scope, receive)


class BoundedBodies(unittest.TestCase):
    def test_playground_reader_stops_a_chunked_body_over_the_limit(self):
        from app.playground_api import _read_request_limited
        request = _chunked_request([b"x" * 600, b"x" * 600, b"x" * 600])
        with self.assertRaises(ValueError):
            asyncio.run(_read_request_limited(request, 1000, "too big"))

    def test_playground_reader_returns_a_body_within_the_limit(self):
        from app.playground_api import _read_request_limited
        request = _chunked_request([b'{"a":', b"1}"])
        raw = asyncio.run(_read_request_limited(request, 1000, "too big"))
        self.assertEqual(json.loads(raw), {"a": 1})

    def test_admin_json_object_rejects_a_chunked_body_over_the_limit(self):
        from app.admin_api import _json_object
        request = _chunked_request([b'{"a":"' + b"x" * 700, b"x" * 700 + b'"}'])
        body, error = asyncio.run(_json_object(request, max_bytes=1000))
        self.assertIsNone(body)
        self.assertEqual(error.status_code, 413)

    def test_admin_json_object_still_parses_small_bodies(self):
        from app.admin_api import _json_object
        body, error = asyncio.run(_json_object(_chunked_request([b'{"ok":', b" true}"]), max_bytes=1000))
        self.assertIsNone(error)
        self.assertEqual(body, {"ok": True})


if __name__ == "__main__":
    unittest.main()
