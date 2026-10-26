"""Bounded raw fetching for catalogs larger than the ordinary page limit."""
import unittest
from unittest.mock import AsyncMock, patch

from app import websearch

class CatalogFetchTest(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, size, *, raw=True, limit=8_000_000):
        class Response:
            status_code = 200
            headers = {"content-type": "application/json"}

            async def aiter_content(self):
                for offset in range(0, size, 65536):
                    yield b"x" * min(65536, size - offset)

            aclose = AsyncMock()

        session = AsyncMock()
        session.get.return_value = Response()
        with patch.object(websearch.http, "get_session", return_value=session), patch.object(websearch, "_resolve_public", new=AsyncMock()):
            result = await websearch.fetch_page("https://models.dev/api.json?type=all", raw=raw, limit=limit)
        self.assertTrue(session.get.call_args.kwargs["stream"])
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
        Response.aclose.assert_awaited_once()
        return result

    async def test_large_catalog_is_complete(self):
        result = await self.fetch(5_400_000)
        self.assertEqual(len(result["text"]), 5_400_000)
        self.assertFalse(result["truncated"])

    async def test_raw_ceiling_remains_bounded(self):
        result = await self.fetch(8_100_000, limit=20_000_000)
        self.assertEqual(len(result["text"]), 8_000_000)
        self.assertTrue(result["truncated"])

    async def test_small_requested_limit_is_still_honoured(self):
        result = await self.fetch(10_000, limit=1000)
        self.assertEqual(len(result["text"]), 1000)
        self.assertTrue(result["truncated"])

    async def test_ordinary_pages_keep_previous_bound(self):
        result = await self.fetch(5_400_000, raw=False, limit=8_000_000)
        self.assertEqual(len(result["text"]), websearch._MAX_PAGE_BYTES)
        self.assertTrue(result["truncated"])
