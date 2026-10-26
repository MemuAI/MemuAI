"""Skill selection eval (Round 29, r29c-prompts): does the chat model load a skill playbook exactly when it helps?

    set -a; . /workspace/work/secrets/test_keys.env; set +a
    python3 tests/prompt_eval/skills_eval.py --trees old=<copy-root>,new=. --models flash,oss,qwen35 --runs 2

A tree root must contain app/pgstatic/app.js and app/pgstatic/skills.js. The catalogue + <skills_protocol> are built
exactly like buildSystemPrompt() does (every built-in skill enabled), on top of the real DEFAULT_PROMPT of the
working tree. A reply "loads" a skill when it contains a ```memuai-skill block. Not part of the normal test suite.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
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

JS = r"""
const fs = require("fs"), path = require("path");
const root = path.resolve(process.argv[process.argv.length - 1]);
global.window = {};
require(path.join(root, "app/pgstatic/skills.js"));
const app = fs.readFileSync(path.join(root, "app/pgstatic/app.js"), "utf8");
const start = app.indexOf("const activeSkills = ");
const end = app.indexOf("} catch (e) {}", start);
const body = app.slice(start, end);
const getSkills = () => window.MEMUAI_SKILL_LIBRARY.skills.map((s) => ({ ...s, enabled: true }));
let base = "";
eval(body);
process.stdout.write(JSON.stringify({ block: base }));
"""

SKILL_BLOCK = re.compile(r"```[ \t]*memuai-skill[^\n]*\n([\s\S]*?)(?:\n[ \t]*```|$)", re.I)

CASES = [
    ("load-review", "Сделай ревью этого диффа перед мержем:\n```diff\n- def total(items):\n-     return sum(i.price for i in items)\n+ def total(items, discount=None):\n+     t = sum(i.price * i.qty for i in items)\n+     if discount: t = t - t * discount / 100\n+     return round(t)\n```", ("code_review",)),
    ("load-debug", "У меня периодически падает сервис на FastAPI с ошибкой 'RuntimeError: Event loop is closed' только в проде под gunicorn, локально всё ок. Помоги найти причину и починить.", ("debugging",)),
    ("load-contract", "Вот договор аренды квартиры, который мне прислал арендодатель (пункты ниже). Проверь его на риски для меня как арендатора:\n1. Арендодатель вправе расторгнуть договор в любой момент без уведомления.\n2. Залог не возвращается при досрочном выезде.\n3. Все ремонты за счёт арендатора.", ("legal_review",)),
    ("load-slides", "Сделай структуру презентации на 12 слайдов для питча моего стартапа инвесторам: сервис доставки домашней еды от соседей.", ("slides", "business_analysis")),
    ("load-plan", "Нужно за 3 месяца перевезти нашу CRM с самописного PHP на Django. Команда 4 человека. Составь план проекта с этапами, оценками и рисками.", ("project_planning", "software_engineering")),
    ("load-data", "Вот выгрузка продаж по месяцам за 2 года (CSV ниже). Найди сезонность, аномалии и сделай прогноз на следующий квартал.\nmonth,revenue\n2024-01,120\n2024-02,118\n2024-03,140\n2024-04,150\n2024-05,170\n2024-06,90\n2024-07,180", ("data_analysis",)),
    ("skip-hello", "Привет! Как дела?", ()),
    ("skip-palindrome", "Напиши функцию на Python, которая проверяет, является ли строка палиндромом.", ()),
    ("skip-translate", "Переведи на английский: «Встреча переносится на четверг».", ()),
    ("skip-git", "Чем git merge отличается от git rebase? Коротко.", ()),
    ("skip-email", "Напиши короткое сообщение коллеге, что я опоздаю на 10 минут.", ()),
    ("skip-fact", "Сколько будет 15% от 1200?", ()),
    ("skip-explain", "Что такое замыкание в JavaScript?", ()),
    ("skip-regex", "Регулярка для проверки email?", ()),
]

def skill_block(tree: Path) -> str:
    out = subprocess.run(["node", "-e", JS, str(tree)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["block"]

def loaded(reply: str):
    ids = []
    for raw in SKILL_BLOCK.findall(scenarios.THINK.sub("", reply or "")):
        try:
            data = json.loads(raw.strip())
            ids += [str(x) for x in (data.get("skills") if isinstance(data, dict) else data) or []]
        except Exception:
            ids.append("_invalid")
    return ids

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trees", default=f"new={ROOT}")
    parser.add_argument("--models", default="flash")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    snap = json.loads(subprocess.run([sys.executable, str(HERE / "snapshot.py"), str(ROOT)], capture_output=True,
                                     text=True, check=True).stdout)
    trees = dict(item.split("=", 1) for item in args.trees.split(","))
    blocks = {name: skill_block(Path(path).resolve()) for name, path in trees.items()}
    jobs = [(c, v, m, n) for c in CASES for v in trees for m in args.models.split(",") for n in range(args.runs)]

    def one(job):
        (cid, text, expect), variant, model, n = job
        system = scenarios.chat_system(snap, web=False) + "\n\n" + blocks[variant]
        reply = llm.chat(model, [{"role": "system", "content": system}, {"role": "user", "content": text}])
        if reply.get("error"):
            return {"id": cid, "variant": variant, "model": model, "ok": None, "detail": reply["error"]}
        ids = loaded(reply["content"])
        ok = (bool(ids) and any(i in expect for i in ids)) if expect else not ids
        return {"id": cid, "variant": variant, "model": model, "run": n, "ok": ok, "loaded": ids,
                "kind": "load" if expect else "skip", "detail": f"loaded={ids}"}

    results = []
    with cf.ThreadPoolExecutor(args.workers) as pool:
        for row in pool.map(one, jobs):
            results.append(row)
            print({True: "PASS", False: "FAIL", None: "ERR "}[row["ok"]], row["variant"], row["model"], row["id"],
                  row["detail"], flush=True)
    table = defaultdict(lambda: [0, 0])
    for r in results:
        if r["ok"] is not None:
            key = (r.get("kind", "?"), r["model"], r["variant"])
            table[key][0] += bool(r["ok"])
            table[key][1] += 1
    for key in sorted(table):
        print(f"{key[0]:5} {key[1]:7} {key[2]:4} {table[key][0]}/{table[key][1]}")
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    path = out / time.strftime("skills-%Y%m%d-%H%M%S.json")
    path.write_text(json.dumps(results, ensure_ascii=False, indent=1), "utf-8")
    print("saved", path, "| catalogue tokens:", {k: len(v) // 4 for k, v in blocks.items()})

if __name__ == "__main__":
    main()
