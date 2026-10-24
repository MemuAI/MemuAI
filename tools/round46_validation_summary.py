"""Collect final Round 46 release evidence without double-counting retries."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"


def require(value, message):
    if not value:
        raise AssertionError(message)


def read(name):
    return (BUILD / name).read_text("utf-8", errors="replace")


def main():
    rows = {}
    for name in ("audit-isolated-20261023-205428", "audit-isolated-20261024-073238"):
        folder = BUILD / name
        for row in json.loads((folder / "summary.json").read_text("utf-8")):
            path = folder / (row["module"] + ".log")
            output = path.read_text("utf-8", errors="replace")
            skip = re.search(r"skipped=(\d+)", output) or re.search(r"(\d+) skipped", output)
            skipped = int(skip[1]) if skip else 0
            rows[row["module"]] = {**row, "skipped": skipped, "passed": row["tests"] - skipped,
                                    "log": path.relative_to(ROOT).as_posix()}
    require(all(r["exit"] == 0 for r in rows.values()), "Final backend gate failed")
    extra = []
    for name, count in (("round46-ui-regressions.log", 62), ("round46-telegram-preview-final-layout.log", 4)):
        output = read(name)
        match = re.search(r"(\d+) passed in", output)
        require(match and int(match[1]) == count and "FAILED" not in output, "UI gate failed: " + name)
        extra.append({"gate": name, "passed": count})
    require("Ran 3 tests" in read("round46-version-test.log") and "OK" in read("round46-version-test.log"), "Version gate failed")
    extra.append({"gate": "round46-version-test.log", "passed": 3})
    js = [p.name for p in sorted(BUILD.glob("round46-js-*.log")) if p.name != "round46-js-syntax.log"]
    require(len(js) == 13, "Incomplete JavaScript gates")
    for name in js:
        output = read(name)
        require(("PASS" in output or "scenarios passed" in output) and "AssertionError" not in output
                and "FAIL" not in output, "JavaScript gate failed: " + name)
    require("All checks passed!" in read("round46-critical-lint.log"), "Critical lint failed")
    require("PASS JavaScript syntax" in read("round46-js-syntax.log"), "Syntax gate failed")
    require("ИТОГО: пройдено 45, провалено 0" in read("round46-smoke.log"), "Smoke gate failed")
    require("PASS Round 46 source headless + frozen archive/CLI checks" in read("round46-frozen.log"), "Headless/archive proof failed")
    require("PASS: frozen MemuFiles" in read("round46-frozen-apps.log")
            and "PASS: frozen MemuNote" in read("round46-frozen-apps.log"), "Frozen module proof failed")
    components = json.loads((BUILD / "round46-compiled-version.json").read_text("utf-8"))
    require(len(components["executables"]) == 6 and components.get("memu_cli", {}).get("commands_passed") == 3,
            "Compiled component proof incomplete")
    require("PASS installer payload" in read("round46-installer-verify.log"), "Payload proof failed")

    extension = ROOT / "dist/MemuAI-Chrome.zip"
    base = ROOT / "app/pgstatic/browser-extension"
    with zipfile.ZipFile(extension) as archive:
        expected = {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file() and not p.is_symlink()}
        require(set(archive.namelist()) == expected, "Extension ZIP file set differs")
        for name in expected:
            require(archive.read(name) == (base / name).read_bytes(), "Extension ZIP differs: " + name)
        manifest = json.loads(archive.read("manifest.json"))
        require(manifest["version"] == "3.8.46" and manifest["manifest_version"] == 3, "Wrong extension version")
        for size, name in manifest["icons"].items():
            with Image.open(io.BytesIO(archive.read(name))) as icon:
                require(icon.size == (int(size), int(size)), "Incorrect icon size")
    artifacts = []
    for path in (ROOT / "installer/MemuAI-Setup-3.8.exe", extension):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        artifacts.append({"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size,
                          "sha256": digest.hexdigest().upper()})
    require(artifacts[0]["sha256"] in read("round46-installer-verify.log"), "Payload proof belongs to an older installer")
    result = {"date": "2026-10-24", "version": "3.8", "extension_version": "3.8.46",
              "python": {"modules": len(rows), "tests_run": sum(r["tests"] for r in rows.values()),
                         "passed": sum(r["passed"] for r in rows.values()), "skipped": sum(r["skipped"] for r in rows.values()),
                         "failed": 0, "results": list(rows.values())}, "extra_python_gates": extra,
              "passed_total": sum(r["passed"] for r in rows.values()) + sum(g["passed"] for g in extra),
              "javascript_gates": js, "smoke_passed": 45, "compiled_cli_commands_passed": 3, "artifacts": artifacts,
              "notes": ["Initial failures of legacy assertions/AST parsers corrected against current provider and build contracts; raw logs retained.",
                        "Final recheck supersedes original modules; overlapping focused/review gates are not added to the count.",
                        "CLI differential review: 40 Read ranges and 528 Grep cases; benchmark estimates are not provider billing.",
                        "Frozen desktop GUI/compositor not controlled or interrupted; isolated source headless entry plus actual frozen archive/component comparison used.",
                        "No Computer Use, user browser profiles, real account writes or paid generations."]}
    (BUILD / "round46-validation-summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"passed_total": result["passed_total"], "modules": len(rows),
                      "skipped": result["python"]["skipped"], "artifacts": artifacts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
