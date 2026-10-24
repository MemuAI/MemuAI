"""Shortcut startup must probe and publish the server's base URL."""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import memuai
import uvicorn
from app import desktop, playground_api, storage


class ShortcutStartupRegressionTest(unittest.TestCase):
    def test_chat_shortcuts_keep_ui_query_out_of_health_and_runtime_urls(self):
        for flag, action in (("--new-chat", "new"), ("--temporary-chat", "temp")):
            with self.subTest(flag=flag), contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(sys, "argv", ["memuai.py", flag, "--no-start-proxy", "--port", "18680"]))
                stack.enter_context(patch.object(memuai, "_read_runtime_url", return_value="http://127.0.0.1:8317"))
                probe = stack.enter_context(patch.object(memuai, "_is_online", return_value=True))
                stack.enter_context(patch.object(memuai, "_startup_theme", return_value="dark"))
                stack.enter_context(patch.object(memuai, "_memuai_tray_icon", return_value=None))
                stack.enter_context(patch.object(desktop, "acquire_instance_lock", return_value=Mock()))
                stack.enter_context(patch.object(desktop, "stop_server"))
                stack.enter_context(patch.object(memuai.threading, "Thread", return_value=Mock()))
                stack.enter_context(patch.object(uvicorn, "Server", return_value=Mock()))
                stack.enter_context(patch.object(playground_api, "create_app", return_value=SimpleNamespace(state=SimpleNamespace())))
                stack.enter_context(patch.object(desktop, "wait_for_service", side_effect=lambda predicate, *_args, **_kw: predicate()))
                saved = stack.enter_context(patch.object(storage, "save_json"))
                shell = stack.enter_context(patch.object(desktop, "DesktopShell"))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                memuai.main()
                self.assertEqual(probe.call_args.args[:2], ("http://127.0.0.1:18680", "/api/pg/ping"))
                self.assertEqual(saved.call_args.args[1]["url"], "http://127.0.0.1:18680")
                self.assertEqual(shell.call_args.args[0], "http://127.0.0.1:18680/?start=" + action)


if __name__ == "__main__":
    unittest.main()
