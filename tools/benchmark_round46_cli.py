"""Paired offline Read/Grep replay against the saved Round 45 implementation.

Reports wall time and actual serialized context size; token counts are MemuAI
estimates, not provider billing. Uses generated temporary files, no LLM/API.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.cli.tools import files  # noqa: E402
from app.cli.tools.base import Prepared, ToolCall, ToolContext  # noqa: E402
from app.cli import convert, tokens  # noqa: E402

def context(root):
    session = SimpleNamespace(read_cache={}, read_state={}, context_epoch=0)
    runtime = SimpleNamespace(root=root, add_dirs=[], session=session,
                              files=SimpleNamespace(files=lambda **kwargs: ["dense.py"]))
    return ToolContext(runtime, ToolCall("Read", {}))

def timed(function, repetitions=7):
    samples = []
    for _ in range(repetitions):
        start = time.perf_counter()
        function()
        samples.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(samples), 3)

def main():
    baseline = ROOT / "build/round46-baseline/cli-files-round45.py"
    spec = importlib.util.spec_from_file_location("app.cli.tools._round45_files", baseline)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    result = {"baseline_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
              "repetitions": 7, "timing": "median wall milliseconds, paired offline fixtures",
              "tokens": "MemuAI estimator; no provider billing or generated-model quality measurement"}
    with tempfile.TemporaryDirectory(prefix="round46-cli-bench-", dir=ROOT / "build") as temporary:
        root = Path(temporary)
        dense = root / "dense.py"
        dense.write_text("".join(f"target_{i:06} = process_record({i}, source='fixture', enabled=True)\n" for i in range(40_000)), "utf-8")
        modules = (("before", old), ("after", files))
        for label, module in modules:
            def pagination():
                ctx = context(root)
                read = module.ReadTool()
                for index in range(20):
                    read._read_text(ctx, dense, {"offset": 1 + index * 300, "limit": 100})
            prepared = Prepared(data={"base": root, "prefix": "", "pattern": "^target_"})
            def grep():
                output = module.GrepTool()._search(context(root), {}, prepared)
                assert output.output == "dense.py"
            read = module.ReadTool()._read_text(context(root), dense, {})
            messages = [{"role": "user", "kind": "prompt", "content": "Locate the relevant implementation."},
                        {"role": "assistant", "tool_calls": [{"id": "r1", "name": "Read", "args": {"file_path": "dense.py"}}]},
                        {"role": "tool", "tool_call_id": "r1", "name": "Read", "content": read.output}]
            wire = convert.to_native(messages)
            result[label] = {"pagination_20_ms": timed(pagination), "grep_paths_ms": timed(grep),
                             "default_read_lines": read.meta["lines"], "default_read_characters": len(read.output),
                             "native_wire_bytes": len(json.dumps(wire, ensure_ascii=False).encode("utf-8")),
                             "native_estimated_tokens": tokens.estimate_messages(wire)}
        before, after = result["before"], result["after"]
        result["improvement"] = {
            "pagination_speedup": round(before["pagination_20_ms"] / after["pagination_20_ms"], 2),
            "grep_speedup": round(before["grep_paths_ms"] / after["grep_paths_ms"], 2),
            "wire_reduction_percent": round(100 * (1 - after["native_wire_bytes"] / before["native_wire_bytes"]), 2),
            "estimated_token_reduction_percent": round(100 * (1 - after["native_estimated_tokens"] / before["native_estimated_tokens"]), 2)}
    (ROOT / "build/round46-cli-benchmark.json").write_text(json.dumps(result, indent=2), "utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
