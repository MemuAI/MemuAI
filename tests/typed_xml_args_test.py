"""Explicit XML object parameters retain their names outside DSML transport."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest

from app import answer_guard

ROOT = Path(__file__).resolve().parents[1]


def cases():
    result = []
    for key in ("input", "args", "arguments", "parameters"):
        payload = {key: {"title": "x", "options": {"enabled": True}}}
        invoke = '<invoke name="mcp_create_record"><parameter name="' + key + '">' + json.dumps(payload[key]) + '</parameter></invoke>'
        for outer in ("", "function_calls", "tool_call"):
            text = f"<{outer}>{invoke}</{outer}>" if outer else invoke
            result.append((text, "mcp_create_record", payload))
    folder = {"path": r"C:\Windows.old\Users\Example\Desktop"}
    for bars in ("｜", "|"):
        invoke_open = f'<{bars}DSML{bars} invoke name="list_folder">'
        invoke_close = f'</{bars}DSML{bars} invoke>'
        body = f'<{bars}DSML{bars} parameter name="args" string="false">' + json.dumps(folder) + f'</{bars}DSML{bars} parameter>'
        result.append((invoke_open + body + invoke_close, "list_folder", folder))
        result.append((f'<{bars}DSML{bars} calls>' + invoke_open + body + invoke_close + f'</{bars}DSML{bars} calls>', "list_folder", folder))
        # A JSON body already describes the actual argument map, including input.
        result.append((invoke_open + json.dumps({"input": folder}) + invoke_close, "list_folder", {"input": folder}))
        # Some DSML responses leave the inner XML invoke unprefixed.
        result.append((f'<{bars}DSML{bars} calls><invoke name="list_folder"><parameter name="args">' + json.dumps(folder)
                       + f'</parameter></invoke></{bars}DSML{bars} calls>', "list_folder", folder))
    result.append(('<invoke name="mcp_create_record">{"input":{"title":"x"}}</invoke>', "mcp_create_record", {"input": {"title": "x"}}))
    return result


class TypedXmlArguments(unittest.TestCase):
    def test_python_preserves_named_xml_objects_and_repairs_dsml_holders(self):
        for text, tool, expected in cases():
            with self.subTest(text=text):
                visible, calls = answer_guard.extract_calls(text)
                self.assertEqual(visible, "")
                self.assertEqual(calls, [{"tool": tool, "args": expected}])

    @unittest.skipUnless(shutil.which("node"), "Node.js required")
    def test_javascript_matches_python_for_each_transport(self):
        script = "const fs=require('fs'),g=require('./app/pgstatic/answer-guard.js');const cases=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(cases.map(row=>g.extractCalls(row[0]))));"
        fixtures = cases()
        result = subprocess.run(["node", "-e", script], input=json.dumps(fixtures), text=True, encoding="utf-8",
                                cwd=ROOT, capture_output=True, check=True, timeout=15)
        parsed = json.loads(result.stdout)
        self.assertEqual(len(parsed), len(fixtures))
        for (text, tool, expected), actual in zip(fixtures, parsed):
            with self.subTest(text=text):
                self.assertEqual(actual["text"], "")
                self.assertEqual(actual["calls"], [{"tool": tool, "args": expected}])

    def test_dsml_examples_in_code_are_visible_and_never_executed(self):
        literal = '<｜DSML｜ calls><｜DSML｜ invoke name="list_folder">{"path":"."}</｜DSML｜ invoke></｜DSML｜ calls>'
        examples = ["```xml\n" + literal + "\n```", "`" + literal + "`", "~~~xml\n" + literal + "\n~~~", "Код 👩‍💻\n```xml\n" + literal + "\n```"]
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(answer_guard.clean(text), text)
                self.assertEqual(answer_guard.extract_calls(text), (text, []))
        live = '<｜DSML｜ calls><｜DSML｜ invoke name="list_folder">{"path":"live"}</｜DSML｜ invoke></｜DSML｜ calls>'
        text = examples[0] + "\n" + live
        visible, calls = answer_guard.extract_calls(text)
        self.assertEqual(visible, examples[0])
        self.assertEqual(calls, [{"tool": "list_folder", "args": {"path": "live"}}])

    @unittest.skipUnless(shutil.which("node"), "Node.js required")
    def test_python_and_javascript_keep_code_examples_and_parse_outer_dsml(self):
        literal = '<｜DSML｜ calls><｜DSML｜ invoke name="list_folder">{"path":"."}</｜DSML｜ invoke></｜DSML｜ calls>'
        fenced = "```xml\n" + literal + "\n```"
        inline = "`" + literal + "`"
        content = "```python\nprint('hello')\n```"
        outer = '<｜DSML｜ calls><｜DSML｜ invoke name="mcp_create_record"><｜DSML｜ parameter name="content" string="true">' + content + '</｜DSML｜ parameter></｜DSML｜ invoke></｜DSML｜ calls>'
        fixtures = [(fenced, fenced, []), (inline, inline, []), (outer, "", [{"tool": "mcp_create_record", "args": {"content": content}}]),
                    (fenced + "\n" + outer, fenced, [{"tool": "mcp_create_record", "args": {"content": content}}])]
        script = "const fs=require('fs'),g=require('./app/pgstatic/answer-guard.js');const cases=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(cases.map(row=>({clean:g.clean(row[0]),...g.extractCalls(row[0])}))));"
        result = subprocess.run(["node", "-e", script], input=json.dumps(fixtures), text=True, encoding="utf-8",
                                cwd=ROOT, capture_output=True, check=True, timeout=15)
        parsed = json.loads(result.stdout)
        self.assertEqual(len(parsed), len(fixtures))
        for (text, visible, expected), actual in zip(fixtures, parsed):
            with self.subTest(text=text):
                self.assertEqual(answer_guard.extract_calls(text), (visible, expected))
                self.assertEqual(actual["text"], visible)
                self.assertEqual(actual["calls"], expected)
                self.assertEqual(actual["clean"], answer_guard.clean(text))


if __name__ == "__main__":
    unittest.main()
