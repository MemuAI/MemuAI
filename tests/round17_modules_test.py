"""Regression proof for the three redesigned built-in modules."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

class ModulesBrowserTest(unittest.TestCase):
    @unittest.skipUnless(os.name=="nt","Windows Edge regression")
    def test_shipped_modules_responsive_interactions(self):
        from app.automation_runtime import _edge_executable
        try: _edge_executable()
        except RuntimeError as exc: self.skipTest(str(exc))
        runner=Path(__file__).with_name("round17_modules_browser.py")
        result=subprocess.run([sys.executable,str(runner)],capture_output=True,text=True,encoding="utf-8",timeout=120,env={**os.environ,"PYTHONUTF8":"1"})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn("PASS:",result.stdout)

if __name__=="__main__":unittest.main()
