"""Браузер DFY на удалённой машине (Раунд 30, пункт 1а + 1б + 1в).

Агент на машине (``app/dfy_agent.py``) умеет ``browser_start`` — запускает там
Chromium/Chrome/Edge с CDP на 127.0.0.1 машины — и отдаёт сырой TCP-туннель к
этому CDP по ``POST /tunnel`` (подпись HMAC, как у всех вызовов).  Здесь
MemuAI-сторона:

* ``CDPTunnel`` — локальный порт 127.0.0.1:N; каждое соединение к нему — новый
  подписанный туннель к агенту.  Для CDP это просто «браузер на 127.0.0.1:N»:
  ``/json/list`` и WebSocket страницы идут через него без изменений;
* ``RemoteDFYBrowser`` — тот же ``DFYBrowser`` (снимки, клики, ввод, события
  CDP для перехвата управления), только процесс браузера живёт на машине.

Работает с машинами, подключёнными по коду агента (HTTP/HTTPS).  SSH-машины и
«Этот компьютер» браузер на машине не дают: у первых нет постоянного канала
(нужен был бы ``ssh -L``), у второго есть обычный браузер DFY.

Прокси машины: Chromium на машине ходит в прокси напрямую (``direct_args``),
имена резолвит прокси.  Прокси с логином требует релея на самой машине —
поэтому для браузера на машине он не поддерживается (команды и скачивания на
машине работают и с логином — через ``HTTP(S)_PROXY``).
"""
from __future__ import annotations

import asyncio
import json
import ssl
from typing import Any, Dict, List, Optional

from .dfy_agent import sign
from .dfy_browser import DFYBrowser
from .dfy_remote import RemoteError

HEAD_LIMIT = 16 * 1024


class CDPTunnel:
    """127.0.0.1:port → ``POST /tunnel`` агента → CDP браузера на машине."""

    def __init__(self, host: str, port: int, token: str, *, tls: bool = False) -> None:
        self.host, self.remote_port, self.token, self.tls = host, int(port), token, bool(tls)
        self.port = 0
        self.server: Optional[asyncio.base_events.Server] = None
        self._writers: set = set()

    async def start(self) -> int:
        if self.server is None:
            self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
            self.port = int(self.server.sockets[0].getsockname()[1])
        return self.port

    async def open(self):
        """Один подписанный туннель → ``(reader, writer)`` уже после ответа агента «200»."""
        context = None
        if self.tls:
            # как HttpTransport (verify=False): агент с самоподписанным сертификатом, подлинность — подпись HMAC
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.remote_port, ssl=context), 15)
        except (OSError, asyncio.TimeoutError) as exc:
            raise RemoteError(f"Нет связи с машиной: {exc}", f"No connection to the machine: {exc}") from None
        body = b"{}"
        writer.write(b"POST /tunnel HTTP/1.1\r\nHost: " + self.host.encode("ascii", "replace") +
                     b"\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() +
                     b"\r\nX-Memu-Agent-Auth: " + sign(self.token, body).encode("ascii") + b"\r\n\r\n" + body)
        await writer.drain()
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, OSError):
            writer.close()
            raise RemoteError("Агент не открыл туннель к браузеру", "The agent did not open the browser tunnel") from None
        status = head.split(b" ", 2)[1:2]
        if status != [b"200"]:
            text = b""
            try:
                text = await asyncio.wait_for(reader.read(4096), 3)
            except (asyncio.TimeoutError, OSError):
                pass
            writer.close()
            try:
                payload = json.loads(text or b"{}")
            except ValueError:
                payload = {}
            raise RemoteError(str(payload.get("error") or "Агент отказал в туннеле к браузеру"),
                              str(payload.get("error_en") or "The agent refused the browser tunnel"))
        return reader, writer

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            try:
                remote = await self.open()
            except RemoteError:
                writer.close()
                return
            self._writers.add(remote[1])
            await asyncio.gather(_pump(reader, remote[1]), _pump(remote[0], writer))
        finally:
            self._writers.discard(writer)

    async def close(self) -> None:
        server, self.server = self.server, None
        if server is None:
            return
        server.close()
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()
        try:
            await asyncio.wait_for(server.wait_closed(), 2)
        except (asyncio.TimeoutError, Exception):
            pass


async def _pump(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await source.read(65536)
            if not data:
                break
            target.write(data)
            await target.drain()
    except (OSError, RuntimeError, asyncio.CancelledError):
        pass
    finally:
        target.close()


class RemoteDFYBrowser(DFYBrowser):
    """``DFYBrowser`` поверх браузера на удалённой машине (через агента и ``CDPTunnel``)."""

    def __init__(self, machines: Any, machine_id: str, *, visible: bool = False,
                 extra_args: Optional[List[str]] = None) -> None:
        # Имена сайтов резолвит машина (или её прокси), а не этот ПК: proxied=True — без локального DNS.
        super().__init__(visible=visible, extra_args=extra_args, proxied=True)
        self.machines = machines
        self.machine_id = str(machine_id)
        self.tunnel: Optional[CDPTunnel] = None
        self.remote: Dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return self.socket is not None and self.tunnel is not None and self._receiver is not None \
            and not self._receiver.done()

    async def start(self) -> None:
        if self.running:
            return
        await self.close(stop_remote=False)
        transport = self.machines.transport(self.machine_id)
        if not getattr(transport, "supports_browser", False):
            raise RemoteError("Браузер на машине доступен только для машин, подключённых по коду агента (не SSH)",
                              "A browser on the machine needs a machine paired with the agent code (not SSH)")
        self.remote = await self.machines.call(self.machine_id, "browser_start",
                                               {"visible": self.visible, "args": list(self.extra_args)}, timeout=60)
        row = transport.row
        self.tunnel = CDPTunnel(str(row.get("host") or ""), int(row.get("port") or 8765), transport.token,
                                tls=bool(row.get("tls")))
        port = await self.tunnel.start()
        await self._attach(port)

    async def close(self, *, stop_remote: bool = True) -> None:
        await super().close()
        tunnel, self.tunnel = self.tunnel, None
        if tunnel is not None:
            await tunnel.close()
        if stop_remote and self.remote:
            self.remote = {}
            try:
                await self.machines.call(self.machine_id, "browser_stop", {}, timeout=15)
            except Exception:
                pass       # машина могла уйти офлайн — браузер там закроется вместе с агентом

    async def clear_profile(self) -> None:
        raise RuntimeError("Профиль браузера на машине хранится у агента (каталог агента → browser-profile)")
