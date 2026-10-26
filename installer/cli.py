"""Command line of MemuAI Setup / the uninstaller.

Inno-Setup-compatible switches are accepted so existing scripts keep working::

    MemuAI-Setup.exe /VERYSILENT /DIR="D:\\Apps\\MemuAI" /COMPONENTS=memuai,memucode /TASKS=autostart
    MemuAI-Setup.exe --silent --edition proxy --tasks autostart,proxyicon --launch
    MemuAI-Uninstall.exe --uninstall --silent [--remove-data]

``/S`` (NSIS) and ``/D=path`` (NSIS, last argument) work as well. Unknown
Inno switches that only make sense for Inno (``/SP-``, ``/NORESTART``,
``/SUPPRESSMSGBOXES``, ``/CLOSEAPPLICATIONS``…) are accepted and ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

IGNORED = {"/SP-", "/NORESTART", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/NOCLOSEAPPLICATIONS",
           "/FORCECLOSEAPPLICATIONS", "/NOFORCECLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS",
           "/NORESTARTAPPLICATIONS", "/NOCANCEL", "/NOICONS", "/ALLUSERS", "/CURRENTUSER", "/PORTABLE=0"}

@dataclass
class Args:
    silent: bool = False
    very_silent: bool = False
    uninstall: bool = False
    directory: Optional[str] = None
    components: Optional[List[str]] = None
    tasks: Optional[List[str]] = None
    merge_tasks: List[str] = field(default_factory=list)
    edition: Optional[str] = None
    language: Optional[str] = None
    log: Optional[str] = None
    launch: bool = False
    remove_data: bool = False
    force_close: bool = False
    app_dir: Optional[str] = None
    product: Optional[str] = None
    relaunched: bool = False
    ui_dev: bool = False
    unknown: List[str] = field(default_factory=list)

def _split(value: str) -> List[str]:
    return [item.strip() for item in value.strip().strip('"').split(",") if item.strip()]

def parse(argv: Sequence[str]) -> Args:
    args = Args()
    items = list(argv)
    i = 0
    while i < len(items):
        raw = items[i]
        upper = raw.upper()
        key, _, value = raw.partition("=")
        ukey = key.upper()

        def next_value() -> str:
            nonlocal i
            if value:
                return value
            if i + 1 < len(items):
                i += 1
                return items[i]
            return ""

        if upper in ("/S", "/SILENT", "--SILENT", "-S", "/QUIET", "--QUIET", "/Q"):
            args.silent = True
        elif upper in ("/VERYSILENT", "--VERY-SILENT"):
            args.silent = args.very_silent = True
        elif upper in ("--UNINSTALL", "/UNINSTALL", "-UNINSTALL"):
            args.uninstall = True
        elif ukey in ("/DIR", "--DIR", "/D"):
            if ukey == "/D" and value:

                args.directory = " ".join([value] + items[i + 1:]).strip().strip('"')
                break
            args.directory = next_value().strip().strip('"')
        elif ukey in ("/COMPONENTS", "--COMPONENTS"):
            args.components = _split(next_value())
        elif ukey in ("/TASKS", "--TASKS"):
            args.tasks = _split(next_value())
        elif ukey in ("/MERGETASKS", "--MERGE-TASKS"):
            args.merge_tasks += _split(next_value())
        elif ukey in ("/TYPE", "--TYPE", "--EDITION"):
            kind = next_value().strip().lower()
            args.edition = "proxy" if kind in ("proxy", "proxyonly", "proxy-only") else "full"
        elif ukey in ("/LANG", "--LANG", "--LANGUAGE"):
            lang = next_value().strip().lower()
            args.language = "ru" if lang.startswith("ru") else "en"
        elif ukey in ("/LOG", "--LOG"):
            if value:
                args.log = value.strip().strip('"')
            elif ukey == "--LOG" and i + 1 < len(items) and not items[i + 1].startswith(("/", "-")):
                i += 1
                args.log = items[i]
            elif ukey == "--LOG" and i + 1 < len(items) and items[i + 1].startswith("/") and "/" in items[i + 1][1:]:
                i += 1
                args.log = items[i]
            else:
                args.log = ""
        elif upper in ("/LAUNCH", "--LAUNCH", "/RUN", "--RUN"):
            args.launch = True
        elif upper in ("--REMOVE-DATA", "/REMOVEDATA", "--DELETE-DATA"):
            args.remove_data = True
        elif upper in ("--KEEP-DATA", "/KEEPDATA"):
            args.remove_data = False
        elif upper in ("--FORCE-CLOSE", "/FORCECLOSE"):
            args.force_close = True
        elif ukey == "--APP-DIR":
            args.app_dir = next_value().strip().strip('"')
        elif ukey == "--PRODUCT":
            args.product = next_value().strip().lower()
        elif upper == "--RELAUNCHED":
            args.relaunched = True
        elif upper == "--UI-DEV":
            args.ui_dev = True
        elif upper in IGNORED or ukey in ("/LOADINF", "/SAVEINF", "/PASSWORD", "/RESTARTEXITCODE"):
            pass
        else:
            args.unknown.append(raw)
        i += 1
    return args

def resolve_selection(args: Args, product, defaults_components: List[str], defaults_tasks: List[str]):
    """Apply /TYPE, /COMPONENTS, /TASKS and /MERGETASKS (``!task`` removes) to the defaults."""
    components = list(defaults_components)
    if args.edition == "proxy":
        components = [c.id for c in product.components if c.required]
    elif args.edition == "full":
        components = product.default_components()
    if args.components is not None:
        components = [c for c in args.components if not c.startswith("!")]
    tasks = list(defaults_tasks) if args.tasks is None else []
    for item in (args.tasks or []) + args.merge_tasks:
        if item.startswith("!"):
            if item[1:] in tasks:
                tasks.remove(item[1:])
        elif item not in tasks:
            tasks.append(item)
    return product.normalize(components, tasks)
