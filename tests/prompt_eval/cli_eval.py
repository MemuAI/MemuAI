"""MemuCode CLI eval: the REAL CLI (memucode.py -p --output-format stream-json) against sample repos.

    set -a; . /workspace/work/secrets/test_keys.env; set +a
    python3 tests/prompt_eval/cli_eval.py --trees base=/tmp/memu_base,new=. --models flash,oss --runs 2

A tree is a copy of the MemuAI sources (base = a copy taken before the prompt pass); the CLI of that tree runs with
an isolated MEMUAI_DATA_DIR/MEMUCODE_CONFIG_DIR and a direct OpenAI-compatible connection to the test endpoint
(settings.json is written into a temp dir with mode 600 and deleted afterwards; keys are never printed).
Metrics: solved (checker), tool calls, identical repeated calls, tool errors, seconds, input tokens.
Not part of the normal test suite.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import llm  # noqa: E402
import work_eval as we  # noqa: E402

GIT = ["git", "-c", "user.email=eval@example.com", "-c", "user.name=eval"]


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(GIT + ["add", "-A"], cwd=root, check=True)
    subprocess.run(GIT + ["commit", "-qm", "init"], cwd=root, check=True)


def check_answer(pattern: str, *, min_tools: int = 0, max_tools: int = 99):
    def check(root: Path, result: Dict[str, Any]) -> Tuple[bool, str]:
        text = result["final"]
        found = bool(re.search(pattern, text, re.I))
        n = result["tool_calls"]
        ok = found and min_tools <= n <= max_tools
        return ok, f"answer_ok={found} tools={n}"
    return check


def check_loop(root: Path, result: Dict[str, Any]) -> Tuple[bool, str]:
    told = bool(re.search(r"(?i)npm|package\.json|node|не (?:найден|установлен)|not (?:found|installed)|нет", result["final"]))
    ok = told and result["tool_calls"] <= 6
    return ok, f"tools={result['tool_calls']} repeats={result['repeats']} told={told}"


def wrap(check):
    return lambda root, result: check(root)


CLI_TASKS: Dict[str, Dict[str, Any]] = {
    "c-shop-tests": {"setup": we.project_shop, "check": wrap(we.check_shop),
                     "prompt": "Тесты падают. Почини код, чтобы все тесты проходили (python -m pytest -q). Тесты не трогай."},
    "c-docstrings": {"setup": we.project_docstrings, "check": wrap(we.check_docstrings),
                     "prompt": "Добавь короткие однострочные docstring ко всем функциям во всех файлах src/. Логику не меняй."},
    "c-knowledge": {"setup": we.project_docstrings, "check": check_answer(r"tuple|кортеж", max_tools=0),
                    "prompt": "Чем в Python list отличается от tuple? Коротко."},
    "c-explore": {"setup": we.project_config, "check": check_answer(r"settings\.yml", min_tools=1),
                  "prompt": "Где в этом проекте задаётся порт сервера и какой он?"},
    "c-npm-loop": {"setup": we.project_docstrings, "check": check_loop,
                   "prompt": "Запусти npm test и скажи, проходят ли тесты."},
}


def write_settings(cfg: Path, model: str) -> str:
    prefix, model_id = llm._endpoint(llm.MODELS[model][0])
    settings = {"backend": "direct", "connections": [{"name": "ev", "format": "openai", "base_url":
                os.environ[prefix + "_BASE"], "api_key": os.environ[prefix + "_KEY"], "models": [model_id]}]}
    path = cfg / "settings.json"
    path.write_text(json.dumps(settings))
    path.chmod(0o600)
    return "@ev:" + model_id


def episode(task_id: str, tree: Path, model: str) -> Dict[str, Any]:
    task = CLI_TASKS[task_id]
    work = Path(tempfile.mkdtemp(prefix=f"pe-cli-{task_id}-"))
    repo, cfg, data = work / "repo", work / "cfg", work / "data"
    for p in (repo, cfg, data):
        p.mkdir()
    try:
        task["setup"](repo)
        init_repo(repo)
        model_ref = write_settings(cfg, model)
        env = dict(os.environ, MEMUAI_DATA_DIR=str(data), MEMUCODE_CONFIG_DIR=str(cfg), PYTHONPATH=str(tree),
                   NO_PROXY="127.0.0.1,localhost")
        started = time.time()
        try:
            done = subprocess.run([sys.executable, str(tree / "memucode.py"), "-p", "-m", model_ref,
                                   "--dangerously-skip-permissions", "--output-format", "stream-json", "--max-turns", "30",
                                   task["prompt"]], cwd=repo, env=env, capture_output=True, text=True, timeout=900)
            out = done.stdout
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        seconds = time.time() - started
        calls: List[str] = []
        errors = 0
        final = ""
        usage: Dict[str, Any] = {}
        for line in out.splitlines():
            try:
                event = json.loads(line)
            except Exception:
                continue
            if event.get("type") == "tool_use":
                calls.append(json.dumps([event.get("name"), event.get("input")], sort_keys=True, ensure_ascii=False))
            elif event.get("type") == "tool_result" and not event.get("ok"):
                errors += 1
            elif event.get("type") == "result":
                final = str(event.get("result") or "")
                usage = event.get("usage") or {}
        repeats = len(calls) - len(set(calls))
        result = {"task": task_id, "model": model, "tool_calls": len(calls), "repeats": repeats, "tool_errors": errors,
                  "seconds": round(seconds, 1), "input_tokens": usage.get("input_tokens"), "final": final}
        ok, detail = task["check"](repo, result)
        result.update(ok=ok, detail=detail, final=final[:800])
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trees", default=f"base=/tmp/memu_base,new={ROOT}")
    parser.add_argument("--models", default="flash")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--only", default="")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    trees = dict(item.split("=", 1) for item in args.trees.split(","))
    tasks = [t for t in CLI_TASKS if not args.only or any(t.startswith(p) for p in args.only.split(","))]
    jobs = [(t, v, m, n) for t in tasks for v in trees for m in args.models.split(",") for n in range(args.runs)]
    results = []

    def one(job):
        t, v, m, n = job
        row = episode(t, Path(trees[v]).resolve(), m)
        row.update(variant=v, run=n)
        return row

    with cf.ThreadPoolExecutor(args.workers) as pool:
        for row in pool.map(one, jobs):
            results.append(row)
            print(("PASS" if row["ok"] else "FAIL"), row["variant"], row["model"], row["task"],
                  f"tools={row['tool_calls']} rep={row['repeats']} err={row['tool_errors']} {row['seconds']}s "
                  f"in={row['input_tokens']} :: {row['detail']}", flush=True)
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    path = out / time.strftime("cli-%Y%m%d-%H%M%S.json")
    path.write_text(json.dumps(results, ensure_ascii=False, indent=1), "utf-8")
    agg: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in results:
        agg.setdefault((row["variant"], row["model"]), []).append(row)
    for key, rows in sorted(agg.items()):
        n = len(rows)
        print(f"{key[0]:5} {key[1]:6} solved {sum(r['ok'] for r in rows)}/{n} tools≈{sum(r['tool_calls'] for r in rows)/n:.1f}"
              f" repeats={sum(r['repeats'] for r in rows)} sec≈{sum(r['seconds'] for r in rows)/n:.0f}"
              f" in≈{sum(int(r['input_tokens'] or 0) for r in rows)//n}")
    print("saved", path)


if __name__ == "__main__":
    main()
