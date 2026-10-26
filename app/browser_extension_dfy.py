"""Reuse the DFY driver through Chrome's extension debugger transport.

Only a new background tab belongs to this driver. Closing a DFY session never
closes a user's existing tabs or their Chrome process/profile.
"""
from __future__ import annotations

from .browser_extension import bridge, settings
from .dfy_browser import DFYBrowser, _INIT_SCRIPT

class ExtensionDFYBrowser(DFYBrowser):
    def __init__(self, *, visible: bool = False) -> None:
        super().__init__(visible=False)
        self.tab_id: int | None = None
        self._connection = None

    @property
    def running(self) -> bool:
        return self.tab_id is not None and bridge.socket is self._connection and bridge.socket is not None

    async def start(self, _retry: bool = False) -> None:
        if not settings()["dfy"]:
            raise PermissionError("Разрешите DFY в Настройки → Интеграции → Chrome")
        if self.running:
            return
        self.tab_id = None
        result = await bridge.rpc("dfy_start")
        self.tab_id = int(result["tab_id"])
        self._connection = bridge.socket
        try:
            await self.command("Page.addScriptToEvaluateOnNewDocument", {"source": _INIT_SCRIPT})
            await self.evaluate(_INIT_SCRIPT)
        except BaseException:
            await self.close()
            raise

    async def command(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        await self.start()
        return await bridge.rpc("cdp", {"tab_id": self.tab_id, "method": method, "params": params or {}}, timeout=timeout)

    async def close(self) -> None:
        tab, self.tab_id = self.tab_id, None
        if tab is not None and bridge.socket is self._connection and bridge.socket is not None:
            try:
                await bridge.rpc("dfy_stop", {"tab_id": tab}, timeout=8)
            except RuntimeError:
                pass
        self._connection = None

    async def clear_profile(self) -> None:
        await self.close()

    async def adopt_popups(self) -> None:
        return
