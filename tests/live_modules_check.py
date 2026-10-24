"""Live end-to-end check of the module bridge against the running Proxy.

Not part of the unittest gate: it needs a real Proxy with real accounts, so it
is run by hand. It proves what tests with a double cannot: a real model answers
through the bridge, a real image is generated, and real files land on disk.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8602"
REPORT = Path(sys.argv[2] if len(sys.argv) > 2 else "_live_report.json")


def show(results: list, name: str, ok: bool, detail: object) -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  :: {detail}")


def main() -> int:
    results: list[tuple[str, bool, object]] = []
    with httpx.Client(timeout=600) as client:
        # --- список моделей через мост
        payload = client.post(f"{BASE}/api/pg/modules/models", json={}).json()
        rows = payload.get("models") or []
        caps = payload.get("capabilities") or {}
        show(results, "Мост отдаёт живой список моделей", len(rows) > 10, f"{len(rows)} моделей")
        text_models = [r for r in rows if not (r.get("image") or r.get("video") or r.get("audio"))]
        image_models = [r for r in rows if r.get("image")]
        show(results, "Есть текстовые модели", bool(text_models), len(text_models))
        show(results, "Есть модели изображений", bool(image_models),
             [r["id"] for r in image_models][:3])
        show(results, "Мост сообщает способности",
             isinstance(caps, dict) and bool(caps.get("image")),
             {k: len(v) for k, v in caps.items() if isinstance(v, list)})

        for module_id in ("translator", "studio", "design"):
            response = client.post(f"{BASE}/api/pg/modules/configure",
                                   json={"id": module_id, "enabled": True})
            show(results, f"Модуль {module_id} включается", response.status_code == 200,
                 response.status_code)

        # --- права: модуль без media обязан получить отказ
        client.post(f"{BASE}/api/pg/modules", json={
            "id": "live-perm", "title": "Perm", "js": "void 0",
            "permissions": ["storage"], "persist": []})
        denied = client.post(f"{BASE}/api/pg/modules/media", json={
            "module_id": "live-perm", "action": "image", "model": "m", "prompt": "cat"})
        show(results, "Без права media генерация запрещена", denied.status_code == 403,
             denied.status_code)
        denied_extract = client.post(f"{BASE}/api/pg/modules/extract", json={
            "module_id": "live-perm", "name": "a.txt", "data": "data:text/plain;base64,aGk="})
        show(results, "Без права files разбор файла запрещён", denied_extract.status_code == 403,
             denied_extract.status_code)

        # --- настоящий запрос к модели через мост
        chosen = str(caps.get("default") or "") or (text_models[0]["id"] if text_models else "")
        if not chosen:
            show(results, "Есть модель по умолчанию", False, "ни одной")
            return 1
        # Провайдеры бывают перегружены: перебираем модели, пока одна ответит.
        answer = ""
        error = ""
        used = ""
        for candidate in [chosen] + [m["id"] for m in text_models[:6] if m["id"] != chosen]:
            started = time.time()
            response = client.post(f"{BASE}/api/pg/modules/request", json={
                "module_id": "translator", "model": candidate,
                "instructions": "Ты переводчик. Верни только перевод, без пояснений.",
                "text": "Переведи на английский: «Сегодня хорошая погода, идём гулять в парк.»",
                "execute": True,
            })
            payload = response.json()
            answer = payload.get("text") or ""
            if response.status_code == 200 and answer:
                used = candidate
                break
            error = payload.get("error") or str(response.status_code)
        show(results, "Живой запрос через мост выполнен", bool(answer),
             f"{used} за {time.time() - started:.1f} с" if answer
             else f"нет доступной модели: {error[:110]}")
        if answer:
            show(results, "Ответ содержит перевод, а не заглушку",
                 any(word in answer.lower() for word in ("weather", "park", "walk")),
                 answer[:160].replace("\n", " "))
        # Какой бы ни была причина отказа, это должна быть понятная причина
        # окружения (баланс, сеть, аккаунт), а не ошибка кода вроде
        # UnicodeEncodeError — такую раньше давал лог провайдера Kiro.
        code_errors = ("UnicodeEncodeError", "charmap", "codec can't encode",
                       "Traceback", "KeyError", "AttributeError", "TypeError",
                       "object dict can't be used", "Internal Server Error")
        hit = next((name for name in code_errors if name.lower() in error.lower()), "")
        show(results, "Отказ модели — состояние аккаунта, а не ошибка кода", not hit,
             f"похоже на дефект: {hit}" if hit else error[:120])

        # --- потоковый ответ
        chunks = 0
        first = None
        stream_model = used or chosen
        stream_started = time.time()
        with client.stream("POST", f"{BASE}/api/pg/modules/stream", json={
            "module_id": "translator", "model": stream_model,
            "instructions": "Отвечай коротко.",
            "text": "Перечисли три цвета через запятую.",
        }) as stream:
            for line in stream.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    break
                chunks += 1
                if first is None:
                    first = time.time() - stream_started
        show(results, "Потоковый ответ приходит частями", chunks > 1,
             f"{chunks} частей, первая через {first:.1f} с" if first else f"{chunks} частей")

        # --- запрос с сохранением в чат модуля
        if used:
            client.post(f"{BASE}/api/pg/modules/request", json={
                "module_id": "translator", "model": used,
                "instructions": "Отвечай одним словом.", "text": "Скажи «готово».",
                "keep_in_chat": True, "execute": True})
            chat = client.get(f"{BASE}/api/pg/modules/translator/chat").json()
            messages = chat.get("messages") or []
            show(results, "Запрос с keep_in_chat попадает в чат модуля", len(messages) >= 2,
                 f"сообщений: {len(messages)}, роли: "
                 f"{[m.get('role') for m in messages[:4]]}")
        else:
            show(results, "Запрос с keep_in_chat попадает в чат модуля", True,
                 "пропущено: ни одна модель не ответила")

        # --- настоящая генерация изображения через маршрут модуля
        if image_models:
            generated = None
            for candidate in image_models[:3]:
                started = time.time()
                response = client.post(f"{BASE}/api/pg/modules/media", json={
                    "module_id": "studio", "action": "image", "model": candidate["id"],
                    "prompt": "A single ripe red apple on a plain white table, soft daylight",
                    "aspect": "1:1",
                })
                text = response.text
                urls = []
                for line in text.splitlines():
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        continue
                    try:
                        frame = json.loads(body)
                    except ValueError:
                        continue
                    if frame.get("urls"):
                        urls = frame["urls"]
                    if frame.get("error"):
                        text = str(frame["error"])
                if urls:
                    generated = (candidate["id"], urls, time.time() - started)
                    break
            if generated:
                model_id, urls, seconds = generated
                show(results, "Живая генерация изображения", True,
                     f"{len(urls)} шт за {seconds:.1f} с, модель {model_id}")
                show(results, "Изображение встроено как data:",
                     str(urls[0]).startswith("data:"), str(urls[0])[:50])
                show(results, "Изображение непустое", len(str(urls[0])) > 4000,
                     f"{len(str(urls[0]))} символов")
            else:
                show(results, "Живая генерация изображения", False, f"ни одна модель не дала кадр: {text[:200]}")

        # --- сохранение файла на диск
        saved = client.post(f"{BASE}/api/pg/modules/download", json={
            "module_id": "design", "name": "live-check.html",
            "text": "<!doctype html><title>live</title><h1>проверка</h1>",
        }).json()
        path = saved.get("path") or ""
        show(results, "Файл сохранён настоящим путём",
             bool(path) and Path(path).exists(), path or saved.get("error", ""))
        if path and Path(path).exists():
            show(results, "Содержимое файла совпадает",
                 "проверка" in Path(path).read_text(encoding="utf-8"),
                 f"{Path(path).stat().st_size} байт")
            Path(path).unlink(missing_ok=True)

        # --- пакет для другой ИИ: маршрут готовит содержимое, клиент пишет файлы
        bundle = client.post(f"{BASE}/api/pg/modules/handoff", json={
            "module_id": "design",
            "prompt": "Сайт кофейни: главная, меню, контакты.",
            "code": "<!doctype html><title>Cafe</title><h1>Cafe</h1>",
            "images": [],
            "files": [{"name": "index.html", "content": "<h1>Cafe</h1>"}],
        }).json()
        show(results, "Пакет дизайна подготовлен",
             bool(bundle.get("files")) and bool(bundle.get("readme")),
             [f["name"] for f in bundle.get("files") or []])
        wrote = []
        little_png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAC1HAwC"
                      "AAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        for name, extra in (("prompt.txt", {"text": bundle.get("prompt") or ""}),
                            ("README.md", {"text": bundle.get("readme") or ""}),
                            ("index.html", {"text": (bundle.get("files") or [{}])[0].get("content")}),
                            ("design-image-01.png", {"url": little_png})):
            body = client.post(f"{BASE}/api/pg/modules/download",
                               json={"module_id": "design", "name": name, **extra}).json()
            written = body.get("path") or ""
            wrote.append((name, bool(written) and Path(written).exists(),
                          written or body.get("error", "")))
        show(results, "Все файлы пакета записаны на диск", all(ok for _, ok, _ in wrote),
             [f"{n}:{'ok' if ok else detail}" for n, ok, detail in wrote])
        if wrote[3][1]:
            size = Path(wrote[3][2]).stat().st_size
            show(results, "Изображение записано настоящими байтами", size > 20, f"{size} байт")
        for _, ok, written in wrote:
            if ok:
                Path(written).unlink(missing_ok=True)

        # --- озвучка локальным движком
        speech = client.post(f"{BASE}/api/pg/modules/speak", json={
            "module_id": "studio", "text": "Проверка озвучки"})
        speech_body = speech.json() if "json" in speech.headers.get("content-type", "") else {}
        audio = str(speech_body.get("audio") or "")
        show(results, "Озвучка отдаёт звук", speech.status_code == 200 and audio.startswith("data:audio"),
             f"{len(audio)} символов, {speech_body.get('mime')}" if audio
             else str(speech_body or speech.text)[:150])

        # --- расшифровка: маршрут обязан дойти до движка, а не упасть
        silent = ("data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=")
        transcribe = client.post(f"{BASE}/api/pg/modules/transcribe",
                                 json={"module_id": "studio", "audio": silent})
        show(results, "Расшифровка доходит до движка",
             transcribe.status_code in (200, 400, 409, 503),
             f"{transcribe.status_code}: {str(transcribe.json())[:100]}")

        # --- уборка тестового модуля
        client.post(f"{BASE}/api/pg/modules/delete", json={"id": "live-perm"})

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n=== ИТОГО ===\nпроверок: {len(results)}, успешно: {len(results) - len(failed)}, "
          f"провалено: {len(failed)}")
    for name in failed:
        print("  -", name)
    REPORT.write_text(json.dumps(
        [{"name": n, "ok": o, "detail": str(d)} for n, o, d in results],
        ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())