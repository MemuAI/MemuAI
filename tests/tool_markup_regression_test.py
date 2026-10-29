"""Offline regression checks for textual tool protocols across assistant surfaces."""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from app import answer_guard
from app.cli import protocol
from app.memufiles import assistant as files

ROOT = Path(__file__).resolve().parents[1]


def dsml(body, *, name="list_folder", outer="calls", bars="｜", space=" "):
    tag = lambda value: f"<{bars}DSML{bars}{space}{value}>"
    close = lambda value: f"</{bars}DSML{bars}{space}{value}>"
    return tag(outer) + tag(f'invoke name="{name}"') + body + close("invoke") + close(outer)


FOLDER_ARGS = {"path": r"C:\Windows.old\Users\Example\Desktop"}
FOLDER_BODY = '<｜DSML｜ parameter name="args" string="false">' + json.dumps(FOLDER_ARGS) + '</｜DSML｜ parameter>'


@pytest.mark.parametrize("outer", ["calls", "function_calls"])
@pytest.mark.parametrize("bars", ["｜", "|"])
@pytest.mark.parametrize("space", ["", " ", "  "])
def test_dsml_calls_and_direct_json_are_parsed_and_hidden(outer, bars, space):
    text = "Checking. " + dsml(json.dumps(FOLDER_ARGS), outer=outer, bars=bars, space=space)
    visible, calls = answer_guard.extract_calls(text)
    assert visible == "Checking."
    assert calls == [{"tool": "list_folder", "args": FOLDER_ARGS}]
    guard = answer_guard.StreamGuard()
    output = "".join(guard.feed(char) for char in text) + guard.finish()
    assert output.strip() == "Checking."


def test_dsml_argument_holder_is_unwrapped_and_explicit_strings_preserved():
    text = dsml(FOLDER_BODY)
    assert answer_guard.extract_calls(text)[1] == [{"tool": "list_folder", "args": FOLDER_ARGS}]
    typed = dsml('<｜DSML｜ parameter name="query" string="true">001</｜DSML｜ parameter>'
                 '<｜DSML｜ parameter name="limit" string="false">3</｜DSML｜ parameter>', name="web_search")
    assert answer_guard.extract_calls(typed)[1] == [{"tool": "web_search", "args": {"query": "001", "limit": 3}}]


@pytest.mark.parametrize("raw,expected", [
    ('{"path":"a,}",}', {"path": "a,}"}),
    (r'{"path":"C:\Windows\Users"}', {"path": r"C:\Windows\Users"}),
    (json.dumps(FOLDER_ARGS), FOLDER_ARGS),
])
def test_dsml_json_repairs_preserve_literal_content_in_python_and_javascript(raw, expected):
    text = dsml(raw)
    assert answer_guard.extract_calls(text)[1] == [{"tool": "list_folder", "args": expected}]
    script = "const g=require('./app/pgstatic/answer-guard.js');const fs=require('fs');process.stdout.write(JSON.stringify(g.extractCalls(fs.readFileSync(0,'utf8'))));"
    result = subprocess.run(["node", "-e", script], input=text, text=True, encoding="utf-8", cwd=ROOT,
                            capture_output=True, check=True)
    assert json.loads(result.stdout)["calls"] == [{"tool": "list_folder", "args": expected}]


@pytest.mark.parametrize("body", ["read_file('a.txt')", "read_file(**{'path':'a.txt'})", "read_file(path='a',path='b')"])
def test_memufiles_does_not_silently_discard_python_call_arguments(body):
    assert files.parse_tool("<|tool_call_start|>[" + body + "]<|tool_call_end|>")["name"] == "_bad_json"


@pytest.mark.parametrize("body", [FOLDER_BODY, json.dumps(FOLDER_ARGS)])
def test_memufiles_runs_dsml_read_tool_without_showing_internal_markup(body, tmp_path):
    text = "Checking. " + dsml(body)
    call = files.parse_tool(text)
    assert call["name"] == "list_folder" and call["args"] == FOLDER_ARGS
    assert call["start"] == len("Checking. ")
    hold = files.HoldBack()
    assert ("".join(hold.feed(char) for char in text) + hold.finish()).strip() == "Checking."


def test_memufiles_incomplete_tool_is_not_executed():
    assert files.parse_tool(dsml(FOLDER_BODY).split('</｜DSML｜ invoke>')[0]) is None


def test_memufiles_uses_earliest_marker_regardless_of_protocol_or_unicode_prefix():
    prefix = "İ Checking. "
    text = prefix + dsml(FOLDER_BODY) + '<tool>{"name":"read_file","args":{"path":"later.txt"}}</tool>'
    hold = files.HoldBack()
    assert hold.feed(text) + hold.finish() == prefix
    parsed = files.parse_tool(text)
    assert parsed["name"] == "list_folder" and parsed["args"] == FOLDER_ARGS
    assert parsed["start"] == len(prefix)


def test_memufiles_native_tool_protocol_is_case_insensitive():
    parsed = files.parse_tool('<TOOL>{"name":"LIST_FOLDER","args":{"path":"."}}</TOOL>')
    assert parsed["name"] == "list_folder"


@pytest.mark.parametrize("bars", ["|", "｜"])
def test_memufiles_dsml_whitespace_does_not_leak_while_streaming(bars):
    text = "Checking. " + dsml(json.dumps(FOLDER_ARGS), bars=bars).replace(f"<{bars}DSML{bars}", f"<{bars} DSML {bars}")
    hold = files.HoldBack()
    assert ("".join(hold.feed(char) for char in text) + hold.finish()).strip() == "Checking."
    assert files.parse_tool(text)["args"] == FOLDER_ARGS


def test_memufiles_dsml_agent_round_executes_only_read_tools(tmp_path, monkeypatch):
    (tmp_path / "hello.txt").write_text("hello", encoding="utf-8")
    responses = ["Checking. " + dsml(json.dumps({"path": str(tmp_path)})), "One file."]
    events = []
    files.run_chat(str(tmp_path), [{"role": "user", "content": "List this folder"}], "en",
                   lambda messages: iter(responses.pop(0)), lambda event: events.append(event) or True,
                   files.PlanStore(), threading.Event())
    assert [event["tool"] for event in events if event["t"] == "step"] == ["list_folder"]
    assert events[-1] == {"t": "final", "text": "One file."}
    assert all("DSML" not in event.get("text", "") for event in events)


def test_cli_dsml_calls_are_extracted_and_hidden():
    text = "Checking. " + dsml(FOLDER_BODY)
    parsed = protocol.parse(text, finished=True)
    assert [(call.name, call.args) for call in parsed.calls] == [("list_folder", FOLDER_ARGS)]
    assert parsed.text == "Checking."
    hold = protocol.StreamHider()
    assert ("".join(hold.feed(char) for char in text) + hold.flush()).strip() == "Checking."


@pytest.mark.parametrize("syntax", ["calls", "function_calls", "tool_call", "invoke", "qwen"])
def test_cli_tool_markup_inside_raw_file_content_is_not_an_executable_call(syntax):
    if syntax in ("calls", "function_calls"):
        literal = dsml('<｜DSML｜ parameter name="path" string="true">important.txt</｜DSML｜ parameter>',
                       name="Delete", outer=syntax)
    else:
        literal = {
            "tool_call": '<tool_call>{"name":"Delete","args":{"path":"important.txt"}}</tool_call>',
            "invoke": '<invoke name="Delete"><parameter name="path">important.txt</parameter></invoke>',
            "qwen": '<function=Delete><parameter=path>important.txt</parameter></function>',
        }[syntax]
    write = [{"tool": "Write", "args": {"file_path": "example.txt", "content": "@content:1"}}]
    source = ("```memuai-tool\n" + json.dumps(write) + "\n```\n"
              '<memuai-content id="1">\n' + literal + '\n</memuai-content>')
    parsed = protocol.parse(source, finished=True)
    assert [(call.name, call.args) for call in parsed.calls] == [
        ("Write", {"file_path": "example.txt", "content": literal + "\n"})]
    assert parsed.errors == []
    with_live_call = protocol.parse(source + "\n" + dsml(FOLDER_BODY), finished=True)
    assert [(call.name, call.args) for call in with_live_call.calls] == [
        ("Write", {"file_path": "example.txt", "content": literal + "\n"}), ("list_folder", FOLDER_ARGS)]


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 31])
@pytest.mark.parametrize("closer", [
    '</｜ DSML ｜ function_calls >',
    '</|' + ' ' * 70 + 'DSML' + '\t' * 45 + '|' + ' ' * 90 + 'function_calls' + ' ' * 55 + '>',
])
def test_cli_dsml_whitespace_closer_never_swallows_following_prose(closer, chunk_size):
    bars = '|' if closer.startswith('</|') else '｜'
    text = ('Before ' + f'<{bars}DSML{bars} function_calls>'
            + f'<{bars}DSML{bars} invoke name="Read">{{"path":"x"}}</{bars}DSML{bars} invoke>'
            + closer + ' After')
    hold = protocol.StreamHider()
    visible = ''.join(hold.feed(text[index:index + chunk_size]) for index in range(0, len(text), chunk_size)) + hold.flush()
    assert visible == 'Before  After'


def test_javascript_guard_and_work_command_use_existing_approval_card():
    command = {"command": "Get-ChildItem 'C:\\Windows.old\\Users'", "shell": "powershell", "reason": "List folders"}
    payload = {"folder": "Checking. " + dsml(FOLDER_BODY),
               "command": dsml(json.dumps(command) + '</｜DSML｜ parameter>', name="memuai-command"),
               "native": "```memuai-command\n" + json.dumps(command) + "\n```"}
    script = r'''
const fs = require('fs'), vm = require('vm');
const guard = require('./app/pgstatic/answer-guard.js');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
let raw = '';
for (const char of input.folder) {
  raw += char;
  const visible = guard.clean(raw, {partial: true});
  if (visible.includes('DSML') || visible.includes('list_folder')) throw new Error('streamed tool markup');
}
const source = fs.readFileSync('./app/pgstatic/app.js', 'utf8');
const start = source.indexOf('function extractWorkProtocols(');
const end = source.indexOf('\nfunction ', start + 1);
const scope = {MemuAnswerGuard: guard, assistantSources: new WeakMap(), captureChatSource: () => ({}),
  isProjectMode: mode => mode === 'work', parseToolBlocks: text => ({text, calls: [], errors: []}),
  bareToolCalls: () => [], extractForeignSearchCalls: text => text, TOOL_ROUND_LIMIT: 16, PG_LANG: 'en'};
vm.createContext(scope); vm.runInContext(source.slice(start, end), scope);
const assistant = {}, context = {mode: 'work', workspaces: {active_id: 'project'}};
const visible = scope.extractWorkProtocols(input.command, assistant, context);
const noTools = {_noMoreTools: true}; scope.extractWorkProtocols(input.command, noTools, context);
const nativeNoTools = {_noMoreTools: true}; scope.extractWorkProtocols(input.native, nativeNoTools, context);
const ordinary = {}; scope.extractWorkProtocols(input.command, ordinary, {mode: 'chat', workspaces: {}});
process.stdout.write(JSON.stringify({folder: guard.extractCalls(input.folder), visible, assistant, noTools, nativeNoTools, ordinary}));
'''
    out = subprocess.run(["node", "-e", script], input=json.dumps(payload), cwd=ROOT,
                         text=True, encoding="utf-8", capture_output=True, check=True)
    result = json.loads(out.stdout)
    assert result["folder"] == {"text": "Checking.", "calls": [{"tool": "list_folder", "args": FOLDER_ARGS}]}
    assert result["visible"] == ""
    assert result["assistant"]["workCommand"]["command"] == command["command"]
    assert result["assistant"]["workCommand"]["status"] == "pending"
    assert "toolCalls" not in result["assistant"]
    assert "workCommand" not in result["noTools"]
    assert "workCommand" not in result["nativeNoTools"]
    assert "workCommand" not in result["ordinary"]


@pytest.mark.parametrize("raw,expected", [
    ('{"tool":"write_file","args":{"path":"x.txt","content":"a,}",},}', "a,}"),
    ('[{"tool":"write_file","args":{"path":"x.txt","content":"a,]\\\"",},}]', 'a,]"'),
])
def test_work_native_trailing_comma_repair_preserves_file_content(raw, expected):
    script = r'''
const fs = require('fs'), vm = require('vm');
const source = fs.readFileSync('./app/pgstatic/app.js', 'utf8');
const scope = {}; vm.createContext(scope);
for (const name of ['parseToolBlocks', 'rebalanceJsonClosers']) {
  const start = source.indexOf('function ' + name + '(');
  const end = source.indexOf('\nfunction ', start + 1);
  vm.runInContext(source.slice(start, end), scope);
}
const raw = fs.readFileSync(0, 'utf8');
process.stdout.write(JSON.stringify(scope.parseToolBlocks('```memuai-tool\n' + raw + '\n```')));
'''
    result = subprocess.run(["node", "-e", script], input=raw, cwd=ROOT,
                            text=True, encoding="utf-8", capture_output=True, check=True)
    parsed = json.loads(result.stdout)
    assert parsed == {"text": "", "calls": [{"tool": "write_file", "args": {
        "path": "x.txt", "content": expected}}], "errors": []}
