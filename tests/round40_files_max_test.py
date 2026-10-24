"""Round 40 (r40-files-max): MemuFiles — AI «sort by meaning» (classify_and_organize), Properties → Details/Hashes,
one-click type filter, pinned tabs, and the bugs fixed in the audit (Esc in a menu, Explorer sort order, «null» in
Settings, tile heights, folder location). No network: the model is a fake."""
import json
import os
import re
import struct
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "memufiles" / "static"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEMUFILES_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield


def docx(path: Path, text: str, title: str = "") -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document><w:body>" + "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.split("\n")) + "</w:body></w:document>")
        zf.writestr("docProps/core.xml", f"<cp:coreProperties><dc:title>{title}</dc:title><dc:creator>Сидоров</dc:creator></cp:coreProperties>")
        zf.writestr("docProps/app.xml", "<Properties><Pages>3</Pages><Words>120</Words><Application>Microsoft Office Word</Application></Properties>")


PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
       b"4 0 obj<</Length 60>>stream\nBT /F1 14 Tf 20 150 Td (Invoice 77 Globex Inc Total 500 USD) Tj ET\nendstream endobj\n"
       b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def downloads(base: Path) -> Path:
    from PIL import Image
    dl = base / "Загрузки"
    dl.mkdir(parents=True)
    (dl / "Счёт №4521 ООО Ромашка.txt").write_text("СЧЁТ НА ОПЛАТУ № 4521 от 12.09.2026\nПоставщик: ООО «Ромашка»\n", encoding="utf-8")
    (dl / "invoice_acme.txt").write_text("INVOICE #A-998\nACME Corporation\nDate: 2026-09-03\n", encoding="utf-8")
    (dl / "resume_petrov.txt").write_text("Петров Алексей Сергеевич\nРезюме. Python-разработчик\n", encoding="utf-8")
    (dl / "scan0042.pdf").write_bytes(PDF)
    docx(dl / "Договор.docx", "ДОГОВОР АРЕНДЫ № 17\nАрендодатель: Сидоров П.П.", "Договор аренды")
    Image.new("RGB", (64, 48), (200, 30, 30)).save(dl / "photo.jpg")
    (dl / ".hidden").write_text("x", encoding="utf-8")
    (dl / "partial.crdownload").write_bytes(b"x")
    return dl


class FakeModel:
    """Answers like a fast model: invoices → Бухгалтерия/Счета, a resume → HR, the rest → Разное. Records calls."""

    def __init__(self, names=None, folder_override=None):
        self.calls = []
        self.names = names or {}
        self.folder_override = folder_override

    def __call__(self, messages):
        self.calls.append(messages)
        system = messages[0]["content"]
        if "раскладываешь" in system or "sort the user's files" in system:
            out = []
            for line in messages[-1]["content"].splitlines():
                row = json.loads(line)
                low = (row["name"] + " " + row.get("text", "")).lower()
                folder = "Бухгалтерия/Счета" if ("счёт" in low or "invoice" in low) else "HR" if "резюме" in low else "Разное"
                out.append({"i": row["i"], "folder": self.folder_override or folder,
                            "name": self.names.get(row["name"], "2026-09-" + row["name"].split(".")[0][:10]), "why": "документ"})
            yield "<think>hmm</think>Вот:\n```json\n" + json.dumps(out, ensure_ascii=False) + "\n```"
            return
        if "tool_result name=\"propose_plan\"" in messages[-1]["content"]:
            yield "Разложу по папкам."
            return
        yield '<tool>{"name": "propose_plan", "args": {"title": "По смыслу", "actions": [{"op": "classify_and_organize", "root": "."}]}}</tool>'


# ------------------------------------------------------------------ reading documents
def test_describe_reads_documents_and_metadata(tmp_path):
    from app.memufiles import organize
    dl = downloads(tmp_path)
    assert "4521" in organize.describe(str(dl / "Счёт №4521 ООО Ромашка.txt"))["text"]
    doc = organize.describe(str(dl / "Договор.docx"))
    assert "ДОГОВОР АРЕНДЫ" in doc["text"] and doc["meta"]["title"] == "Договор аренды"
    assert "Globex" in organize.describe(str(dl / "scan0042.pdf"))["text"]
    assert organize.describe(str(dl / "photo.jpg"))["meta"]["size"] == "64x48"
    # the crude PDF reader (no pypdf) still finds the text
    import sys
    saved = sys.modules.get("pypdf")
    sys.modules["pypdf"] = None
    try:
        assert "Globex" in organize._pdf_text(str(dl / "scan0042.pdf"), 500)[0]
    finally:
        if saved is not None:
            sys.modules["pypdf"] = saved
        else:
            sys.modules.pop("pypdf", None)


def test_names_and_folders_are_sanitised():
    from app.memufiles import organize
    assert organize.clean_folder("../../Windows/System32") == os.path.join("Windows", "System32")
    assert organize.clean_folder("C:\\Users\\x") == os.path.join("C", "Users", "x")
    assert organize.clean_folder("..") == ""
    assert organize.clean_folder("a/b/c/d/e") == os.path.join("a", "b", "c")
    assert organize.new_file_name("scan.pdf", "Счёт: 77 / Globex?") == "Счёт 77 Globex.pdf"
    assert organize.new_file_name("scan.pdf", "report.docx") == "report.docx.pdf"     # the extension is never changed
    assert organize.new_file_name("scan.pdf", "Счёт.pdf") == "Счёт.pdf"
    assert organize.new_file_name("scan.pdf", "...") == "scan.pdf"
    assert organize._parse_answer('<think>[x]</think> [{"i": 1, "folder": "A", "name": "b",}]') == [{"i": 1, "folder": "A", "name": "b"}]


# ------------------------------------------------------------------ plan → one operation → one undo
def test_plan_execute_and_undo_as_one_operation(tmp_path):
    from app.memufiles import assistant, opqueue
    assert "organize" in opqueue.KINDS                       # owned by r40-files-core: keep it there
    dl = downloads(tmp_path)
    before = sorted(os.listdir(dl))
    model = FakeModel(names={"invoice_acme.txt": "2026-09-ACME", "Счёт №4521 ООО Ромашка.txt": "2026-09-ACME"})
    plan = assistant.build_plan(str(dl), {"title": "t", "actions": [{"op": "classify_and_organize", "root": "."}]}, None, model, "ru")
    by = {os.path.basename(i["path"]): i for i in plan["items"]}
    assert ".hidden" not in by and "partial.crdownload" not in by           # hidden / unfinished downloads are left alone
    assert by["resume_petrov.txt"]["category"] == "HR"
    inv = sorted([by["invoice_acme.txt"]["new_name"], by["Счёт №4521 ООО Ромашка.txt"]["new_name"]])
    assert inv == ["2026-09-ACME (2).txt", "2026-09-ACME.txt"]                # two files never get one name
    assert all(i["op"] == "organize" and i["dest"].startswith(str(dl)) for i in plan["items"])
    queue = opqueue.OpQueue()
    res = assistant.execute_plan(plan, [i["i"] for i in plan["items"]], queue)
    assert len(res["ops"]) == 1
    op = queue.get(res["ops"][0])
    for _ in range(200):
        if op.status in ("done", "error", "cancelled"):
            break
        time.sleep(0.02)
    assert op.status == "done", op.errors
    assert (dl / "Бухгалтерия" / "Счета" / "2026-09-ACME.txt").exists()
    assert (dl / "HR" / "2026-09-resume_pet.txt").exists()
    undo = queue.undo()
    for _ in range(200):
        if undo.status in ("done", "error", "cancelled"):
            break
        time.sleep(0.02)
    assert sorted(os.listdir(dl)) == before                                  # every file back, the new folders gone
    with pytest.raises(Exception):
        assistant.execute_plan(plan, [0], queue)                             # a plan runs once


def test_model_cannot_escape_the_scope(tmp_path):
    from app.memufiles import assistant
    dl = downloads(tmp_path)
    plan = assistant.build_plan(str(dl), {"actions": [{"op": "classify_and_organize"}]}, None, FakeModel(folder_override="../../../../tmp"), "ru")
    assert all(i["dest"].startswith(str(dl)) for i in plan["items"])
    out = assistant.build_plan(str(dl), {"actions": [{"op": "classify_and_organize", "dest": str(tmp_path)}]}, None, FakeModel(), "ru")
    assert not out["items"] and out["rejected"][0]["why"] == "dest_outside_scope"
    none = assistant.build_plan(str(dl), {"actions": [{"op": "classify_and_organize"}]}, None, None, "ru")
    assert none["rejected"][0]["why"] == "no_model"


def test_run_chat_emits_reading_step_and_grouped_plan(tmp_path):
    import threading
    from app.memufiles import assistant
    dl = downloads(tmp_path)
    events = []
    model = FakeModel()
    assistant.run_chat(str(dl), [{"role": "user", "content": "разложи по смыслу"}], "ru", model, lambda e: events.append(e) or True,
                       assistant.PlanStore(), threading.Event())
    steps = [e for e in events if e["t"] == "step"]
    assert [s["tool"] for s in steps] == ["propose_plan", "classify_docs"] and steps[1]["args"]["files"] == 6
    plan = next(e["plan"] for e in events if e["t"] == "plan")
    assert {i["category"] for i in plan["items"]} >= {"HR", os.path.join("Бухгалтерия", "Счета")}
    assert events[-1]["t"] == "final" and "Разложу" in events[-1]["text"]
    assert "classify_and_organize" in assistant.system_prompt(str(dl), "ru") and "classify_and_organize" in assistant.system_prompt(str(dl), "en")


def test_batches_run_in_parallel_and_a_failed_batch_is_not_fatal(tmp_path, monkeypatch):
    from app.memufiles import organize
    docs = [{"i": i, "name": f"f{i}.txt", "modified": "2026-01-01", "text": "x"} for i in range(30)]
    calls = []

    def flaky(messages):
        calls.append(1)
        rows = [json.loads(line) for line in messages[-1]["content"].splitlines()]
        if rows[0]["i"] == 12:
            raise RuntimeError("gateway down")
        yield json.dumps([{"i": r["i"], "folder": "A", "name": "n"} for r in rows])
    out = organize.classify(flaky, docs, "ru")
    assert len(out) == 30 - organize.BATCH and len(calls) == 3


# ------------------------------------------------------------------ Properties → Details
def test_meta_reader(tmp_path):
    from PIL import Image
    from app.memufiles import feat40
    img = Image.new("RGB", (320, 200))
    exif = Image.Exif()
    exif[271], exif[272], exif[306] = "Canon", "EOS R6", "2026:07:01 10:20:30"
    img.save(tmp_path / "p.jpg", exif=exif, dpi=(300, 300))
    meta = feat40.meta(str(tmp_path / "p.jpg"))
    assert meta["dimensions"] == "320 × 200 px" and meta["camera"] == "Canon EOS R6" and meta["taken"] == "2026-07-01 10:20:30"
    docx(tmp_path / "d.docx", "текст", "Отчёт")
    meta = feat40.meta(str(tmp_path / "d.docx"))
    assert meta["title"] == "Отчёт" and meta["author"] == "Сидоров" and meta["pages"] == 3 and meta["words"] == 120
    # MP4: ftyp + moov/mvhd (version 0, timescale 1000, duration 125 500 ms)
    mvhd = b"mvhd" + bytes([0, 0, 0, 0]) + b"\0" * 8 + struct.pack(">II", 1000, 125500) + b"\0" * 80
    moov = struct.pack(">I4s", 8 + 4 + len(mvhd), b"moov") + struct.pack(">I", 4 + len(mvhd)) + mvhd
    (tmp_path / "v.mp4").write_bytes(struct.pack(">I4s", 16, b"ftyp") + b"isom\0\0\0\0" + struct.pack(">I4s", 16, b"mdat") + b"\0" * 8 + moov)
    assert feat40.meta(str(tmp_path / "v.mp4"))["duration"] == 125.5
    with zipfile.ZipFile(tmp_path / "a.zip", "w") as zf:
        zf.writestr("x.txt", "a" * 1000)
        zf.writestr("d/", "")
    assert feat40.meta(str(tmp_path / "a.zip")) == {"files": 1, "unpacked": 1000}
    (tmp_path / "t.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    assert feat40.meta(str(tmp_path / "t.py"))["lines"] == 2
    (tmp_path / "broken.png").write_bytes(b"nope")
    assert feat40.meta(str(tmp_path / "broken.png")) == {}


def test_meta_route(tmp_path):
    from app.memufiles import server as srv
    app = srv.FilesApp()
    httpd, _ = srv.start(app)
    try:
        (tmp_path / "t.txt").write_text("один\nдва\n", encoding="utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{app.port}/api/x40/meta", data=json.dumps({"path": str(tmp_path / "t.txt")}).encode(),
                                     method="POST", headers={"X-MF-Token": app.token})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as resp:
            assert json.loads(resp.read())["meta"]["lines"] == 2
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------ front-end contract
def test_frontend_contract():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'href="feat40.css"' in index and 'href="js/feat40.js"' in index
    app_js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert 'from "./feat40.js"' in app_js
    feat = (STATIC / "js" / "feat40.js").read_text(encoding="utf-8")
    en = feat[feat.index("addStrings({"):feat.index("}, {")]
    assert not re.search(r"[А-Яа-яЁё]", en), "Cyrillic in the English strings"
    keys_en = set(re.findall(r'"([a-z0-9_.]+)":', en))
    keys_ru = set(re.findall(r'"([a-z0-9_.]+)":', feat[feat.index("}, {"):]))
    assert keys_en == keys_ru
    dialogs = (STATIC / "js" / "dialogs.js").read_text(encoding="utf-8")
    assert "isWin() ? defaultShellBlock() : null" not in dialogs
    schema = __import__("app.memufiles.settings_schema", fromlist=["SCHEMA"]).SCHEMA
    assert any(e["key"] == "ai_fast_model" for e in schema)


# ------------------------------------------------------------------ UI (headless Chromium)
def _chromium():
    exe = os.environ.get("MEMU_TEST_CHROMIUM") or "/workspace/work/chromium/chrome"
    if not Path(exe).exists():
        pytest.skip("no chromium")
    return exe


def test_ui_filter_pins_props_menu_and_plan(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    from app.memufiles import fsops, server as srv
    from app.memunote import ai as note_ai
    monkeypatch.setattr(fsops, "open_default", lambda p: None)
    monkeypatch.setattr(note_ai, "status", lambda *a, **k: {"online": True, "models": [{"id": "fake", "label": "Fake"}], "default_model": "fake"})
    dl = downloads(tmp_path)
    (dl / "CV-Anna.md").write_text("# Anna Smith — CV\n", encoding="utf-8")
    app = srv.FilesApp()
    app.llm_override = FakeModel()
    httpd, _ = srv.start(app)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1280, "height": 800}, bypass_csp=True)   # r40-files-2: polling wait_for_function needs eval
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(f"http://127.0.0.1:{app.port}/index.html#t={app.token}")
            page.wait_for_selector(".tabbar .tab")
            page.evaluate("p => MemuFiles.pane().navigate(p)", str(dl))
            page.wait_for_function("MemuFiles.pane().shown.length > 5")
            # Explorer order: digits, Latin, then Cyrillic
            names = page.evaluate("() => MemuFiles.pane().shown.map((r) => r.n)")
            assert names.index("CV-Anna.md") < names.index("Договор.docx")
            # one-click type filter
            page.keyboard.press("Control+Shift+KeyF")
            page.click(".kchip[data-kind=image]")
            assert page.evaluate("() => MemuFiles.pane().shown.map((r) => r.n)") == ["photo.jpg"]
            page.click(".kchip[data-kind='']")
            # Esc in a context menu closes the menu only — the selection stays
            page.locator(".row", has_text="photo.jpg").first.click(button="right", position={"x": 60, "y": 12})
            page.wait_for_selector(".menu")
            page.keyboard.press("Escape")
            assert page.locator(".menu").count() == 0 and page.evaluate("() => MemuFiles.pane().sel.size") == 1
            # Properties: General · Details · Hashes (verify)
            page.keyboard.press("Alt+Enter")
            page.click(".ptab[data-t=details]")
            page.wait_for_selector(".ptab-page.details .kv")
            assert "64 × 48" in page.inner_text(".ptab-page.details")
            page.click(".ptab[data-t=hashes]")
            page.click(".hash-top .btn")
            page.wait_for_selector(".hash-row")
            page.fill(".ptab-page.hashes input", page.inner_text(".hash-row code"))
            assert "MD5" in page.inner_text(".hash-verdict")
            page.keyboard.press("Escape")
            # Settings: no stray «null» outside Windows
            page.evaluate("() => import('/js/dialogs.js').then((m) => m.settingsDialog(MemuFiles))")
            page.wait_for_selector(".modal-back")
            assert "null" not in page.inner_text(".modal-back")
            page.keyboard.press("Escape")
            # pinned tab: Ctrl+W keeps it, the session remembers it
            page.evaluate("p => MemuFiles.newTab(p)", str(tmp_path))
            page.locator(".tab").nth(1).click(button="right")
            page.click(".menu >> text=Закрепить вкладку")
            assert page.evaluate("() => MemuFiles.tabs[0].pinned") is True
            page.evaluate("() => MemuFiles.switchTab(0)")
            page.keyboard.press("Control+KeyW")
            assert page.evaluate("() => MemuFiles.tabs.length") == 2
            assert page.evaluate("() => MemuFiles.serializeTab(MemuFiles.tabs[0]).pinned") is True
            # AI: «sort by meaning» plan grouped by destination folder, one Ctrl+Z undoes it
            page.evaluate("() => MemuFiles.switchTab(1)")
            page.evaluate("() => MemuFiles.setRight('ai')")
            page.wait_for_selector(".ai-input textarea")
            page.fill(".ai-input textarea", "Разложи по смыслу")
            page.keyboard.press("Enter")
            page.wait_for_selector(".plan .plan-group")
            groups = page.eval_on_selector_all(".plan-group .pg-name", "els => els.map((e) => e.textContent)")
            assert "HR" in groups
            assert page.locator(".plan-item.org .pi-new").count() >= 6
            page.click(".plan-foot .btn.primary")
            page.wait_for_function("document.querySelector('.plan-foot') && /Готово|Done|выполнено|finished/i.test(document.querySelector('.plan-foot').textContent)", timeout=15000)
            assert (dl / "HR").is_dir()
            page.evaluate("() => MemuFiles.setRight('preview')")
            page.locator(".row").first.click(position={"x": 60, "y": 12})
            page.keyboard.press("Control+KeyZ")
            page.wait_for_function("!document.querySelector('.ops-running, .op-running')", timeout=5000)
            for _ in range(100):
                if not (dl / "HR").exists():
                    break
                time.sleep(0.05)
            assert not (dl / "HR").exists() and (dl / "resume_petrov.txt").exists()
            assert not errors, errors
            browser.close()
    finally:
        httpd.shutdown()


def test_design_auditor_detects_problems():
    """The auditor's page checks are not blind: a cut label, low contrast and a stray «null» are reported."""
    pw = pytest.importorskip("playwright.sync_api")
    import importlib.util
    spec = importlib.util.spec_from_file_location("mf_audit", ROOT / "tests" / "round40_files_design_audit.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        page = browser.new_page()
        page.set_content("<body style='background:#fff'><div style='width:40px;overflow:hidden;white-space:nowrap'>Очень длинная подпись</div>"
                         "<p style='color:#ddd'>бледный текст</p><span>null</span><button style='position:absolute;left:-30px'>x</button></body>")
        kinds = {f["kind"] for f in page.evaluate(audit.AUDIT_JS, {"layer": ""})}
        browser.close()
    assert {"clipped", "contrast", "junk-text", "offscreen"} <= kinds
