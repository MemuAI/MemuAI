"""Snapshot of every prompt the eval harness uses, taken from a given source tree.

    python3 tests/prompt_eval/snapshot.py <tree-root>   → JSON on stdout

Run in a subprocess per tree (old = `git archive HEAD` copy, new = working tree), so the two versions never share
imported modules. Not part of the normal test suite.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def snapshot(root: Path) -> dict:
    sys.path.insert(0, str(root))
    os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="pe-data-"))
    from app import pgdata, quick_ask_core, dfy, memory_v2  # noqa: E402
    from app.cli import prompts as cli  # noqa: E402
    out = {
        "DEFAULT_PROMPT": pgdata.DEFAULT_PROMPT,
        "internal": {row["id"]: row["text"] for row in pgdata.INTERNAL_PROMPTS},
        "quick_brief": quick_ask_core.system_prompt(lang="ru", style="brief", now=0),
        "quick_brief_en": quick_ask_core.system_prompt(lang="en", style="brief", now=0),
        "dfy": dfy.SYSTEM_PROMPT,
        "memory_protocol": memory_v2.protocol_prompt("chat", doc={"rules": [], "services": {}}) if False else "",
        "cli": {name: getattr(cli, name) for name in ("IDENTITY", "STYLE", "WORKFLOW", "TOOL_RULES", "SAFETY",
                                                        "TEXT_PROTOCOL", "PLAN_REMINDER")},
        "cli_subagent_ask": cli.SUBAGENT_RULES["ask"],
    }
    try:
        doc = memory_v2.load()
        out["memory_protocol"] = memory_v2.protocol_prompt("chat", doc=doc)
    except Exception as exc:  # pragma: no cover - diagnostics only
        out["memory_protocol_error"] = repr(exc)
    js = subprocess.run(["node", str(Path(__file__).with_name("snapshot.js")), str(root), json.dumps(out["internal"])],
                        capture_output=True, text=True, check=True)
    out["js"] = json.loads(js.stdout)
    return out


if __name__ == "__main__":
    print(json.dumps(snapshot(Path(sys.argv[1]).resolve()), ensure_ascii=False))
