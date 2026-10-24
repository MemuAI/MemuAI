"""Read every installer payload byte, verify SHA-256 and compare with dist.

Does not execute Setup or install anything. Suitable for repeatable release
checks on Windows without touching the current user's installed applications.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from installer.payload import Payload  # noqa: E402
from installer.products import get  # noqa: E402
from app.version import APP_VERSION  # noqa: E402


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def verify(path: Path) -> None:
    count = size = compared = 0
    with Payload(path) as payload:
        if str(payload.meta.get("version")) != APP_VERSION:
            raise ValueError("Installer version differs from app/version.py")
        product = get(str(payload.meta.get("product_id") or payload.meta.get("product") or "memuai"))
        components = {component.id: component for component in product.components}
        for entry, chunks in payload.iter_files(components):
            consumed = sum(len(piece) for piece in chunks)  # drains SHA and xz integrity checks
            if consumed != entry.size:
                raise ValueError("Incorrect payload size: " + entry.path)
            count += 1
            size += consumed
            component = components[entry.component]
            packaged = PurePosixPath(entry.path.replace("\\", "/"))
            prefix = PurePosixPath(component.dest.replace("\\", "/"))
            relative = packaged.relative_to(prefix) if component.dest else packaged
            source_root = (ROOT / component.source).resolve()
            source = source_root.joinpath(*relative.parts).resolve()
            if not source.is_relative_to(source_root) or not source.is_file():
                raise ValueError("Payload file is absent from dist: " + entry.path)
            if source.stat().st_size != entry.size or digest(source) != entry.sha256:
                raise ValueError("Payload differs from dist: " + entry.path)
            compared += 1
    print(f"PASS installer payload: {count} files, {size} bytes; every SHA-256 and xz block verified; {compared} files match dist")
    print(f"SHA256: {digest(path).upper()}")


if __name__ == "__main__":
    verify(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "installer" / f"MemuAI-Setup-{APP_VERSION}.exe")
