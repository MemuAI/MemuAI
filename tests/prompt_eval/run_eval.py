"""A/B prompt eval on real models. NOT part of the normal test suite (needs network and API keys).

    set -a; . /workspace/work/secrets/test_keys.env; set +a
    python3 tests/prompt_eval/run_eval.py --variants base,new --models qwen,flash --runs 3 [--only chat-web,quick]

base = tests/prompt_eval/baseline_snapshot.json (prompts before the Round-29 prompt pass),
new  = a fresh snapshot of the working tree. Results go to tests/prompt_eval/results/<stamp>.json and a summary
table is printed. Single-turn scenarios live in scenarios.py, multi-turn Work agent scenarios in work_eval.py.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import llm  # noqa: E402
import scenarios  # noqa: E402

def load_snapshot(variant: str) -> dict:
    if variant == "base":
        return json.loads((HERE / "baseline_snapshot.json").read_text("utf-8"))
    if variant == "new":
        out = subprocess.run([sys.executable, str(HERE / "snapshot.py"), str(ROOT)], capture_output=True, text=True,
                             check=True)
        return json.loads(out.stdout)
    return json.loads(Path(variant).read_text("utf-8"))

def run_single(rows, snaps, models, runs, workers):
    jobs = [(row, variant, model, n) for row in rows for variant in snaps for model in models for n in range(runs)]
    results = []

    def one(job):
        row, variant, model, n = job
        messages = row["build"](snaps[variant])
        reply = llm.chat(model, messages)
        if reply.get("error"):
            return {"id": row["id"], "group": row["group"], "variant": variant, "model": model, "run": n,
                    "ok": None, "detail": "ERROR " + reply["error"]}
        ok, detail = row["check"](reply["content"])
        return {"id": row["id"], "group": row["group"], "variant": variant, "model": model, "run": n, "ok": ok,
                "detail": detail, "prompt_tokens": reply["usage"].get("prompt_tokens"),
                "completion_tokens": reply["usage"].get("completion_tokens"), "seconds": round(reply["seconds"], 1),
                "reply": reply["content"][:1500]}

    with cf.ThreadPoolExecutor(workers) as pool:
        for result in pool.map(one, jobs):
            results.append(result)
            mark = {True: "PASS", False: "FAIL", None: "ERR "}[result["ok"]]
            print(f"{mark} {result['variant']:4} {result['model']:5} {result['id']:22} {result['detail'][:110]}",
                  flush=True)
    return results

def summary(results):
    table = defaultdict(lambda: [0, 0])
    for r in results:
        if r["ok"] is None:
            continue
        key = (r["group"], r["model"], r["variant"])
        table[key][0] += bool(r["ok"])
        table[key][1] += 1
    lines = []
    for key in sorted(table):
        good, total = table[key]
        lines.append(f"{key[0]:20} {key[1]:6} {key[2]:5} {good}/{total}")
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", default="base,new")
    parser.add_argument("--models", default=",".join(llm.available()))
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--only", default="", help="comma-separated group or id prefixes")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    snaps = {variant: load_snapshot(variant) for variant in args.variants.split(",")}
    rows = scenarios.all_scenarios()
    if args.only:
        prefixes = args.only.split(",")
        rows = [r for r in rows if any(r["group"].startswith(p) or r["id"].startswith(p) for p in prefixes)]
    models = [m for m in args.models.split(",") if m]
    results = run_single(rows, snaps, models, args.runs, args.workers)
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    path = out / time.strftime("%Y%m%d-%H%M%S.json")
    path.write_text(json.dumps(results, ensure_ascii=False, indent=1), "utf-8")
    print("\n" + summary(results))
    print("saved", path)

if __name__ == "__main__":
    main()
