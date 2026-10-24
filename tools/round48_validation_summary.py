"""Focused Round 48 release proof, with actual artifact hashes and no duplicate retries."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"


def read(name):
    return (BUILD / name).read_text("utf-8", errors="replace")


def main():
    folder = Path(re.search(r"logs: (.+)", read("round48-regressions.log"))[1].strip())
    rows = json.loads((folder / "summary.json").read_text("utf-8"))
    assert len(rows) == 11 and all(row["exit"] == 0 for row in rows)
    skipped = 0
    for row in rows:
        output = (folder / (row["module"] + ".log")).read_text("utf-8")
        match = re.search(r"skipped=(\d+)", output) or re.search(r"(\d+) skipped", output)
        row["skipped"] = int(match[1]) if match else 0
        skipped += row["skipped"]
    assert "4 passed" in read("round48-ui-layout.log") and "FAILED" not in read("round48-ui-layout.log")
    js = ["round48-js-fields.log", "round48-js-setup.log", "round48-js-diagnostic.log"]
    for name in js:
        assert "PASS" in read(name) and "AssertionError" not in read(name) and "FAIL" not in read(name)
    assert "PASS JavaScript syntax" in read("round48-js-syntax.log")
    assert "All checks passed!" in read("round48-critical-lint.log")
    assert "ИТОГО: пройдено 45, провалено 0" in read("round48-smoke.log")
    gateway = json.loads(read("round48-gateway-probe-check.json"))
    assert gateway["counters"]["automatic"]["post"] > 0 and gateway["counters"]["manual"]["post"] == 0
    public = json.loads(read("round48-public-catalog.json"))
    assert public["model_count"] > 0 and public["gateway_processes"] == public["external_generations"] == 0
    assert "PASS Round 48 source headless + frozen archive/CLI checks" in read("round48-frozen.log")
    components = json.loads(read("round48-compiled-version.json"))
    assert len(components["executables"]) == 6 and components["memu_cli"]["commands_passed"] == 3
    extension = ROOT / "dist/MemuAI-Chrome.zip"
    base = ROOT / "app/pgstatic/browser-extension"
    with zipfile.ZipFile(extension) as archive:
        expected = {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file() and not p.is_symlink()}
        assert set(archive.namelist()) == expected
        assert all(archive.read(name) == (base / name).read_bytes() for name in expected)
    artifacts = []
    for path in (ROOT / "installer/MemuAI-Setup-3.8.exe", extension):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest().upper()
        artifacts.append({"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": digest})
    assert "PASS installer payload" in read("round48-installer-verify.log")
    assert artifacts[0]["sha256"] in read("round48-installer-verify.log")
    result = {"round": 48, "date": "2026-10-24", "version": "3.8", "extension_version": "3.8.46",
              "python": {"modules": len(rows), "tests_run": sum(r["tests"] for r in rows),
                         "passed": sum(r["tests"] for r in rows) - skipped, "skipped": skipped, "failed": 0,
                         "results": rows}, "ui_passed": 4, "javascript_gates": js, "smoke_passed": 45,
              "passed_total": sum(r["tests"] for r in rows) - skipped + 4,
              "real_gateway_probe_check": gateway, "live_public_catalog": public, "artifacts": artifacts,
              "limits": ["Focused regression gate, not a repeat of the entire Round 47 broad suite.",
                         "Direct public generation control returned 403 FreeTierError requiring official OpenCode; successful live Free generation is not confirmed.",
                         "Manual availability suppresses probes for known models; third-party gateway routing may perform its own internal operations.",
                         "No Computer Use, installed GUI control, private account data or paid generation."]}
    (BUILD / "round48-validation-summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({k: result[k] for k in ("passed_total", "artifacts")}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
