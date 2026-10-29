"""Run critical offline test modules in separate, isolated Python processes.

Separating modules prevents their global mocks, caches and event loops from
contaminating one another. Logs and application data stay under build/.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MODULES = """
release_build_test release_data_test release_download_test release_lifecycle_test
release_local_models_test release_log_storage_test release_mcp_test release_memory_test
release_module_transaction_test release_modules_test release_permissions_test release_prompt_test
release_public_test release_queue_test release_security_test release_stream_test release_ui_test
round24_proxy_test round24_telegram_test round25_chat_test round25_proxy_test round25_work_test
round26_chat_test round26_chat2_test round26_proxy_test round26_storage_test round26_telegram_test
round26_work_integrations_test round27_hunt_chat_test round27_hunt_tg_test round27_proxy_code_test
round29_memory_test round29_safe_mode_test round29_schedsend_test round29_schedule_rules_test
round29_search_test round29_stuck_test round29_sync_test round29_tg_api_test
round29c_media_telegram_test round29c_scheduling_test round29c_sync_safe_test
round29d_chat4_test round29d_sched_media_test round29t_chat_test round29t_telegram_test
round30_dfy_proxy_test round30_integrations_test round30_misc_test round30_modules_errors_test
round31_bughunt_test round32_memucode_test round32_misc_test round32_skills_test
round33_chat_test round33_fixes_test round33_hotfix_test round33_instructions_test
round33_integrations_test round33_local_test round33_media_cache_test round33_memucode_test
round33_models_lists_test round33_models_test round33_proxy_test round33_thinking_test
round34_cli_tg_test round34_main_chat_test round34_proxy_test round34_quick_agentic_test
round35_askuser_test round35_branches_test round35_composite_test round35_detector_test
round35_finish_c_test round35_flows_test round35_fsindex_test round35_inbox_test
round35_mentions_test round35_protocol_test round35_quota_test round35_triggers_test
round36_audit2_test round36_chat_test round36_fsearch_test round36_perf_core_test
round36_perf_core2_test round36_plan_test round36_proxy_test round36_skills_test
round37_extras_test round37_extras2_test round37_geminiweb_test round37_plan_test round37_plan2_test
round38_fixes_test round38_mod_host_test round38_mod_integ_test round38_mod_integ2_test
round38_suite_test round39_integ_test round40_lag_test round40_learn_test
round41_gemini_web_test round41b_gemini_media_test scheduler_contract_test
telegram_contract_test telegram_r20_test telegram_r21_reliability_test telegram_r21_sync_test
voice_contract_test work_agent_contract_test
""".split()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modules", nargs="*", help="test module names (without tests.)")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=150)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 8 or args.timeout <= 0:
        parser.error("jobs must be 1–8 and timeout must be positive")
    modules = list(dict.fromkeys(args.modules or MODULES))
    for module in modules:
        if not re.fullmatch(r"[A-Za-z_]\w*", module) or not (ROOT / "tests" / (module + ".py")).is_file():
            parser.error(f"unknown test module: {module}")
    log_dir = ROOT / "build" / ("audit-isolated-" + time.strftime("%Y%m%d-%H%M%S"))
    log_dir.mkdir(parents=True)

    def run(module):
        data = log_dir / module / "data"
        env = dict(os.environ, PYTHONUTF8="1", MEMUAI_DATA_DIR=str(data),
                   MEMUFILES_TRASH_DIR=str(data / "trash"), NO_PROXY="127.0.0.1,localhost")
        if not env.get("MEMU_TEST_CHROMIUM"):
            for candidate in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
                if Path(candidate).is_file():
                    env["MEMU_TEST_CHROMIUM"] = candidate
                    break
        for key in list(env):
            if (key.startswith(("XKIRO_", "MEMU_LIVE_", "SOL_", "REFORM_", "YANDEX_", "AWS9_"))
                    or key.endswith(("_KEY", "_KEY2", "_KEY3", "_TOKEN"))
                    or "_LIVE" in key or key == "MEMUAI_PROXY_URL"):
                env.pop(key)
        started = time.monotonic()
        try:
            result = subprocess.run([sys.executable, "-u", "-m", "unittest", "tests." + module, "-q"],
                                    cwd=ROOT, env=env, capture_output=True, timeout=args.timeout,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            output = (result.stdout + result.stderr).decode("utf-8", "replace").replace("\r\n", "\n")
            status = result.returncode
            # unittest reports success even when a module contains only pytest
            # functions. Such a module must actually run before it can pass.
            count = re.search(r"Ran (\d+) tests?", output)
            if (status == 5 and "NO TESTS RAN" in output) or (status == 0 and count and int(count[1]) == 0):
                result = subprocess.run([sys.executable, "-u", "-m", "pytest", "tests/" + module + ".py",
                                         "-q", "--tb=short", "--disable-warnings"], cwd=ROOT, env=env,
                                        capture_output=True, timeout=args.timeout,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                output = (result.stdout + result.stderr).decode("utf-8", "replace").replace("\r\n", "\n")
                status = result.returncode
        except subprocess.TimeoutExpired as exc:
            output = ((exc.stdout or b"") + (exc.stderr or b"")).decode("utf-8", "replace") + "\nMODULE TIMEOUT\n"
            status = -1
        (log_dir / (module + ".log")).write_text(output, encoding="utf-8")
        count = re.search(r"Ran (\d+) tests?", output)
        summary = output.splitlines()[-1] if output.splitlines() else ""
        pytest_counts = dict((kind, int(number)) for number, kind in re.findall(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed)\b", summary))
        skipped = re.search(r"skipped=(\d+)", output) if count else None
        tests = int(count[1]) if count else sum(pytest_counts.values())
        # Empty modules and import-only helpers are never a successful check.
        if status == 0 and tests == 0:
            status = 5
        return {"module": module, "exit": status, "tests": tests,
                "skipped": int(skipped[1]) if skipped else pytest_counts.get("skipped", 0),
                "seconds": round(time.monotonic() - started, 1)}

    rows = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for result in as_completed([pool.submit(run, m) for m in modules]):
            row = result.result()
            rows.append(row)
            print(("PASS" if row["exit"] == 0 else "FAIL"), row["module"], row["tests"], flush=True)
    (log_dir / "summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    failed = [r for r in rows if r["exit"]]
    print(f"{len(rows) - len(failed)}/{len(rows)} modules passed; {sum(r['tests'] for r in rows)} tests run; logs: {log_dir}")
    return bool(failed)

if __name__ == "__main__":
    raise SystemExit(main())
