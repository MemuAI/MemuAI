"""Release an inherited installation-directory lock, including the one-file parent."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import shutil
import tempfile
from typing import Sequence

from .cli import Args


def absolute_arguments(args: Args) -> list[str]:
    """Keep parsed CLI semantics when restarting from another working directory."""
    result = []
    for flag, enabled in (("--very-silent" if args.very_silent else "--silent", args.silent),
                          ("--uninstall", args.uninstall), ("--launch", args.launch),
                          ("--remove-data", args.remove_data), ("--force-close", args.force_close),
                          ("--relaunched", args.relaunched), ("--ui-dev", args.ui_dev)):
        if enabled:
            result.append(flag)
    for flag, value in (("--dir", args.directory), ("--app-dir", args.app_dir), ("--log", args.log)):
        if value:
            result.append(flag + "=" + str(Path(os.path.expandvars(os.path.expanduser(value))).resolve()))
        elif flag == "--log" and value == "":
            result.append("/LOG")
    for flag, value in (("--components", args.components), ("--tasks", args.tasks)):
        if value is not None:
            # Empty selections must not consume the following argument.
            result.extend([flag, ",".join(value)])
    if args.merge_tasks:
        result.extend(["--merge-tasks", ",".join(args.merge_tasks)])
    for flag, value in (("--edition", args.edition), ("--language", args.language), ("--product", args.product)):
        if value is not None:
            result.append(flag + "=" + value)
    return result + list(args.unknown)


def restart_outside_installation(exe: Path, args: Args, roots: Sequence[Path]) -> bool:
    """A chdir in Python cannot release PyInstaller's parent process CWD handle.

    Restart the whole frozen executable before acquiring the installer mutex or
    changing files. The old one-file processes exit; the independent replacement
    starts outside every installation root. This also fixes launches by old apps.
    """
    roots = [Path(root).resolve() for root in roots]
    current = Path.cwd().resolve()
    exe = exe.resolve()
    current_inside = any(current == root or root in current.parents for root in roots)
    exe_inside = any(exe == root or root in exe.parents for root in roots)
    if not current_inside and not exe_inside:
        return False
    safe = Path(tempfile.gettempdir()).resolve()
    if any(safe == root or root in safe.parents for root in roots):
        raise OSError("Папка TEMP находится внутри папки установки / TEMP is inside the installation folder")
    from .winplat import clean_environment
    if exe_inside:
        folder = Path(tempfile.mkdtemp(prefix="MemuAI-Setup-run-", dir=safe))
        copied = folder / exe.name
        shutil.copy2(exe, copied)
        exe = copied
    subprocess.Popen([str(exe), *absolute_arguments(args)], cwd=str(safe),
                     env=clean_environment(), close_fds=True,
                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return True
