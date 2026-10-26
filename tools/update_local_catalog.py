"""Refresh the local model wizard catalogue from Hugging Face (developer tool, Round 33).

    python tools/update_local_catalog.py            # refresh app/local_catalog/models.json in place
    python tools/update_local_catalog.py --check    # only report what changed, write nothing

What it updates for every entry: the GGUF file of each listed quant (with split parts and sizes),
the best image projector (F16 first) and MTP/draft file, total parameters, the trained context
length and the KV cache size per token (from the base model's config.json).

What stays curated by hand in the JSON: names, the repository choice, licence, languages,
capabilities, task scores, recommended sampling and the short "why" texts. To add a model, append
an entry with at least ``id``, ``name``, ``repo``, ``base``, ``quants``, ``caps``, ``scores`` and
``why`` and run this tool. A HF_TOKEN environment variable is used for gated repositories.
Users get the same refresh from the wizard («Обновить данные»), stored in their profile.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import local_wizard  # noqa: E402

def get_json(url: str):
    headers = {"User-Agent": "MemuAI-catalog-tool/1.0"}
    token = os.environ.get("HF_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="report changes without writing")
    parser.add_argument("--only", default="", help="comma-separated model ids to refresh")
    args = parser.parse_args()
    path = local_wizard.BUNDLED
    catalog = json.loads(path.read_text(encoding="utf-8"))
    only = {item.strip() for item in args.only.split(",") if item.strip()}
    if only:
        subset = dict(catalog, models=[row for row in catalog["models"] if row["id"] in only])
        fresh, errors = local_wizard.refresh_catalog(subset, get_json,
                                                     lambda done, total: print(f"  {done}/{total}", end="\r"))
        by_id = {row["id"]: row for row in fresh["models"]}
        fresh = dict(catalog, version=fresh["version"],
                     models=[by_id.get(row["id"], row) for row in catalog["models"]])
    else:
        fresh, errors = local_wizard.refresh_catalog(catalog, get_json,
                                                     lambda done, total: print(f"  {done}/{total}", end="\r"))
    print()
    for old, new in zip(catalog["models"], fresh["models"]):
        quants = {q: round(v["size"] / 1024 ** 3, 2) for q, v in (new.get("files") or {}).items()}
        if old.get("files") != new.get("files") or old.get("kv") != new.get("kv"):
            print(f"{new['id']:34} {quants} kv={new.get('kv')}")
    for error in errors:
        print("ERROR", error)
    if not args.check:
        path.write_text(json.dumps(fresh, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"written {path.relative_to(ROOT)} ({len(fresh['models'])} models, version {fresh['version']})")
    return 1 if errors else 0

if __name__ == "__main__":
    raise SystemExit(main())
