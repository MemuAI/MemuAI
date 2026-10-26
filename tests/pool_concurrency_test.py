"""Account results are updated as one transaction under concurrent requests."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import patch

from app.pool import AccountPool
from app.state import Account, state

class AccountPoolConcurrencyTests(unittest.TestCase):
    def test_success_updates_are_serialized(self) -> None:
        account = Account("custom", fields={"name": "test"})
        original = account.record_result
        guard = threading.Lock()
        active = 0
        peak = 0

        def observed(*args, **kwargs):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.005)
            try:
                original(*args, **kwargs)
            finally:
                with guard:
                    active -= 1

        account.record_result = observed
        pool = AccountPool()
        with patch.object(state, "save_accounts"), ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _: pool.report_success(account), range(20)))
        self.assertEqual(peak, 1)
        self.assertEqual(account.stats["requests"], 20)
        self.assertEqual(account.stats["success"], 20)

if __name__ == "__main__":
    unittest.main()
