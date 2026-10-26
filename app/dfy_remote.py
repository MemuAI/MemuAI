"""Удалённые машины DFY (Раунд 30, пункт 1а).

DFY перестаёт быть «только браузером»: кроме браузера агент работает со всей
машиной — командами оболочки, файлами, пакетами, процессами — на этом
компьютере и на удалённом сервере.

Транспорты к одному и тому же протоколу (``app/dfy_agent.py``):

* ``LocalTransport``   — «Этот компьютер»: ``Agent`` вызывается прямо в процессе
  MemuAI (тот же Guard, те же подтверждения);
* ``HttpTransport``    — машина с запущенным ``MemuAI Remote Agent`` (подпись
  HMAC, стрим вывода, отмена, TCP-туннель к CDP браузера машины);
* ``SshTransport``     — без установки агента: системный ``ssh`` запускает на
  сервере ``python3 -c <загрузчик>``, исходник агента идёт в stdin (режим ``once``).

Машины и их токены лежат в ``DATA_DIR/memuai/dfy_machines.json``; токен
шифруется тем же хранилищем, что и секреты DFY (``SecretVault``), поэтому в
файле его нет в открытом виде и модель его не видит.

В каждый вызов MemuAI добавляет ``protect`` — свои пути (папка приложения и
данные): агент на той же машине, что и MemuAI, их не тронет, даже если запущен
копией из другого каталога. Агент дополнительно сам узнаёт папку MemuAI
(``Guard.memuai_tree``).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .dfy_agent import AGENT_VERSION, Agent, Guard, NeedsConfirm, Refused, parse_pairing_code, sign

MACHINES_FILE = "dfy_machines.json"
LOCAL_ID = "local"
BROWSER_SUFFIX = "/browser"
OS_LABELS = {"linux": "Linux", "windows": "Windows", "darwin": "macOS", "": ""}
ONLINE_TTL = 90.0
AGENT_FILE = "memuai_remote_agent.py"

class RemoteError(RuntimeError):
    """Ошибка связи/настройки машины: текст на обоих языках."""

    def __init__(self, ru: str, en: str = "") -> None:
        super().__init__(ru)
        self.ru = ru
        self.en = en or ru

    def text(self, lang: str = "ru") -> str:
        return self.en if lang == "en" else self.ru

def agent_source() -> str:
    """Исходник агента — его отдают пользователю на скачивание и шлют по SSH."""
    return (Path(__file__).with_name("dfy_agent.py")).read_text("utf-8")

def install_command() -> Dict[str, str]:
    """Команды установки агента на сервер (Linux/macOS).

    MemuAI слушает только 127.0.0.1, поэтому сервер не может скачать агента у
    него по ``curl``.  Вместо этого:

    * ``paste``   — одна самодостаточная строка: исходник агента (gzip+base64)
      записывается в ``~/memuai_remote_agent.py`` и сразу запускается — нужны
      только ``base64``, ``gunzip`` и ``python3``, интернет на сервере не нужен;
    * ``scp``     — файл скачан кнопкой «Скачать агента» и копируется с этого ПК;
    * ``curl``    — если файл агента лежит по вашей ссылке (``<URL>``);
    * ``systemd`` — служба: агент переживает перезагрузку, код подключения
      печатает последняя команда цепочки.
    """
    import base64
    import gzip
    packed = base64.b64encode(gzip.compress(agent_source().encode("utf-8"), 9, mtime=0)).decode("ascii")
    run = f"python3 ~/{AGENT_FILE}"
    return {
        "paste": f"echo {packed} | base64 -d | gunzip > ~/{AGENT_FILE} && {run} serve",
        "scp": f"scp {AGENT_FILE} USER@SERVER:~/ && ssh USER@SERVER {run} serve",
        "curl": f"curl -fsSL <URL>/{AGENT_FILE} | python3 - serve",
        "serve": f"{run} serve",
        "systemd": (f"{run} systemd | sudo tee /etc/systemd/system/memuai-agent.service >/dev/null && "
                    "sudo systemctl daemon-reload && sudo systemctl enable --now memuai-agent && "
                    f"{run} code"),
        "file": AGENT_FILE,
        "size": str(len(packed)),
    }

def proxy_env(upstream: Any) -> Dict[str, str]:
    """Переменные окружения прокси DFY для команд на удалённой машине (curl, pip, git, wget…).

    ``upstream`` — ``app.dfy_proxy.Upstream`` или ``None``.  SOCKS5 отдаётся как
    ``socks5h://`` — имена сайтов разрешает прокси, а не машина."""
    if upstream is None:
        return {}
    from urllib.parse import quote
    auth = ""
    if upstream.username or upstream.password:
        auth = f"{quote(upstream.username, safe='')}:{quote(upstream.password, safe='')}@"
    scheme = "socks5h" if upstream.scheme == "socks5" else upstream.scheme
    host = f"[{upstream.host}]" if ":" in upstream.host else upstream.host
    url = f"{scheme}://{auth}{host}:{int(upstream.port)}"
    env = {name: url for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")}
    env.update(NO_PROXY="localhost,127.0.0.1,::1", no_proxy="localhost,127.0.0.1,::1")
    return env

def _clean_name(value: Any, fallback: str = "") -> str:
    name = re.sub(r"\s+", " ", str(value or "").strip())[:60]
    return name or fallback

class MachineStore:
    """Список машин: метаданные открытым текстом, токены — в зашифрованном хранилище."""

    def __init__(self, root: Path, vault: Any) -> None:
        self.root = Path(root)
        self.path = self.root / MACHINES_FILE
        self.vault = vault

    def _read(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {"machines": []}
        return data if isinstance(data, dict) and isinstance(data.get("machines"), list) else {"machines": []}

    def _write(self, data: Dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def list(self) -> List[Dict[str, Any]]:
        rows = []
        for row in self._read()["machines"]:
            if isinstance(row, dict) and row.get("id"):
                rows.append(self.public(row))
        return rows

    def get(self, machine_id: str) -> Optional[Dict[str, Any]]:
        wanted = str(machine_id or "")
        return next((row for row in self._read()["machines"]
                     if isinstance(row, dict) and row.get("id") == wanted), None)

    @staticmethod
    def public(row: Dict[str, Any]) -> Dict[str, Any]:
        """Строка для UI и для модели: без токена и пароля."""
        seen = float(row.get("seen") or 0)
        return {"id": row["id"], "name": row.get("name") or row["id"], "kind": row.get("kind") or "http",
                "host": row.get("host", ""), "port": row.get("port", 0), "os": row.get("os", ""),
                "os_label": OS_LABELS.get(row.get("os", ""), str(row.get("os") or "")),
                "user": row.get("user", ""), "roots": row.get("roots") or [],
                "agent_version": row.get("agent_version", ""), "seen": seen,
                "online": bool(seen and time.time() - seen < ONLINE_TTL),
                "error": row.get("error", ""), "created": row.get("created", 0),
                "default_cwd": row.get("default_cwd", "")}

    def add(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(payload.get("kind") or "").strip()
        code = str(payload.get("code") or "").strip()
        row: Dict[str, Any] = {"id": uuid.uuid4().hex[:10], "created": time.time()}
        if code:
            try:
                parsed = parse_pairing_code(code)
            except ValueError as exc:
                raise RemoteError(str(exc), "The pairing code is damaged") from None
            row.update({"kind": "http", "host": parsed["host"], "port": parsed["port"], "os": parsed["os"],
                        "tls": parsed["tls"], "name": _clean_name(payload.get("name"), parsed["name"] or parsed["host"])})
            self._put_token(row, parsed["token"])
        elif kind == "ssh":
            host = str(payload.get("host") or "").strip()
            if not host:
                raise RemoteError("Укажите адрес сервера", "Enter the server address")
            row.update({"kind": "ssh", "host": host, "port": int(payload.get("port") or 22),
                        "user": _clean_name(payload.get("user")), "os": "linux",
                        "key_path": str(payload.get("key_path") or "").strip()[:400],
                        "name": _clean_name(payload.get("name"), host)})
            if payload.get("password"):
                self._put_token(row, str(payload["password"]), label="ssh")
        else:
            host = str(payload.get("host") or "").strip()
            if not host:
                raise RemoteError("Вставьте код подключения из агента или укажите адрес",
                                  "Paste the pairing code from the agent or enter an address")
            row.update({"kind": "http", "host": host, "port": int(payload.get("port") or 8765),
                        "tls": bool(payload.get("tls")), "name": _clean_name(payload.get("name"), host)})
            if payload.get("token"):
                self._put_token(row, str(payload["token"]))
        roots = payload.get("roots")
        if isinstance(roots, list):
            row["roots"] = [str(r)[:400] for r in roots if str(r).strip()][:12]
        if payload.get("default_cwd"):
            row["default_cwd"] = str(payload["default_cwd"])[:400]
        data = self._read()
        data["machines"].append(row)
        self._write(data)
        return self.public(row)

    def update(self, machine_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        data = self._read()
        row = next((r for r in data["machines"] if isinstance(r, dict) and r.get("id") == machine_id), None)
        if row is None:
            raise RemoteError("Машина не найдена", "Machine not found")
        for key in ("host", "default_cwd", "user", "key_path"):
            if key in patch:
                row[key] = str(patch[key])[:400]
        if "name" in patch:
            row["name"] = _clean_name(patch["name"], row.get("name") or row["id"])
        if "port" in patch:
            row["port"] = max(1, min(65535, int(patch["port"] or 0) or row.get("port") or 8765))
        if isinstance(patch.get("roots"), list):
            row["roots"] = [str(r)[:400] for r in patch["roots"] if str(r).strip()][:12]
        if patch.get("code"):
            parsed = parse_pairing_code(str(patch["code"]))
            row.update({"host": parsed["host"], "port": parsed["port"], "os": parsed["os"], "tls": parsed["tls"]})
            self._put_token(row, parsed["token"])
        elif patch.get("token"):
            self._put_token(row, str(patch["token"]))
        elif patch.get("password"):
            self._put_token(row, str(patch["password"]), label="ssh")
        self._write(data)
        return self.public(row)

    def note(self, machine_id: str, **fields: Any) -> None:
        """Отметить машину после вызова (онлайн, ОС, версия агента, ошибка)."""
        data = self._read()
        row = next((r for r in data["machines"] if isinstance(r, dict) and r.get("id") == machine_id), None)
        if row is None:
            return
        for key, value in fields.items():
            if value is None:
                row.pop(key, None)
            else:
                row[key] = value
        self._write(data)

    def delete(self, machine_id: str) -> bool:
        data = self._read()
        rows = [r for r in data["machines"] if not (isinstance(r, dict) and r.get("id") == machine_id)]
        if len(rows) == len(data["machines"]):
            return False
        data["machines"] = rows
        self._write(data)
        return True

    def _put_token(self, row: Dict[str, Any], token: str, *, label: str = "agent") -> None:
        row["secret"] = self.vault._encrypt(token.encode("utf-8"))
        row["secret_kind"] = label

    def token(self, machine_id: str) -> str:
        row = self.get(machine_id)
        if not row or not row.get("secret"):
            return ""
        try:
            return self.vault._decrypt(row["secret"]).decode("utf-8")
        except Exception:
            return ""

class Transport:
    """Общий интерфейс: одна операция агента → результат."""

    kind = "none"
    supports_browser = False

    async def call(self, op: str, params: Dict[str, Any], *,
                   on_chunk: Optional[Callable[[str], None]] = None, timeout: float = 180.0) -> Dict[str, Any]:
        raise NotImplementedError

    async def close(self) -> None:
        return None

class LocalTransport(Transport):
    """«Этот компьютер»: тот же ``Agent`` в процессе MemuAI."""

    kind = "local"
    supports_browser = False

    def __init__(self, *, protect: Optional[List[str]] = None, roots: Optional[List[str]] = None) -> None:
        self.agent = Agent(Guard(protect=protect, roots=roots))

    async def call(self, op: str, params: Dict[str, Any], *,
                   on_chunk: Optional[Callable[[str], None]] = None, timeout: float = 180.0) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()

        def chunk(_job: str, text: str) -> None:

            loop.call_soon_threadsafe(on_chunk, text)

        home = str(Path.home())
        if op == "exec" and not params.get("cwd"):

            params = {**params, "cwd": home}

        def run() -> Dict[str, Any]:
            result = self.agent.call(op, params, chunk if on_chunk else None)
            if op == "sysinfo":
                result = {**result, "cwd": home}
            return result
        return await asyncio.to_thread(run)

class HttpTransport(Transport):
    """Машина с запущенным агентом: HTTP + подпись HMAC, стрим NDJSON."""

    kind = "http"
    supports_browser = True

    def __init__(self, row: Dict[str, Any], token: str) -> None:
        self.row = row
        self.token = token
        scheme = "https" if row.get("tls") else "http"
        self.base = f"{scheme}://{row.get('host')}:{int(row.get('port') or 8765)}"

    async def call(self, op: str, params: Dict[str, Any], *,
                   on_chunk: Optional[Callable[[str], None]] = None, timeout: float = 180.0) -> Dict[str, Any]:
        import httpx
        body = json.dumps({"op": op, "params": params or {}}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "X-Memu-Agent-Auth": sign(self.token, body)}
        path = "/stream" if on_chunk else "/call"
        try:
            async with httpx.AsyncClient(timeout=timeout, verify=False, trust_env=False) as client:
                if not on_chunk:
                    response = await client.post(self.base + path, content=body, headers=headers)
                    return _unwrap(response.json() if response.content else {}, response.status_code)
                async with client.stream("POST", self.base + path, content=body, headers=headers) as response:
                    final: Dict[str, Any] = {}
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            message = json.loads(line)
                        except ValueError:
                            continue
                        if "chunk" in message:
                            on_chunk(str(message["chunk"]))
                        else:
                            final = message
                    return _unwrap(final, response.status_code)
        except httpx.HTTPError as exc:
            raise RemoteError(f"Нет связи с машиной: {exc}", f"No connection to the machine: {exc}") from None

class SshTransport(Transport):
    """Без установки агента: исходник уходит по SSH в stdin, на сервере он запускается
    в режиме ``once`` (одна операция, ответ — NDJSON). Нужен только ``python3`` на сервере.

    Вход — ключом или ssh-agent (``BatchMode``: пароль не спрашивается в консоли), либо
    сохранённым паролем через ``SSH_ASKPASS``: сам пароль ssh получает из переменной окружения
    дочернего процесса, в командной строке и в файлах его нет.
    Закрытие SSH (отмена задачи) закрывает stdin агента, и он останавливает команду."""

    kind = "ssh"
    supports_browser = False

    BOOTSTRAP = ("import sys,os;n=int(sys.stdin.buffer.readline());s=sys.stdin.buffer.read(n).decode('utf-8');"
                 "h=os.path.expanduser('~/.memuai-agent');os.makedirs(h,exist_ok=True);"
                 "g={'__name__':'memuai_agent','__file__':os.path.join(h,'memuai_remote_agent.py')};"
                 "exec(compile(s,'memuai_remote_agent.py','exec'),g);sys.exit(g['main'](['once']+sys.argv[1:]))")

    def __init__(self, row: Dict[str, Any], password: str = "") -> None:
        self.row = row
        self.password = password

    def command(self, roots: Optional[List[str]] = None) -> List[str]:
        import shlex
        binary = os.environ.get("MEMUAI_SSH_BINARY") or shutil.which("ssh")
        if not binary:
            raise RemoteError("На этом компьютере нет клиента ssh — установите OpenSSH или используйте агента",
                              "No ssh client on this computer — install OpenSSH or use the agent")
        login = ["-o", "NumberOfPasswordPrompts=1"] if self.password else ["-o", "BatchMode=yes"]
        command = [binary, *login, "-o", "StrictHostKeyChecking=accept-new",
                   "-o", "ConnectTimeout=15", "-p", str(int(self.row.get("port") or 22))]
        if self.row.get("key_path"):
            command += ["-i", str(self.row["key_path"])]
        host = str(self.row.get("host") or "")
        user = str(self.row.get("user") or "")
        command.append(f"{user}@{host}" if user else host)
        remote = ["python3", "-c", self.BOOTSTRAP]
        for root in roots or self.row.get("roots") or []:
            remote += ["--root", str(root)]
        command.append(" ".join(shlex.quote(part) for part in remote))
        return command

    def askpass_env(self) -> Optional[Dict[str, str]]:
        """Окружение ssh для входа по паролю: ``SSH_ASKPASS`` — крошечный скрипт, печатающий
        ``MEMUAI_SSH_PASSWORD``; ``SSH_ASKPASS_REQUIRE=force`` (OpenSSH 8.4+, в т.ч. Windows 10/11)."""
        if not self.password:
            return None
        import tempfile
        folder = Path(tempfile.gettempdir()) / "memuai-ssh"
        folder.mkdir(exist_ok=True)
        if os.name == "nt":
            helper = folder / "askpass.cmd"
            text = '@powershell -NoProfile -Command "[Console]::Out.WriteLine($env:MEMUAI_SSH_PASSWORD)"\r\n'
        else:
            helper = folder / "askpass.sh"
            text = '#!/bin/sh\nprintf \'%s\\n\' "$MEMUAI_SSH_PASSWORD"\n'
        if not helper.exists() or helper.read_bytes() != text.encode("utf-8"):
            helper.write_bytes(text.encode("utf-8"))
            helper.chmod(0o700)
        return {**os.environ, "SSH_ASKPASS": str(helper), "SSH_ASKPASS_REQUIRE": "force",
                "DISPLAY": os.environ.get("DISPLAY") or ":0", "MEMUAI_SSH_PASSWORD": self.password}

    async def call(self, op: str, params: Dict[str, Any], *,
                   on_chunk: Optional[Callable[[str], None]] = None, timeout: float = 180.0) -> Dict[str, Any]:
        params = dict(params or {})
        roots = params.pop("roots", None)
        source = agent_source().encode("utf-8")
        request = json.dumps({"op": op, "params": params}, ensure_ascii=False).encode("utf-8")
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command(roots), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=self.askpass_env())
        except OSError as exc:
            raise RemoteError(f"Не удалось запустить ssh: {exc}", f"Could not start ssh: {exc}") from None
        assert process.stdin is not None and process.stdout is not None
        final: Dict[str, Any] = {}
        try:
            process.stdin.write(b"%d\n" % len(source) + source + request + b"\n")
            await process.stdin.drain()
            deadline = time.monotonic() + timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise asyncio.TimeoutError
                line = await asyncio.wait_for(process.stdout.readline(), left)
                if not line:
                    break
                try:
                    message = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if "chunk" in message:
                    if on_chunk:
                        on_chunk(str(message["chunk"]))
                    continue
                final = message
                break
        except asyncio.TimeoutError:
            raise RemoteError("Машина не ответила вовремя", "The machine did not answer in time") from None
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                process.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.kill()
        if not final:
            error = b""
            if process.stderr is not None:
                try:
                    error = await asyncio.wait_for(process.stderr.read(), 2)
                except asyncio.TimeoutError:
                    pass
            text = error.decode("utf-8", "replace").strip()[-400:] or f"ssh exit {process.returncode}"
            raise RemoteError(f"SSH: {text}", f"SSH: {text}")
        return _unwrap(final, 409 if final.get("needs_confirm") else 200)

def _unwrap(payload: Dict[str, Any], status: int) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise RemoteError("Агент вернул не JSON", "The agent returned no JSON")
    if payload.get("ok"):
        result = payload.get("result")
        return result if isinstance(result, dict) else {"result": result}
    message = str(payload.get("error") or f"HTTP {status}")
    message_en = str(payload.get("error_en") or message)
    if payload.get("needs_confirm") or status == 409:
        raise NeedsConfirm(message, message_en)
    raise Refused(message, message_en)

class RemoteMachines:
    """Фасад: машины + транспорты + вызовы операций для DFY."""

    def __init__(self, root: Path, vault: Any, *, protect: Optional[List[str]] = None) -> None:
        self.store = MachineStore(Path(root), vault)
        self.protect = list(protect or [])
        self._transports: Dict[str, Transport] = {}
        self.local = LocalTransport(protect=self._protected_paths())

    def _protected_paths(self) -> List[str]:
        """MemuAI и его данные: ни локальный режим, ни агент на этой же машине их не трогают."""
        paths = list(self.protect)
        paths.append(str(Path(__file__).resolve().parents[1]))
        paths.append(str(self.store.root))
        try:
            from .config import DATA_DIR
            paths.append(str(Path(DATA_DIR).resolve()))
        except Exception:
            pass
        return paths

    def targets(self, lang: str = "ru") -> List[Dict[str, Any]]:
        """Цели для выбора в чате: «Этот компьютер» + машины."""
        rows = [{"id": LOCAL_ID, "name": "Этот компьютер" if lang != "en" else "This computer",
                 "kind": "local", "os": "windows" if os.name == "nt" else "linux", "online": True}]
        machines = self.store.list()
        rows.extend(machines)

        rows.extend({**row, "id": row["id"] + BROWSER_SUFFIX, "machine": row["id"], "browser": True,
                     "name": f"{row['name']} — " + ("browser" if lang == "en" else "браузер")}
                    for row in machines if row.get("kind") == "http")
        return rows

    def transport(self, machine_id: str) -> Transport:
        wanted = str(machine_id or LOCAL_ID)
        if wanted in ("", LOCAL_ID, "this", "self"):
            return self.local
        cached = self._transports.get(wanted)
        if cached is not None:
            return cached
        row = self.store.get(wanted)
        if row is None:
            raise RemoteError("Машина не найдена — проверьте список в настройках DFY",
                              "Machine not found — check the list in DFY settings")
        kind = row.get("kind") or "http"
        if kind == "ssh":
            transport: Transport = SshTransport(row, self.store.token(wanted))
        else:
            token = self.store.token(wanted)
            if not token:
                raise RemoteError("У машины нет токена — добавьте её заново по коду подключения",
                                  "The machine has no token — add it again with a pairing code")
            transport = HttpTransport(row, token)
        self._transports[wanted] = transport
        return transport

    def forget_transport(self, machine_id: str) -> None:
        self._transports.pop(str(machine_id or ""), None)

    async def call(self, machine_id: str, op: str, params: Dict[str, Any], *,
                   on_chunk: Optional[Callable[[str], None]] = None, timeout: float = 180.0) -> Dict[str, Any]:
        transport = self.transport(machine_id)
        row = self.store.get(machine_id) if transport is not self.local else None
        if row is not None:

            params = {**params, "protect": self._protected_paths()}
        if row is not None and row.get("default_cwd") and op == "exec" and not params.get("cwd"):
            params = {**params, "cwd": row["default_cwd"]}
        if row is not None and row.get("roots") and op != "ping":
            params = {**params, "roots": row["roots"]}
        try:
            result = await transport.call(op, params, on_chunk=on_chunk, timeout=timeout)
        except (Refused, NeedsConfirm):
            raise
        except RemoteError as exc:
            if row is not None:
                self.store.note(machine_id, error=exc.ru)
            raise
        if row is not None:
            self.store.note(machine_id, seen=time.time(), error="")
        return result

    async def probe(self, machine_id: str) -> Dict[str, Any]:
        """Проверить связь и запомнить ОС/версию агента."""
        info = await self.call(machine_id, "sysinfo", {}, timeout=30)
        if machine_id not in ("", LOCAL_ID):
            self.store.note(machine_id, os=str(info.get("os") or "").lower(), seen=time.time(),
                            agent_version=str(info.get("agent_version") or AGENT_VERSION),
                            user=str(info.get("user") or ""), error="")
        return info

    async def close(self) -> None:
        for transport in list(self._transports.values()):
            await transport.close()
        self._transports.clear()

MACHINE_ACTIONS = """Machine actions (the target machine is in <target>; they work the same on this computer and on a remote server):
{"action":"shell","command":"ls -la /srv","cwd":"/srv","timeout":120,"reason":"..."}   (one shell command; its output comes back)
{"action":"file_read","path":"/etc/nginx/nginx.conf","reason":"..."}
{"action":"file_write","path":"/srv/app/.env","text":"...","append":false,"reason":"..."}
{"action":"file_list","path":"/srv","reason":"..."}
{"action":"file_delete","path":"/srv/old.log","recursive":false,"reason":"..."}
{"action":"file_move","source":"/srv/a","target":"/srv/b","reason":"..."}
{"action":"file_find","path":"/srv","pattern":"*.service","reason":"..."}
{"action":"download","url":"https://...","path":"/srv/app.tar.gz","reason":"..."}
{"action":"package","pkg_action":"install|remove|list|search|update","name":"nginx","manager":"apt-get","reason":"..."}
{"action":"process_list","query":"nginx","reason":"..."}
{"action":"process_kill","pid":1234,"force":false,"reason":"..."}
{"action":"sysinfo","reason":"..."}

Machine rules:
- Never work around the protections: the agent refuses anything that touches itself, MemuAI, its data folder or a system root (/, /etc, C:\\Windows), and refuses rm -rf /, formatting a disk and a fork bomb. If a step is refused, do not retry it in another wording — tell the user.
- Dangerous steps (recursive or forced delete, overwriting a file, removing a package, killing a process, reboot/shutdown, disk operations) need the user's go-ahead: just send the step itself — MemuAI shows the user the exact command and asks once. Do NOT send a separate "confirm" action for such a step (the user would be asked twice). Keep going afterwards.
- Read before you write: look at a file or a directory before changing it, and say in "reason" what you expect.
- Prefer one clear command over a chain; check the exit code and the output before the next step."""

ACTION_OPS = {
    "shell": "exec", "file_read": "fs_read", "file_write": "fs_write", "file_list": "fs_list",
    "file_delete": "fs_delete", "file_move": "fs_move", "file_find": "fs_find", "file_stat": "fs_stat",
    "file_mkdir": "fs_mkdir", "download": "fetch", "package": "pkg", "process_list": "proc_list",
    "process_kill": "proc_kill", "sysinfo": "sysinfo",
}

def action_params(kind: str, action: Dict[str, Any]) -> Dict[str, Any]:
    """Параметры операции агента из действия модели (чужие ключи не проходят)."""
    allowed = {
        "shell": ("command", "cwd", "timeout", "env"),
        "file_read": ("path", "offset", "max_bytes", "binary"),
        "file_write": ("path", "text", "content_b64", "append"),
        "file_list": ("path", "limit"),
        "file_delete": ("path", "recursive"),
        "file_move": ("source", "target"),
        "file_find": ("path", "pattern", "limit"),
        "file_stat": ("path",),
        "file_mkdir": ("path",),
        "download": ("url", "path", "max_bytes"),
        "package": ("name", "manager", "timeout"),
        "process_list": ("query", "limit"),
        "process_kill": ("pid", "force"),
        "sysinfo": (),
    }.get(kind, ())
    params = {key: action[key] for key in allowed if action.get(key) not in (None, "")}
    if kind == "package":
        params["action"] = str(action.get("pkg_action") or action.get("operation") or "install")
    return params

def summarize(kind: str, result: Dict[str, Any], lang: str = "ru") -> tuple:
    """``(строка для карточки шага, строка для модели)`` по ответу агента."""
    en = lang == "en"
    if kind == "shell":
        code = result.get("exit_code")
        output = str(result.get("output") or "").strip()
        head = (f"$ {result.get('command', '')} → " +
                (("timeout" if result.get("status") == "timeout" else
                  "отменено" if result.get("status") == "cancelled" and not en else
                  "cancelled" if result.get("status") == "cancelled" else f"код {code}" if not en else f"exit {code}")))
        tail = output[-4000:]
        model = f"exit_code={code} status={result.get('status')}\n{tail}" + (
            "\n…(вывод обрезан)" if result.get("truncated") else "")
        return head + (" · " + output.splitlines()[-1][:120] if output else ""), model
    if kind == "file_read":
        text = str(result.get("text") or "")
        return (f"{result.get('path')} — {result.get('size', 0)} B",
                text[:12000] + ("\n…(обрезано)" if result.get("truncated") else ""))
    if kind == "file_write":
        return (f"{result.get('path')} ← {result.get('bytes', 0)} B",
                f"written {result.get('bytes', 0)} bytes to {result.get('path')} (size {result.get('size')})")
    if kind == "file_list":
        rows = result.get("entries") or []
        listing = "\n".join(f"{r['type'][0]} {r.get('size', 0):>10} {r['name']}" for r in rows)
        return (f"{result.get('path')} — {result.get('shown', len(rows))}/{result.get('total', len(rows))}",
                listing[:8000])
    if kind == "file_delete":
        return (f"удалено {result.get('path')}" if not en else f"deleted {result.get('path')}",
                f"deleted {result.get('path')}")
    if kind == "file_move":
        return (f"{result.get('source')} → {result.get('target')}",
                f"moved {result.get('source')} to {result.get('target')}")
    if kind == "file_find":
        rows = result.get("matches") or []
        return (f"{result.get('pattern')} — {len(rows)}", "\n".join(r["path"] for r in rows)[:6000])
    if kind == "download":
        return (f"{result.get('path')} ← {result.get('bytes', 0)} B",
                f"downloaded {result.get('bytes')} bytes to {result.get('path')}")
    if kind == "package":
        return (f"{result.get('manager')} {result.get('pkg_action')} {result.get('package') or ''} → "
                f"{'код ' if not en else 'exit '}{result.get('exit_code')}",
                f"exit_code={result.get('exit_code')}\n" + str(result.get("output") or "")[-4000:])
    if kind == "process_list":
        rows = result.get("processes") or []
        return (f"{'процессов' if not en else 'processes'}: {len(rows)}",
                json.dumps(rows[:40], ensure_ascii=False)[:6000])
    if kind == "process_kill":
        return (f"PID {result.get('pid')}", f"killed pid {result.get('pid')}")
    if kind == "sysinfo":
        label = f"{result.get('distro') or result.get('os')} · {result.get('arch')}"
        return label, json.dumps(result, ensure_ascii=False)[:4000]
    return json.dumps(result, ensure_ascii=False)[:300], json.dumps(result, ensure_ascii=False)[:4000]

def confirm_prompt(kind: str, action: Dict[str, Any], machine: str, lang: str = "ru") -> str:
    """Текст подтверждения опасного машинного действия."""
    en = lang == "en"
    if kind == "shell":
        command = str(action.get("command") or "")[:300]
        return (f"Run on «{machine}»:\n{command}\nThis can be irreversible. Allow?" if en else
                f"Выполнить на «{machine}»:\n{command}\nЭто может быть необратимо. Разрешить?")
    if kind == "file_delete":
        return (f"Delete «{action.get('path')}» on «{machine}»"
                + (" with everything inside" if action.get("recursive") else "") + "?" if en else
                f"Удалить «{action.get('path')}» на «{machine}»"
                + (" со всем содержимым" if action.get("recursive") else "") + "?")
    if kind == "file_write":
        return (f"Overwrite «{action.get('path')}» on «{machine}»?" if en else
                f"Перезаписать «{action.get('path')}» на «{machine}»?")
    if kind == "package":
        return (f"{action.get('pkg_action')} the package «{action.get('name')}» on «{machine}»?" if en else
                f"Пакет «{action.get('name')}» на «{machine}»: {action.get('pkg_action')}. Разрешить?")
    if kind == "process_kill":
        return (f"Kill process {action.get('pid')} on «{machine}»?" if en else
                f"Завершить процесс {action.get('pid')} на «{machine}»?")
    if kind == "download":
        return (f"Download «{action.get('url')}» to «{action.get('path')}» on «{machine}»?" if en else
                f"Скачать «{action.get('url')}» в «{action.get('path')}» на «{machine}»?")
    return (f"Action «{kind}» on «{machine}». Allow?" if en else
            f"Действие «{kind}» на «{machine}». Разрешить?")
