from pathlib import Path
import json
import shutil
import subprocess
import unittest
import sys

ROOT=Path(__file__).resolve().parents[1]

class CatalogBenchmarksTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js required')
    def test_same_core_variants_directions_partial_sources_and_family_matching(self):
        done=subprocess.run(['node',str(ROOT/'tests/catalog_benchmarks_runner.cjs')],capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(done.returncode,0,done.stderr+done.stdout)
        result=json.loads(next(line for line in done.stdout.splitlines() if line.startswith('{')))
        self.assertTrue(result['passed'])

    def test_both_consumers_declare_shared_benchmark_data_and_keep_version(self):
        for name in ('benchmarks','catalog'):
            base=ROOT/'app/module_builtins'/name
            self.assertIn('/* @memu-shared:benchmarks */',(base/'main.js').read_text(encoding='utf-8'))
            self.assertEqual(json.loads((base/'manifest.json').read_text(encoding='utf-8'))['version'],'1.0')

    @unittest.skipUnless(sys.platform=='win32','Hidden Edge required')
    def test_browser_detail_comparison_source_failures_and_responsive_themes(self):
        done=subprocess.run([sys.executable,str(ROOT/'tests/catalog_benchmarks_browser.py')],capture_output=True,text=True,encoding='utf-8',timeout=90)
        self.assertEqual(done.returncode,0,done.stderr+done.stdout)
        self.assertIn('PASS:',done.stdout)

if __name__=='__main__':unittest.main()
