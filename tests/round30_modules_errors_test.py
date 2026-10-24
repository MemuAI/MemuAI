"""Round 30: module-facing server errors follow the MemuAI language (a module shows error.message as is)."""
import os
import re
import tempfile
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r30-moderr-")

ROOT = Path(__file__).resolve().parents[1]


class ModuleErrorsLanguageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import socket
        from fastapi.testclient import TestClient
        with socket.socket() as probe:  # a closed local port: the Proxy is "not running", no network
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls._env = os.environ.get("MEMUAI_PROXY_URL")
        os.environ["MEMUAI_PROXY_URL"] = f"http://127.0.0.1:{port}"
        from app import playground_api
        cls.client = TestClient(playground_api.create_app(), base_url="http://127.0.0.1").__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        if cls._env is None:
            os.environ.pop("MEMUAI_PROXY_URL", None)
        else:
            os.environ["MEMUAI_PROXY_URL"] = cls._env

    def lang(self, value: str) -> None:
        from app import pgdata
        pgdata.save_preferences({"language": value})

    def test_model_list_error_follows_language(self):
        for lang, text in (("ru", "Список моделей недоступен"), ("en", "The model list is unavailable")):
            self.lang(lang)
            response = self.client.post("/api/pg/modules/models", json={})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json()["error"], text)

    def test_unknown_module_and_permissions_follow_language(self):
        for lang, missing, denied in (("ru", "Модуль не найден", "Модулю не разрешено открывать ссылки"),
                                      ("en", "Module not found", "The module is not allowed to open links")):
            self.lang(lang)
            self.assertEqual(self.client.post("/api/pg/modules/configure", json={"id": "no-such-module", "enabled": True})
                             .json().get("error"), missing)
            self.assertEqual(self.client.post("/api/pg/modules/open", json={"module_id": "no-such-module", "url": "https://x.y"})
                             .json().get("error"), denied)

    def test_module_routes_have_no_bare_russian_errors(self):
        """Static guard: every Russian error literal in the /api/pg/modules* routes has an English twin."""
        text = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        start = text.index('@app.get("/api/pg/modules")')
        end = text.index('@app.get("/api/pg/media")')
        bare = re.findall(r'\{"error": f?"[^"]*[А-Яа-яЁё][^"]*"\}', text[start:end])
        self.assertEqual(bare, [])


if __name__ == "__main__":
    unittest.main()
