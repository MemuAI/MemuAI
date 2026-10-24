"""A tiny stdio MCP server for tests (Round 40, r40-mcp): tools, resources, ping, stderr logs.

Tools: ``echo`` (read-only), ``add_note`` (writes — needs approval by default), ``crash`` (exits the process),
``whoami`` (returns the value of the ``ECHO_TOKEN`` env var, to prove secrets reach the server).
Resources: ``memo://readme`` and ``memo://notes`` (the notes added so far).
"""
import json
import os
import sys

NOTES = []


def reply(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    if error:
        msg["error"] = {"code": -32000, "message": error}
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


TOOLS = [
    {"name": "echo", "description": "Echo the text back", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "add_note", "description": "Add a note", "annotations": {"readOnlyHint": False, "destructiveHint": False},
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {"name": "whoami", "description": "Length of the configured token", "inputSchema": {"type": "object"}},
    {"name": "crash", "description": "Exit the process", "inputSchema": {"type": "object"}},
]


def main():
    sys.stderr.write("echo server started, token=" + os.environ.get("ECHO_TOKEN", "") + "\n")
    sys.stderr.flush()
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if rid is None:
            continue
        if method == "initialize":
            reply(rid, {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}, "resources": {}},
                        "serverInfo": {"name": "echo-test", "version": "1.0"}})
        elif method == "tools/list":
            reply(rid, {"tools": TOOLS})
        elif method == "resources/list":
            reply(rid, {"resources": [{"uri": "memo://readme", "name": "readme", "mimeType": "text/plain"},
                                      {"uri": "memo://notes", "name": "notes"}]})
        elif method == "resources/read":
            uri = params.get("uri")
            text = "Привет из ресурса MCP: склад открыт с 9 до 18." if uri == "memo://readme" else "\n".join(NOTES)
            reply(rid, {"contents": [{"uri": uri, "mimeType": "text/plain", "text": text}]})
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name == "echo":
                reply(rid, {"content": [{"type": "text", "text": "echo: " + str(args.get("text", ""))}]})
            elif name == "add_note":
                NOTES.append(str(args.get("text", "")))
                reply(rid, {"content": [{"type": "text", "text": f"saved ({len(NOTES)})"}]})
            elif name == "whoami":
                reply(rid, {"content": [{"type": "text", "text": "token length " + str(len(os.environ.get("ECHO_TOKEN", "")))}]})
            elif name == "crash":
                sys.exit(3)
            else:
                reply(rid, error="unknown tool")
        elif method == "ping":
            reply(rid, {})
        else:
            reply(rid, error="unsupported")


if __name__ == "__main__":
    main()
