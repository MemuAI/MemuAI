"""The UI and unattended scheduler must offer the same supported action contract."""
import json
from pathlib import Path
import re
import unittest

from app import schedule_rules


class ScheduleProtocol(unittest.TestCase):
    def test_ui_protocol_matches_the_server_including_team_tasks(self):
        source = (Path(__file__).resolve().parents[1] / "app/pgstatic/plan.js").read_text(encoding="utf-8")
        match = re.search(r'const PROTOCOL = ("(?:[^"\\]|\\.)*");', source)
        self.assertIsNotNone(match)
        protocol = json.loads(match[1])
        self.assertEqual(protocol, schedule_rules.CHAT_PROTOCOL)
        self.assertIn('"team"', protocol)
        self.assertIn('"team_id"', protocol)


if __name__ == "__main__":
    unittest.main()
