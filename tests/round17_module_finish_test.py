import os
from pathlib import Path
import subprocess
import sys
import unittest

class RelatedModuleFinishTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows Edge proof")
    def test_localized_design_status_and_narrow_translator_controls(self):
        runner = Path(__file__).with_name("round17_module_finish_browser.py")
        result = subprocess.run([sys.executable, str(runner)], capture_output=True, text=True, encoding="utf-8", timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS:", result.stdout)
