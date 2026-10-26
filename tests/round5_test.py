# -*- coding: utf-8 -*-
"""Round 5 regression tests: agent storage, Telegram behaviour and UI contracts."""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TELEGRAM = "\n".join((ROOT / "app" / name).read_text(encoding="utf-8")
                     for name in ("telegram_bot.py", "telegram_media.py"))
APP_JS = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
API = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")

class Round5Preferences(unittest.TestCase):
    """Agents used to live only in localStorage and vanished on restart (item 11)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="memu-round5-")
        os.environ["MEMUAI_DATA_DIR"] = self.tmp
        from app import pgdata
        importlib.reload(pgdata)
        self.pgdata = pgdata

    def test_agent_storage_keys_exist(self):
        for key in ("memes", "chat_memes", "memes_open", "section_order", "context_count_images"):
            self.assertIn(key, self.pgdata.DEFAULT_PREFERENCES)

    def test_agents_survive_a_restart(self):
        rows = [{"id": "m1", "name": "\u041a\u043e\u0442", "instructions": "\u043c\u044f\u0443", "model": "gpt-4o"}]
        self.pgdata.save_preferences({"memes": rows, "chat_memes": {"c1": "m1"},
                                      "section_order": ["memes", "chats", "folders"]})
        importlib.reload(self.pgdata)
        stored = self.pgdata.load_preferences()
        self.assertEqual(stored["memes"][0]["name"], "\u041a\u043e\u0442")
        self.assertEqual(stored["chat_memes"]["c1"], "m1")
        self.assertEqual(stored["section_order"][0], "memes")

    def test_telegram_notifications_are_off_by_default(self):
        self.assertEqual(self.pgdata.DEFAULT_PREFERENCES["telegram_notify_mode"], "off")
        legacy = self.pgdata._normalize_preferences({**self.pgdata.DEFAULT_PREFERENCES,
                                                     "telegram_notify": True})
        self.assertEqual(legacy["telegram_notify_mode"], "off")

    def test_notify_mode_round_trip(self):
        self.pgdata.save_preferences({"telegram_notify_mode": "sound"})
        self.assertEqual(self.pgdata.load_preferences()["telegram_notify_mode"], "sound")

class Round5Telegram(unittest.TestCase):
    """Items 9 and 10."""

    def test_notifications_can_be_silent(self):
        self.assertIn('payload["disable_notification"] = True', TELEGRAM)
        self.assertIn("markdown=True, silent=quiet", TELEGRAM)

    def test_notifications_are_gated_by_mode(self):
        self.assertIn('if mode == "off" or not prefs.get("telegram_enabled")', TELEGRAM)
        self.assertIn("def _notify_mode(", TELEGRAM)

    def test_agent_screen_exists(self):
        for token in ("async def _show_agents(", 'elif command == "/agents":',
                      'elif data.startswith("agent:")', "def _active_agent("):
            self.assertIn(token, TELEGRAM)

    def test_agent_persona_reaches_the_model(self):
        self.assertIn("<agent_persona name=", TELEGRAM)
        self.assertIn("_system_prompt, model, prefs, profile, agent)", TELEGRAM)

    def test_attachments_are_stored_in_memuai(self):
        self.assertIn("attachments=tg_files", TELEGRAM)
        self.assertIn('user_row["attachments"]', TELEGRAM)
        self.assertIn('files.append({"name": "telegram-photo.jpg"', TELEGRAM)

    def test_notify_endpoint_forwards_silence(self):
        self.assertIn('silent=bool(body.get("silent"))', API)

class Round5Interface(unittest.TestCase):
    """Items 1-8 and 12-14 as front-end contracts."""

    def test_image_download_uses_the_backend(self):
        self.assertIn("saveToDownloads", APP_JS)
        self.assertIn("/api/pg/download-file", APP_JS)
        self.assertIn("/api/pg/download-file", API)

    def test_resend_keeps_the_picture(self):
        self.assertIn("prepareImagesForResend", APP_JS)

    def test_context_meter_splits_images(self):
        self.assertIn("contextTokenTotals", APP_JS)
        self.assertIn("imageTokenEstimate", APP_JS)
        self.assertIn("context_count_images", APP_JS)

    def test_dropdown_search_is_a_combobox(self):
        self.assertIn("pickMatches", APP_JS)
        self.assertIn("mpick-pop", APP_JS)

    def test_agent_avatars_are_generated(self):
        self.assertIn("MEME_AVATAR_KINDS", APP_JS)
        self.assertIn("memeAvatarSvg", APP_JS)

    def test_agent_chats_lock_model_and_web_search(self):
        self.assertIn("memeModelLock", APP_JS)
        self.assertIn("renderModelLockNote", APP_JS)

    def test_agent_export_and_import(self):
        self.assertIn("function exportMemes(", APP_JS)
        self.assertIn("function importMemesFromFile(", APP_JS)
        self.assertIn("imp-meme-model", APP_JS)

    def test_folders_show_their_chats(self):
        self.assertIn("FOLDER_PAGE_SIZE", APP_JS)
        self.assertIn("folderRenderLimits", APP_JS)

    def test_sidebar_is_translated(self):
        self.assertIn("\u041c\u0435\u043c\u0435\u0441\u044b", APP_JS)

    def test_transfer_pane_exists(self):
        for token in ("initTransferPane", "pane-transfer", "tr-export-chats", "tr-import-chats", "tr-about"):
            self.assertIn(token, APP_JS)

    def test_about_prompt_is_verbatim(self):
        for phrase in ("\u0414\u043e\u043a\u0430\u0437\u0430\u0442\u0435\u043b\u044c\u0441\u0442\u0432\u043e: \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u0441\u043a\u0430\u0437\u0430\u043b",
                       "\u0418\u043c\u043f\u043e\u0440\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u043e \u0438\u0437",
                       "\u0414\u0435\u043c\u043e\u0433\u0440\u0430\u0444\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u0438\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f"):
            self.assertIn(phrase, APP_JS)

if __name__ == "__main__":
    unittest.main()
