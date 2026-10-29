"""Disabling the bot commits its final offset under the old token."""
import unittest
from unittest.mock import AsyncMock, patch

from app import pgdata, telegram_bot, telegram_pairing
from tests.telegram_r21_reliability_test import RuntimeCase, TOKEN


class DisableCheckpoint(RuntimeCase):
    async def test_final_offset_is_saved_before_token_and_lock_are_cleared(self):
        runner = self.runner = telegram_bot._BotRunner(self.factory())
        runner.token = runner.status_token = TOKEN
        runner.offset = 42
        runner.pending_off = 1
        runner.ensure_lock(telegram_pairing.token_fingerprint(TOKEN))
        runner.save_offset_soon()
        pgdata.save_preferences({"telegram_enabled": False})
        with patch.object(runner, "_sleep", AsyncMock()):
            await runner._iteration()
        self.assertEqual(runner.token, "")
        self.assertEqual(runner.status, "disabled")
        self.assertIsNone(runner.lock)
        poll = pgdata.load_telegram_state()["poll"]
        self.assertEqual(poll, {"token_fp": telegram_pairing.token_fingerprint(TOKEN), "offset": 42})


if __name__ == "__main__":
    unittest.main()
