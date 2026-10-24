"""Regenerate the shipped «Кратко от ИИ» / «AI summary» cards: app/docs/summaries/{ru,en}.json.

Developer tool, not a test.  Only pages whose summary is missing or stale (the
article's content hash changed) are written, so it is cheap to re-run after
editing a few articles:

    set -a; . /path/to/keys.env; set +a           # DOCS_AI_BASE/KEY/MODEL or XKIRO_BASE/KEY/MODEL
    python3 tests/docs_summaries.py --check        # list what is missing / stale (no model calls)
    python3 tests/docs_summaries.py                # (re)generate those, a few in parallel
    python3 tests/docs_summaries.py --only sync --lang en --force

``--budget N`` stops starting new requests after N seconds (results are saved
after every page, so a run can be split into several shorter ones).  Keys are
read from the environment only and never written anywhere.  Review the result
(``--show``) against the articles before shipping: the summaries are shown to
users as part of the documentation.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import docs, docs_ai  # noqa: E402


def page_ids() -> list[str]:
    index = json.loads((ROOT / "app" / "docs" / "index.json").read_text(encoding="utf-8"))
    return [page for group in index["groups"] for page in group["pages"]]


def endpoint() -> tuple[str, str, str]:
    base = os.environ.get("DOCS_AI_BASE") or os.environ.get("XKIRO_BASE") or ""
    key = os.environ.get("DOCS_AI_KEY") or os.environ.get("XKIRO_KEY") or ""
    model = os.environ.get("DOCS_AI_MODEL") or os.environ.get("XKIRO_MODEL") or ""
    if not (base and key and model):
        sys.exit("Set DOCS_AI_BASE/DOCS_AI_KEY/DOCS_AI_MODEL (or XKIRO_*) in the environment")
    return base.rstrip("/"), key, model


def public_model(model: str) -> str:
    """The name written into the shipped JSON: a Yandex URI "gpt://<folder>/<name>/latest" keeps only <name>."""
    return model.rstrip("/").split("/")[-2] if model.startswith("gpt://") and model.count("/") >= 4 else model


def call(base: str, key: str, model: str, messages: list[dict], temperature: float = 0.2) -> str:
    body = json.dumps({"model": model, "messages": messages, "temperature": temperature, "max_tokens": 3000}).encode()
    request = urllib.request.Request(base + "/chat/completions", data=body, method="POST",
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                              "User-Agent": "MemuAI-docs-summaries/1.0"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # local / direct endpoint
    last = None
    for attempt in range(6):
        try:
            with opener.open(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
            return str(data["choices"][0]["message"]["content"] or "")
        except urllib.error.HTTPError as exc:   # 429: wait as long as the server asks
            last = exc
            wait = 2 + attempt * 3
            if exc.code == 429:
                match = re.search(r"after (\d+)", exc.read().decode("utf-8", "replace"))
                wait = int(match.group(1)) + 1 if match else 8
            time.sleep(min(wait, 30))
        except Exception as exc:  # retry transient failures
            last = exc
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"model call failed: {type(last).__name__}")


def todo(langs: list[str], only: list[str], force: bool) -> list[tuple[str, str]]:
    rows = []
    for lang in langs:
        data = docs_ai.load(lang)
        for page_id in page_ids():
            if only and page_id not in only:
                continue
            page = docs.page(lang, page_id)
            entry = (data.get("pages") or {}).get(page_id)
            fresh = entry and entry.get("hash") == docs_ai.content_hash(page["title"], page["body"])
            if force or not fresh:
                rows.append((lang, page_id))
    return rows


def verify(langs: list[str], only: list[str], workers: int, budget: float, report: str | None = None) -> None:
    """A second model pass: claims of a summary that its article does not support (for a human to review)."""
    base, key, model = endpoint()
    started = time.time()
    rows = [(lang, page_id) for lang in langs for page_id in sorted(docs_ai.load(lang).get("pages") or {})
            if not only or page_id in only]

    def job(lang: str, page_id: str):
        if budget and time.time() - started > budget:
            return lang, page_id, "skipped"
        page = docs.page(lang, page_id)
        entry = docs_ai.load(lang)["pages"][page_id]
        summary = entry["tldr"] + "\n" + "\n".join("- " + point for point in entry["points"])
        prompt = ("Compare the SUMMARY with the ARTICLE. List every statement of the summary that the article does not "
                  "support or that contradicts it (wrong menu path, wrong shortcut, wrong number, invented feature). "
                  "Ignore style and paraphrase. Answer JSON only: {\"issues\": [\"...\"]} (empty list when all is correct)."
                  f"\n\nARTICLE:\n{docs_ai.normalized_text(page['title'], page['body'])}\n\nSUMMARY:\n{summary}")
        try:
            answer = call(base, key, model, [{"role": "user", "content": prompt}], temperature=0)
        except RuntimeError:
            return lang, page_id, "skipped"
        try:
            issues = json.loads(answer[answer.index("{"):answer.rindex("}") + 1]).get("issues") or []
        except ValueError:
            issues = ["(unparsable verifier answer)"]
        return lang, page_id, issues

    found: dict[str, list] = {}
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for future in cf.as_completed([pool.submit(job, lang, page_id) for lang, page_id in rows]):
            lang, page_id, issues = future.result()
            if issues:
                print(f"{lang}/{page_id}: {issues}")
                if issues != "skipped":
                    found[f"{lang}/{page_id}"] = issues
    if report:
        Path(report).write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")


def repair(report: Path, workers: int, budget: float) -> None:
    """Rewrite the summaries a reviewer (``--verify`` or a human) found doubtful, with the problems as feedback."""
    base, key, model = endpoint()
    started = time.time()
    found = json.loads(report.read_text(encoding="utf-8"))

    def job(ref: str, issues: list):
        if budget and time.time() - started > budget:
            return ref, None
        lang, page_id = ref.split("/", 1)
        page = docs.page(lang, page_id)
        old = docs_ai.load(lang)["pages"].get(page_id) or {}
        messages = docs_ai.summary_messages(lang, page["title"], page["body"])
        messages.append({"role": "assistant", "content": json.dumps({"tldr": old.get("tldr", ""), "points": old.get("points", [])},
                                                                    ensure_ascii=False)})
        messages.append({"role": "user", "content": "A reviewer compared this summary with the article and found problems:\n- "
                         + "\n- ".join(str(issue) for issue in issues)
                         + "\nWrite the summary again, fixing them, strictly by the article. Same JSON format."})
        for attempt in range(3):
            try:
                parsed = docs_ai.parse_summary(call(base, key, model, messages, temperature=0.2 + attempt * 0.3))
            except (ValueError, RuntimeError):
                continue
            if not docs_ai.literal_problems(parsed, page["title"], page["body"]) and len(parsed["points"]) >= 2:
                return ref, {"hash": docs_ai.content_hash(page["title"], page["body"]),
                             "generated": dt.date.today().isoformat(), "model": public_model(model), **parsed}
        return ref, None

    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for future in cf.as_completed([pool.submit(job, ref, issues) for ref, issues in found.items()]):
            ref, entry = future.result()
            if not entry:
                print(f"{ref}: not repaired")
                continue
            lang, page_id = ref.split("/", 1)
            data = docs_ai.load(lang)
            data = {**data, "pages": dict(data.get("pages") or {})}
            data["pages"][page_id] = entry
            docs_ai.save(lang, data)
            print(f"{ref}: repaired")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lang", choices=["ru", "en"], action="append")
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check", action="store_true", help="only list missing / stale summaries")
    parser.add_argument("--show", action="store_true", help="print the summaries for review")
    parser.add_argument("--verify", action="store_true",
                        help="ask the model to check every summary against its article and print doubtful claims")
    parser.add_argument("--report", help="with --verify: also write the doubtful claims to this JSON file")
    parser.add_argument("--repair", help="regenerate the pages of a --report file, telling the model what was wrong")
    parser.add_argument("--budget", type=float, default=0, help="stop starting new requests after N seconds")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    langs = args.lang or ["ru", "en"]
    if args.show:
        for lang in langs:
            for page_id, entry in sorted((docs_ai.load(lang).get("pages") or {}).items()):
                if args.only and page_id not in args.only:
                    continue
                print(f"\n[{lang}/{page_id}] {entry.get('tldr')}")
                for point in entry.get("points") or []:
                    print("   •", point)
        return
    if args.verify:
        verify(langs, args.only, args.workers, args.budget, args.report)
        return
    if args.repair:
        repair(Path(args.repair), args.workers, args.budget)
        return
    rows = todo(langs, args.only, args.force)
    if args.check:
        for lang, page_id in rows:
            print(f"{lang}/{page_id}")
        print(f"{len(rows)} summaries to (re)generate")
        sys.exit(1 if rows else 0)
    if not rows:
        print("all summaries are fresh")
        return
    base, key, model = endpoint()
    started = time.time()
    done = 0

    def job(lang: str, page_id: str):
        if args.budget and time.time() - started > args.budget:
            return lang, page_id, None, "skipped (budget)"
        page = docs.page(lang, page_id)
        for attempt in range(3):
            try:
                parsed = docs_ai.parse_summary(call(base, key, model, docs_ai.summary_messages(lang, page["title"], page["body"]),
                                                    temperature=0.2 + attempt * 0.3))
            except (ValueError, RuntimeError):
                continue
            if docs_ai.literal_problems(parsed, page["title"], page["body"]):
                continue
            if len(parsed["points"]) >= 2:
                return lang, page_id, {"hash": docs_ai.content_hash(page["title"], page["body"]),
                                       "generated": dt.date.today().isoformat(), "model": public_model(model), **parsed}, ""
        return lang, page_id, None, "unparsable answer"

    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(job, lang, page_id) for lang, page_id in rows]
        for future in cf.as_completed(futures):
            try:
                lang, page_id, entry, problem = future.result()
            except Exception as exc:
                print("error:", type(exc).__name__, str(exc)[:200])
                continue
            if entry is None:
                print(f"{lang}/{page_id}: {problem}")
                continue
            data = docs_ai.load(lang)
            data = {**data, "pages": dict(data.get("pages") or {}), "model": data.get("model") or public_model(model),
                    "note": "Generated by tests/docs_summaries.py and reviewed against the articles."}
            data["pages"][page_id] = entry
            docs_ai.save(lang, data)
            done += 1
            print(f"{lang}/{page_id}: ok")
    print(f"written: {done}, left: {len(todo(langs, args.only, False))}")


if __name__ == "__main__":
    main()
