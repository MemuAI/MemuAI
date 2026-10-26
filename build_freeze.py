"""Generate a normal onedir spec with the Python archive beside the launcher.

MemuAI's native PE launcher contains version/icon resources, while its Python
code is in the documented PyInstaller sidecar PKG. No custom bootloader,
packing, encryption, antivirus configuration or runtime patching is involved.
"""
from __future__ import annotations

from pathlib import Path
import sys

def external_archive_spec(source: str) -> str:
    marker = "\n    exclude_binaries=True,\n"
    if source.count(marker) != 1 or "\nexe = EXE(\n" not in source:
        raise ValueError("Expected the generated PyInstaller onedir EXE spec")
    return source.replace(marker, marker + "    append_pkg=False,\n", 1)

def main(arguments=None):
    from PyInstaller import __main__ as freeze
    make_spec = freeze.run_makespec

    def generate(**options):
        path = Path(make_spec(**options))
        source = path.read_text(encoding="utf-8")
        path.write_text(external_archive_spec(source), encoding="utf-8")
        return str(path)

    freeze.run_makespec = generate
    try:
        freeze.run(sys.argv[1:] if arguments is None else arguments)
    finally:
        freeze.run_makespec = make_spec

if __name__ == "__main__":
    main()
