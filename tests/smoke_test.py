"""Интеграционный смоук-тест MemuAI: Proxy, Playground и passthrough.

Запуск:  .venv/Scripts/python tests/smoke_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
import uvicorn  # noqa: E402

MOCK_GS_PORT = 18517
MOCK_CUSTOM_PORT = 18527
APP_PORT = 18617
PG_PORT = 18618
BASE = f"http://127.0.0.1:{APP_PORT}"
PG_BASE = f"http://127.0.0.1:{PG_PORT}"
MOCK_GS = f"http://127.0.0.1:{MOCK_GS_PORT}"
MOCK_CUSTOM = f"http://127.0.0.1:{MOCK_CUSTOM_PORT}"

PASSED, FAILED = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name}  {detail}")


def run_server(app, port: int, http: str = "auto") -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", http=http))
    threading.Thread(target=server.run, daemon=True).start()
    return server


def wait_ready(client: httpx.Client, path: str, timeout: float = 25) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if client.get(path).status_code in (200, 401):
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"сервер на {path} не поднялся")


def main() -> None:
    data_dir = tempfile.mkdtemp(prefix="omnihub-test-")
    os.environ["OMNIHUB_DATA_DIR"] = data_dir

    from tests import mock_genspark, mock_custom
    from app.main import create_app
    os.environ["MEMUAI_PROXY_URL"] = BASE
    os.environ["MEMUAI_TOKEN"] = ""
    from app.playground_api import create_app as create_playground_app

    playground_app = create_playground_app()

    class FakeFolderPicker:
        def create_file_dialog(self, *_args, **_kwargs):
            return [data_dir]

    # Work accepts folders only through the desktop's native picker.  The
    # integration test supplies that boundary without weakening the API.
    playground_app.state.window = FakeFolderPicker()
    # pywebview exists only in the Windows build; the picker needs just its FOLDER_DIALOG constant.
    try:
        import webview  # noqa: F401
    except ImportError:
        import types
        sys.modules["webview"] = types.SimpleNamespace(FOLDER_DIALOG=20)

    # curl_cffi (chrome impersonation) sends plain-http requests with "Upgrade: h2c"; uvicorn's httptools
    # parser then drops the request body (the mock saw {} and 0 uploaded bytes). Real Genspark is HTTPS.
    srv1 = run_server(mock_genspark.app, MOCK_GS_PORT, http="h11")
    srv2 = run_server(mock_custom.app, MOCK_CUSTOM_PORT)
    srv3 = run_server(create_app(), APP_PORT)
    srv4 = run_server(playground_app, PG_PORT)

    with httpx.Client(timeout=60) as client:
        wait_ready(client, f"{MOCK_GS}/mock/state")
        wait_ready(client, f"{MOCK_CUSTOM}/v1/models")
        wait_ready(client, f"{BASE}/health")
        wait_ready(client, f"{PG_BASE}/api/pg/ping")

        print("\n[1] вход в панель")
        time.sleep(1.8)
        token = client.post(f"{BASE}/api/admin/login", json={"password": "admin"}).json()["token"]
        H = {"Authorization": f"Bearer {token}"}
        check("логин работает", bool(token))

        print("\n[2] кастомное подключение → префикс mysite")
        r = client.post(f"{BASE}/api/admin/accounts", headers=H, json={
            "provider": "custom",
            "fields": {"name": "My Site", "base_url": MOCK_CUSTOM + "/v1",
                       "api_key": "sk-custom-123", "models": "gpt-test, claude-test"},
            "label": "тестовый"})
        check("аккаунт добавлен", r.status_code == 200, r.text[:200])
        acc = r.json().get("account", {})
        models = client.get(f"{BASE}/api/admin/models", headers=H).json()["models"]
        ids = [m["id"] for m in models]
        check("модели с префиксом (пробелы → дефис)",
              "my-site-gpt-test" in ids and "my-site-claude-test" in ids, str(ids))

        print("\n[3] /v1/models и защита ключом")
        key = client.post(f"{BASE}/api/admin/keys", headers=H, json={}).json()["key"]
        r = client.get(f"{BASE}/v1/models")
        check("без ключа → 401", r.status_code == 401)
        r = client.get(f"{BASE}/v1/models", headers={"Authorization": f"Bearer {key}"})
        ids = [m["id"] for m in r.json()["data"]]
        check("публичный id содержит префикс", "my-site-gpt-test" in ids, str(ids))

        print("\n[4] чат через кастомное подключение (нестрим)")
        r = client.post(f"{BASE}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                        json={"model": "my-site-gpt-test",
                              "messages": [{"role": "user", "content": "привет"}]})
        check("HTTP 200", r.status_code == 200, r.text[:300])
        check("текст от мока", r.json()["choices"][0]["message"]["content"] == "[gpt-test] ответ мока",
              r.text[:200])
        check("ключ уходит в апстрим",
              client.get(f"{MOCK_CUSTOM}/v1/anything").json()["auth"] == "Bearer sk-custom-123")

        print("\n[5] стрим через кастомное подключение")
        chunks = []
        with client.stream("POST", f"{BASE}/v1/chat/completions",
                           headers={"Authorization": f"Bearer {key}"},
                           json={"model": "my-site-claude-test", "stream": True,
                                 "messages": [{"role": "user", "content": "привет"}]}) as resp:
            for line in resp.iter_lines():
                if line.startswith("data: ") and line[6:] != "[DONE]":
                    chunks.append(json.loads(line[6:]))
        text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks
                       if c["choices"] and c["choices"][0].get("delta"))
        reasoning = "".join(c["choices"][0]["delta"].get("reasoning_content", "") for c in chunks
                            if c["choices"] and c["choices"][0].get("delta"))
        check("стрим собрал текст", text == "[claude-test] ответ мока", repr(text))
        check("reasoning_content прошёл", "думаю" in reasoning)

        print("\n[6] passthrough /p/<имя>/… — обход CORS")
        r = client.get(f"{BASE}/p/my-site/anything", headers={"x-hub-key": key, "X-Custom": "abc"})
        check("passthrough отвечает", r.status_code == 200, r.text[:200])
        check("CORS-заголовки выставлены",
              r.headers.get("access-control-allow-origin") == "*")
        check("клиентские заголовки проброшены", r.json().get("custom_header") == "abc")

        print("\n[7] Genspark: аккаунты + регресс дублей")
        client.post(f"{BASE}/api/admin/settings", headers=H, json={"settings": {
            "provider_settings": {"genspark": {"api_base": MOCK_GS, "cookie_check_interval": 0}}}})
        r = client.post(f"{BASE}/api/admin/accounts", headers=H, json={
            "provider": "genspark", "fields": {"cookie": "session_id=goodcookie-A"}, "label": "g"})
        check("genspark-аккаунт добавлен", r.status_code == 200, r.text[:200])
        gs_models = [m["id"] for m in client.get(f"{BASE}/api/admin/models", headers=H).json()["models"]
                     if m["prefix"] == "genspark"]
        check("сид-каталог genspark засеян", "genspark-claude-opus-5" in gs_models, str(len(gs_models)))
        r = client.post(f"{BASE}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                        json={"model": "genspark-gpt-5.4",
                              "messages": [{"role": "user", "content": "Привет!"}]})
        content = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        check("через префикс роутится в genspark",
              content == "[gpt-5.4] Привет, мир!", repr(content[:80]))
        client.post(f"{MOCK_GS}/mock/reset")
        r = client.post(f"{BASE}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                        json={"model": "genspark-gpt-5.4", "messages": [{"role": "user", "content": [
                            {"type": "text", "text": "Что на изображении?"},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
                        ]}]})
        gs_payloads = client.get(f"{MOCK_GS}/mock/state").json()
        check("вложения Genspark идут в актуальный AI Chat",
              r.status_code == 200 and gs_payloads.get("agent") is not None
              and gs_payloads.get("copilot") is None, r.text[:160])
        client.post(f"{MOCK_GS}/mock/reset")
        r = client.post(f"{BASE}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                        json={"model": "genspark-gpt-5.4", "messages": [{"role": "user", "content": [
                            {"type": "text", "text": "Прочитай документ"},
                            {"type": "image_url", "filename": "sample.pdf",
                             "image_url": {"url": "data:application/pdf;base64,JVBERi0xLjQ="}},
                        ]}]})
        gs_payloads = client.get(f"{MOCK_GS}/mock/state").json()
        rich = ((gs_payloads.get("agent") or {}).get("messages") or [{}])[0].get("content") or []
        check("документы Genspark загружаются как private_file",
              r.status_code == 200 and gs_payloads.get("uploaded", 0) > 0
              and any(x.get("type") == "private_file" for x in rich if isinstance(x, dict)),
              r.text[:160])
        # регресс дублей: дельты + кумулятивный снимок + чужое поле
        for a in client.get(f"{BASE}/api/admin/accounts", headers=H).json()["accounts"]:
            if a["provider"] == "genspark":
                client.delete(f"{BASE}/api/admin/accounts/{a['id']}", headers=H)
        client.post(f"{BASE}/api/admin/accounts", headers=H, json={
            "provider": "genspark", "fields": {"cookie": "session_id=dupcookie"}})
        r = client.post(f"{BASE}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                        json={"model": "genspark-gpt-5.4",
                              "messages": [{"role": "user", "content": "Привет!"}]})
        content = r.json()["choices"][0]["message"]["content"]
        check("ответ не задублирован", content == "Привет! Чем я могу помочь вам сегодня?", repr(content[:80]))

        print("\n[8] маршрутизация без префикса и ошибки")
        r = client.post(f"{BASE}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                        json={"model": "нет-такой-модели",
                              "messages": [{"role": "user", "content": "x"}]})
        check("неизвестная модель без default → 503/404",
              r.status_code in (404, 503), f"{r.status_code} {r.text[:120]}")
        client.post(f"{BASE}/api/admin/settings", headers=H,
                    json={"settings": {"default_provider": "custom"}})
        r = client.post(f"{BASE}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                        json={"model": "gpt-test", "messages": [{"role": "user", "content": "x"}]})
        check("непрефиксованная модель ушла в default_provider",
              r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "[gpt-test] ответ мока",
              r.text[:200])
        client.post(f"{BASE}/api/admin/settings", headers=H,
                    json={"settings": {"default_provider": ""}})

        print("\n[9] oauth-эндпоинты существуют")
        r = client.post(f"{BASE}/api/admin/oauth/kiro/start", headers=H)
        j = r.json()
        check("kiro oauth запускается (или честная ошибка порта)",
              r.status_code in (200, 500), r.text[:150])
        if r.status_code == 200:
            st = client.get(f"{BASE}/api/admin/oauth/kiro/status/{j['flow_id']}", headers=H).json()
            check("статус flow отдаётся", st.get("status") in ("waiting", "error", "done"), json.dumps(st)[:120])

        print("\n[10] логи")
        logs = client.get(f"{BASE}/api/admin/logs", headers=H).json()["logs"]
        check("логи пишутся", len(logs) > 0 and any(l["status"] == 200 for l in logs))

        print("\n[11] мост MemuAI ↔ Proxy")
        r = client.get(f"{PG_BASE}/api/pg/hub")
        check("MemuAI видит запущенный Proxy", r.status_code == 200 and r.json().get("online"), r.text)
        started = time.perf_counter()
        r = client.get(f"{PG_BASE}/api/pg/state")
        elapsed = time.perf_counter() - started
        check("состояние MemuAI загружается без внешней задержки",
              r.status_code == 200 and elapsed < 2.0, f"{r.status_code}, {elapsed:.2f}s")
        returned_providers = {m["provider"] for m in r.json().get("models", [])}
        check("MemuAI показывает модели только добавленных провайдеров",
              returned_providers <= {"custom", "genspark", "Genspark · AI Image"}, str(returned_providers))
        check("Genspark AI Image отделён от обычного чата",
              any(m["id"].startswith("genspark-image/") and m["caps"].get("image")
                  for m in r.json().get("models", [])))
        chunks = []
        with client.stream("POST", f"{PG_BASE}/api/pg/chat", json={
            "model": "my-site-claude-test",
            "messages": [{"role": "user", "content": "bridge"}],
        }) as resp:
            for line in resp.iter_lines():
                if line.startswith("data: ") and line[6:] != "[DONE]":
                    chunks.append(json.loads(line[6:]))
        text = "".join(c.get("choices", [{}])[0].get("delta", {}).get("content", "")
                       for c in chunks if c.get("choices"))
        check("SSE-стрим доходит до MemuAI без JSON-ошибки",
              text == "[claude-test] ответ мока", repr(text))
        pg_logs = client.get(f"{BASE}/api/admin/logs", headers=H).json()["logs"]
        check("запросы из MemuAI попадают в логи Proxy",
              any(x.get("model") == "my-site-claude-test" and x.get("status") == 200
                  for x in pg_logs))
        schedules = client.get(f"{PG_BASE}/api/pg/schedules")
        check("планировщик возвращает задачи и результаты",
              schedules.status_code == 200 and isinstance(schedules.json().get("results"), list),
              schedules.text[:160])
        # Агенты: в обычном чате их нет вовсе, в Work и MemuCode — только по просьбе пользователя.
        removed = client.post(f"{PG_BASE}/api/pg/agents/run", json={"query": "x", "primary_model": "my-site-gpt-test"})
        check("прохода агентов перед ответом чата больше нет", removed.status_code in (404, 405), str(removed.status_code))

        def agent_tool(tool, args=None, **extra):
            return client.post(f"{PG_BASE}/api/pg/tools", json={
                "tool": tool, "args": args or {}, "session": "smoke-agents", "model": "my-site-gpt-test", **extra}).json()

        spawn = {"title": "Проверка", "task": "Проверь решение и верни отчёт"}
        in_chat = agent_tool("agent_spawn", spawn, mode="chat", agents_allowed=True)
        check("агентов нет в обычном чате", "only in Work and MemuCode" in str(in_chat.get("error")), str(in_chat)[:160])
        unasked = agent_tool("agent_spawn", spawn, mode="work", agents_allowed=False)
        check("агент не запускается, если пользователь о нём не просил",
              "only when the user asks" in str(unasked.get("error")), str(unasked)[:160])
        first = agent_tool("agent_spawn", spawn, mode="work", agents_allowed=True)
        second = agent_tool("agent_spawn", {**spawn, "title": "Вторая часть"}, mode="work", agents_allowed=True)
        check("агенты запускаются в фоне и сразу возвращают управление",
              first.get("status") == "running" and second.get("id") == 2 and first.get("can_write") is False,
              str(first)[:160])
        reports = []
        for _ in range(4):
            waited = agent_tool("agent_wait", {"timeout": 20}, mode="work")
            reports += waited.get("finished") or []
            if not waited.get("still_running"):
                break
        check("отчёты обоих агентов возвращаются основной модели",
              sorted(row["id"] for row in reports) == [1, 2] and all("ответ мока" in row.get("report", "") for row in reports),
              str(reports)[:200])
        listed = client.get(f"{PG_BASE}/api/work/agents", params={"session": "smoke-agents"}).json().get("agents", [])
        check("интерфейс видит состояние агентов чата",
              len(listed) == 2 and all(row["status"] == "done" and row["delivered"] for row in listed), str(listed)[:200])

        print("\n[12] настройки и единственный экземпляр")
        client.post(f"{BASE}/api/admin/settings", json={"settings": {
            "provider_settings": {"genspark": {"enable_search_default": True}},
        }})
        settings = client.get(f"{BASE}/api/admin/settings").json()["settings"]
        check("настройка веб-поиска сохраняется",
              settings["provider_settings"]["genspark"]["enable_search_default"] is True)
        pref = client.post(f"{PG_BASE}/api/pg/preferences", json={
            "web_search": True, "thinking": True, "start_proxy_with_memuai": False,
        })
        saved_pref = client.get(f"{PG_BASE}/api/pg/preferences").json()
        check("настройки MemuAI сохраняются в AppData",
              pref.status_code == 200 and saved_pref.get("web_search") is True
              and saved_pref.get("thinking") is True
              and saved_pref.get("start_proxy_with_memuai") is False)
        chats_body = {"chats": [{"id": "chat-test", "title": "Сохранённый чат",
                                  "messages": [{"role": "user", "content": "тест"}],
                                  "mode": "chat", "updated": 10}]}
        client.post(f"{PG_BASE}/api/pg/chats", json=chats_body)
        saved_chats = client.get(f"{PG_BASE}/api/pg/chats").json().get("chats", [])
        check("чаты сохраняются между перезагрузками интерфейса",
              len(saved_chats) == 1 and saved_chats[0].get("id") == "chat-test")
        picked = client.post(f"{PG_BASE}/api/work/pick", json={})
        projects = client.get(f"{PG_BASE}/api/work/projects").json()
        check("Work-проекты сохраняются",
              picked.status_code == 200 and projects.get("active_id")
              and any(x.get("path") == data_dir for x in projects.get("items", [])))
        from app.desktop import acquire_instance_lock
        lock1 = acquire_instance_lock("MemuAITest")
        lock2 = acquire_instance_lock("MemuAITest")
        check("второй экземпляр блокируется", lock1 is not None and lock2 is None)
        if lock1:
            lock1.close()
        lock3 = acquire_instance_lock("MemuAITest")
        check("lock освобождается после выхода", lock3 is not None)
        if lock3:
            lock3.close()
        from app.desktop import DesktopShell
        class FakeWindow:
            def __init__(self):
                self.hidden = False
                self.destroyed = False
            def hide(self):
                self.hidden = True
            def destroy(self):
                self.destroyed = True
        shell = DesktopShell("http://127.0.0.1", close_to_tray=True, show_tray=False)
        shell.window = FakeWindow()
        closed = shell._on_closing() is True
        check("без доступного трея крестик завершает Proxy", closed and not shell.window.hidden)
        shell.request_quit()
        check("явная команда выключения завершает окно", shell.window.destroyed and shell.quit_event.is_set())

    print(f"\n{'=' * 40}\nИТОГО: пройдено {PASSED}, провалено {FAILED}")
    srv1.should_exit = srv2.should_exit = srv3.should_exit = srv4.should_exit = True
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
