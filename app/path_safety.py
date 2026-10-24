"""Filesystem link detection, including native junctions on Python 3.10/3.11."""
from pathlib import Path
import stat


def is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    junction = getattr(path, "is_junction", None)
    if callable(junction) and junction():
        return True
    try:
        tag = getattr(path.lstat(), "st_reparse_tag", 0)
    except OSError:
        return False
    return bool(tag and tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003))
