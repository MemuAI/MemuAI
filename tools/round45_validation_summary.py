"""Collect the last successful Round 45 gates without double-counting retries."""
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

def log_text(name):
    return (BUILD / name).read_text("utf-8", errors="replace")

def main():
    rows = {}

    for directory in ("audit-isolated-20261023-131125", "audit-isolated-20261023-131254", "audit-isolated-20261023-131507"):
        directory = BUILD / directory
        for row in json.loads((directory / "summary.json").read_text("utf-8")):
            if row["module"] == "round12_models_test":
                continue
            output_path = directory / (row["module"] + ".log")
            output = output_path.read_text("utf-8")
            skipped = re.search(r"skipped=(\d+)", output) or re.search(r"(\d+) skipped", output)
            rows[row["module"]] = {**row, "skipped": int(skipped[1]) if skipped else 0,
                                    "log": output_path.relative_to(ROOT).as_posix()}
    require(all(row["exit"] == 0 for row in rows.values()), "A final Python module failed")
    for row in rows.values():
        row["passed"] = row["tests"] - row["skipped"]
    extra = []
    for name, expected in (("round45-chrome-ui.log", 40), ("round45-layout-test.log", 6)):
        output = log_text(name)
        match = re.search(r"(\d+) passed in", output)
        require(match and int(match[1]) == expected and "FAILED" not in output, "UI gate failed: " + name)
        extra.append({"gate": name, "passed": expected})
    require("Ran 4 tests" in log_text("round45-version-test.log") and "OK" in log_text("round45-version-test.log"), "Version gate failed")
    extra.append({"gate": "round45-version-test.log", "passed": 4})
    js = ["round45-chrome-worker.log", "round45-catalog-ui.log", "round45-telegram-ui.log",
          "round45-integrations-ui.log", "round45-telegram-baseline-ui.log"]
    for name in js:
        output = log_text(name)
        require("PASS" in output and "FAIL" not in output and "AssertionError" not in output, "JavaScript gate failed: " + name)
    require("All checks passed!" in log_text("round45-critical-lint.log"), "Critical lint failed")
    require("ИТОГО: пройдено 45, провалено 0" in log_text("round45-smoke.log"), "Smoke gate failed")
    source = log_text("round45-frozen.log")
    require("PASS Round 45 source headless + frozen archive/CLI checks" in source, "Headless / archive proof failed")
    components = json.loads((BUILD / "round45-compiled-version.json").read_text("utf-8"))
    require(len(components["executables"]) == 6, "Compiled version proof incomplete")
    require(components.get("memu_cli", {}).get("commands_passed") == 3, "Lightweight CLI proof incomplete")
    require("PASS" in log_text("round45-installer-verify.log"), "Installer payload proof failed")

    extension = ROOT / "dist/MemuAI-Chrome.zip"
    base = ROOT / "app/pgstatic/browser-extension"
    with zipfile.ZipFile(extension) as archive:
        expected = {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file() and not p.is_symlink()}
        require(set(archive.namelist()) == expected, "Extension ZIP file set differs from source")
        for name in expected:
            require(archive.read(name) == (base / name).read_bytes(), "Extension ZIP differs: " + name)
        manifest = json.loads(archive.read("manifest.json"))
        require(manifest["version"] == "3.8.45" and manifest["manifest_version"] == 3, "Wrong extension version")
        for size, name in manifest["icons"].items():
            with Image.open(io.BytesIO(archive.read(name))) as icon:
                require(icon.size == (int(size), int(size)), "Wrong icon size: " + name)
    artifacts = []
    for path in (ROOT / "installer/MemuAI-Setup-3.8.exe", extension):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        artifacts.append({"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": digest.hexdigest().upper()})
    require(artifacts[0]["sha256"] in log_text("round45-installer-verify.log"), "Installer proof belongs to an older artifact")
    result = {"date": "2026-10-23", "version": "3.8", "extension_version": "3.8.45",
              "python": {"modules": len(rows), "tests_run": sum(r["tests"] for r in rows.values()),
                         "passed": sum(r["passed"] for r in rows.values()), "skipped": sum(r["skipped"] for r in rows.values()),
                         "failed": 0, "results": list(rows.values())}, "extra_python_gates": extra,
              "passed_total": sum(r["passed"] for r in rows.values()) + sum(g["passed"] for g in extra),
              "javascript_gates": js, "smoke_passed": 45, "compiled_cli_commands_passed": 3, "artifacts": artifacts,
              "notes": ["Initial non-existent round12_models_test corrected to round12_test; raw logs retained.",
                        "Initial CLI temporary-process teardown failure passed in an isolated repeat; both logs retained.",
                        "Frozen MemuAI GUI startup not run while user's existing instance holds its global mutex. Source headless with isolated test mutex and actual frozen archive comparison used.",
                        "No real provider accounts, paid APIs, user browser profiles or Computer Use."]}
    (BUILD / "round45-validation-summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"passed_total": result["passed_total"], "modules": len(rows),
                      "skipped": result["python"]["skipped"], "artifacts": artifacts}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
