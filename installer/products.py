"""Products the MemuAI installer can install — the manifest that replaces the two .iss scripts.

``memuai``  — the suite: MemuAI Proxy (required) + MemuAI + MemuCode CLI, in
              ``%LOCALAPPDATA%\\Programs\\MemuAI``, one folder per program
              (never overlay two PyInstaller ``_internal`` trees).
``proxy``   — standalone MemuAI Proxy (old ``installer-proxy.iss``), files at the
              root of ``%LOCALAPPDATA%\\Programs\\MemuAI Proxy``, own data folder
              ``%APPDATA%\\MemuAIProxy``.

The registry keys keep the Inno Setup names (``{AppId}_is1``) on purpose: the new
Setup recognises and upgrades installations made by the old Inno installers in
place, and only one entry is shown in “Installed apps”.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"


@dataclass(frozen=True)
class Component:
    id: str
    required: bool = False
    default: bool = True
    #: Path relative to the install dir of the main executable of the component.
    exe: str = ""
    #: The build folder (relative to the project root) and where it lands in the install dir.
    source: str = ""
    dest: str = ""


@dataclass(frozen=True)
class Task:
    id: str
    default: bool
    #: Components that must be selected for the task to apply.
    needs: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Shortcut:
    #: "programs" (Start menu group) or "desktop".
    where: str
    name: str            # file name without .lnk; may be an i18n key prefixed with "@"
    target: str          # relative to the install dir
    component: str
    args: str = ""
    task: str = ""       # empty = always created with the component
    workdir: str = ""    # "%USERPROFILE%" or empty (= target folder)
    app_id: str = ""     # AppUserModelID (taskbar grouping with the running window)


@dataclass(frozen=True)
class RunValue:
    name: str
    target: str
    args: str
    task: str
    component: str


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    publisher: str
    reg_key: str
    default_dir_name: str
    group: str
    uninstaller: str
    setup_basename: str
    components: Tuple[Component, ...]
    tasks: Tuple[Task, ...]
    shortcuts: Tuple[Shortcut, ...]
    run_values: Tuple[RunValue, ...]
    #: Command started by “Launch” on the last page: (component, exe, args).
    launch: Tuple[Tuple[str, str, str], ...]
    #: Personal data folders (relative to an environment folder) removed only on request.
    data_dirs: Tuple[Tuple[str, str], ...]
    #: Executables to ask for a graceful ``--shutdown`` before files are replaced.
    shutdown: Tuple[str, ...]
    #: Display icon candidates (first existing wins).
    icons: Tuple[str, ...]
    #: Files and folders of historical layouts removed after a successful upgrade.
    legacy_files: Tuple[str, ...] = ()
    #: Historical shortcuts (relative to Start-menu group / desktop) always retired.
    legacy_shortcuts: Tuple[Tuple[str, str], ...] = ()
    #: PATH entries (relative to install dir) added for a component: (component, folder).
    path_entries: Tuple[Tuple[str, str], ...] = ()
    #: HKLM key of an old all-users Inno installation (detected, never touched).
    legacy_machine_key: str = ""
    icon_file: str = "memuai-icon.ico"
    comments: Dict[str, str] = field(default_factory=dict)
    #: Round 29: optional components shipped as a zip when not selected, so the app can
    #: install them later without the Setup (``(component, zip path relative to the install dir)``).
    packages: Tuple[Tuple[str, str], ...] = ()
    #: Round 29: commands run before a component's files are removed on uninstall
    #: (``(component, exe relative to the install dir, args)``), e.g. undo "Open with" entries.
    pre_uninstall: Tuple[Tuple[str, str, str], ...] = ()
    #: Round 35 (protocol): URL schemes registered per user (HKCU\Software\Classes\<scheme>) for a component:
    #: ``(component, scheme, exe relative to the install dir, description)``; removed on uninstall.
    url_protocols: Tuple[Tuple[str, str, str, str], ...] = ()

    # ------------------------------------------------------------------ helpers
    def component(self, component_id: str) -> Component:
        for item in self.components:
            if item.id == component_id:
                return item
        raise KeyError(component_id)

    def component_ids(self) -> List[str]:
        return [c.id for c in self.components]

    def default_components(self) -> List[str]:
        return [c.id for c in self.components if c.required or c.default]

    def default_tasks(self) -> List[str]:
        return [t.id for t in self.tasks if t.default]

    def task(self, task_id: str) -> Optional[Task]:
        for item in self.tasks:
            if item.id == task_id:
                return item
        return None

    def normalize(self, components, tasks) -> Tuple[List[str], List[str]]:
        """Required components added, unknown ids dropped, tasks of absent components removed."""
        chosen = [c.id for c in self.components if c.required or c.id in set(components)]
        if self.id == "memuai" and "memuai" not in chosen:
            # CLI and MemuNote ship with the full edition only (MemuNote is installed later from MemuAI settings).
            chosen = [c for c in chosen if c not in ("memucode", "memunote", "memufiles")]
        valid_tasks = []
        for task_id in tasks:
            task = self.task(task_id)
            if task and all(need in chosen for need in task.needs) and task_id not in valid_tasks:
                valid_tasks.append(task_id)
        return chosen, valid_tasks


SUITE = Product(
    id="memuai",
    name="MemuAI",
    publisher="MemuAI",
    reg_key="{7C2A93E1-5B44-4E0D-9F61-8A2B43E9A122}_is1",
    default_dir_name="MemuAI",
    group="MemuAI",
    uninstaller="MemuAI-Uninstall.exe",
    setup_basename="MemuAI-Setup",
    components=(
        Component("proxy", required=True, exe=r"MemuAIProxy\MemuAIProxy.exe",
                  source="dist/MemuAIProxy", dest="MemuAIProxy"),
        Component("memuai", default=True, exe=r"MemuAI\MemuAI.exe", source="dist/MemuAI", dest="MemuAI"),
        Component("memucode", default=True, exe=r"MemuCode\MemuCode.exe", source="dist/MemuCode", dest="MemuCode"),
        # Round 29: MemuNote — the light AI notepad. Optional: when it is not selected the Setup leaves it
        # as Packages\MemuNote.zip and MemuAI → Settings → MemuNote installs it in one click.
        Component("memunote", default=False, exe=r"MemuNote\MemuNote.exe", source="dist/MemuNote", dest="MemuNote"),
        # Round 35: MemuFiles — the fast file manager. Optional like MemuNote: Packages\MemuFiles.zip
        # otherwise, installed later from MemuAI → Settings → MemuFiles (app/memufiles_manager.py).
        Component("memufiles", default=False, exe=r"MemuFiles\MemuFiles.exe", source="dist/MemuFiles", dest="MemuFiles"),
    ),
    tasks=(
        Task("desktopicon", default=False, needs=("memuai",)),
        Task("proxyicon", default=False),
        Task("autostart", default=True),
        Task("memuaiautostart", default=False, needs=("memuai",)),
    ),
    shortcuts=(
        Shortcut("programs", "MemuAI", r"MemuAI\MemuAI.exe", "memuai", app_id="MemuAI.Desktop"),
        # Round 29: «MemuAI (безопасный режим)» — starts without extensions (app/safe_mode.py).
        Shortcut("programs", "@safe_mode_shortcut", r"MemuAI\MemuAI.exe", "memuai", args="--safe-mode",
                 app_id="MemuAI.Desktop"),
        Shortcut("programs", "MemuCode CLI", r"MemuCode\MemuCode.exe", "memucode", workdir="%USERPROFILE%"),
        Shortcut("programs", "MemuAI Proxy", r"MemuAIProxy\MemuAIProxy.exe", "proxy"),
        Shortcut("programs", "MemuNote", r"MemuNote\MemuNote.exe", "memunote", workdir="%USERPROFILE%",
                 app_id="MemuAI.MemuNote"),
        Shortcut("programs", "MemuFiles", r"MemuFiles\MemuFiles.exe", "memufiles", workdir="%USERPROFILE%",
                 app_id="MemuAI.MemuFiles"),
        Shortcut("programs", "@uninstall_shortcut", "MemuAI-Uninstall.exe", "proxy", args="--uninstall"),
        Shortcut("desktop", "MemuAI", r"MemuAI\MemuAI.exe", "memuai", task="desktopicon", app_id="MemuAI.Desktop"),
        Shortcut("desktop", "MemuAI Proxy", r"MemuAIProxy\MemuAIProxy.exe", "proxy", task="proxyicon"),
    ),
    run_values=(
        RunValue("MemuAIProxy", r"MemuAIProxy\MemuAIProxy.exe", "--background", "autostart", "proxy"),
        RunValue("MemuAI", r"MemuAI\MemuAI.exe", "", "memuaiautostart", "memuai"),
    ),
    launch=(("memuai", r"MemuAI\MemuAI.exe", ""), ("proxy", r"MemuAIProxy\MemuAIProxy.exe", "--background")),
    data_dirs=(("APPDATA", "MemuAI"), ("LOCALAPPDATA", "MemuAI")),
    shutdown=(r"MemuAI\MemuAI.exe", r"MemuAIProxy\MemuAIProxy.exe", r"MemuNote\MemuNote.exe", r"MemuFiles\MemuFiles.exe"),
    icons=(r"MemuAI\MemuAI.exe", r"MemuAIProxy\MemuAIProxy.exe"),
    legacy_files=("MemuAI.exe", "MemuAIProxy.exe", "MemuAI-Maintenance.exe", "_internal"),
    legacy_shortcuts=(
        ("programs", "MemuAI Proxy без окна"), ("desktop", "MemuAI Proxy без окна"),
        ("programs", "Изменить или удалить компоненты MemuAI"),
        ("programs", "Modify or remove MemuAI components"),
        ("programs", "Удалить MemuAI"), ("programs", "Uninstall MemuAI"),
    ),
    # Round 35 (protocol): MemuAI\MemuCLI holds memu.exe (the MemuAI command line; build.py place_memu_cli).
    path_entries=(("memucode", "MemuCode"), ("memuai", r"MemuAI\MemuCLI")),
    url_protocols=(("memuai", "memuai", r"MemuAI\MemuAI.exe", "URL:MemuAI"),),
    legacy_machine_key="{7C2A93E1-5B44-4E0D-9F61-8A2B43E9A122}_is1",
    icon_file="memuai-icon.ico",
    comments={"en": "Personal AI chat, Work, agents, and a local OpenAI-compatible API",
              "ru": "Персональный AI-чат, Work, агенты и локальный OpenAI-совместимый API"},
    packages=(("memunote", r"Packages\MemuNote.zip"), ("memufiles", r"Packages\MemuFiles.zip")),
    pre_uninstall=(("memunote", r"MemuNote\MemuNote.exe", "--unregister-open-with"),
                   # Round 38: «MemuFiles по умолчанию» — put Explorer back (exact registry backup) before removal.
                   ("memufiles", r"MemuFiles\MemuFiles.exe", "--restore-explorer")),
)

PROXY = Product(
    id="proxy",
    name="MemuAI Proxy",
    publisher="MemuAI Proxy",
    reg_key="{D05309A7-8FBA-40D6-BBE9-3FD5614D3B88}_is1",
    default_dir_name="MemuAI Proxy",
    group="MemuAI Proxy",
    uninstaller="MemuAIProxy-Uninstall.exe",
    setup_basename="MemuAIProxy-Setup",
    components=(
        Component("proxy", required=True, exe="MemuAIProxy.exe", source="dist/proxy-only/MemuAIProxy", dest=""),
    ),
    tasks=(
        Task("desktopicon", default=False),
        Task("autostart", default=True),
    ),
    shortcuts=(
        Shortcut("programs", "MemuAI Proxy", "MemuAIProxy.exe", "proxy"),
        Shortcut("programs", "@uninstall_shortcut_proxy", "MemuAIProxy-Uninstall.exe", "proxy", args="--uninstall"),
        Shortcut("desktop", "MemuAI Proxy", "MemuAIProxy.exe", "proxy", task="desktopicon"),
    ),
    run_values=(
        RunValue("MemuAIProxyStandalone", "MemuAIProxy.exe", "--background", "autostart", "proxy"),
    ),
    launch=(("proxy", "MemuAIProxy.exe", "--background"),),
    data_dirs=(("APPDATA", "MemuAIProxy"),),
    shutdown=("MemuAIProxy.exe",),
    icons=("MemuAIProxy.exe",),
    legacy_shortcuts=(("programs", "Удалить MemuAI Proxy"), ("programs", "Uninstall MemuAI Proxy")),
    legacy_machine_key="{D05309A7-8FBA-40D6-BBE9-3FD5614D3B88}_is1",
    icon_file="icon.ico",
    comments={"en": "Local AI account gateway and OpenAI-compatible API",
              "ru": "Локальный центр AI-аккаунтов и OpenAI-совместимый API"},
)

PRODUCTS: Dict[str, Product] = {SUITE.id: SUITE, PROXY.id: PROXY}


def get(product_id: str) -> Product:
    try:
        return PRODUCTS[product_id]
    except KeyError:
        raise KeyError(f"unknown product {product_id!r}") from None
