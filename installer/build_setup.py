"""Build MemuAI-Setup-<ver>.exe / MemuAIProxy-Setup-<ver>.exe (called by ``build.py``).

1. ``build_stub``: PyInstaller one-file exe from ``installer/setup.spec`` (cached:
   rebuilt only when ``installer/`` sources, the icon or the version change);
2. ``payload.assemble``: the stub + the compressed PyInstaller dist folders of the
   product (``products.py``) + index + trailer, written atomically;
3. optional signing: if ``MEMUAI_SIGN_COMMAND`` is set (e.g.
   ``signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /a "{file}"``),
   it is run on the finished Setup. Signing appends the certificate table after
   the payload trailer; the reader tolerates that. The uninstaller copy made at
   install time is the stub without the payload and is therefore unsigned
   (see payload.strip_signature_directory).
"""
from __future__ import annotations

import hashlib
import lzma
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from . import payload, products

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "installer" / "setup.spec"

STUB_TARGETS = {
    "memuai": {"name": "MemuAI-Setup-stub", "icon": "memuai-icon.ico", "version_file": "build/setup-memuai-version.txt",
               "description": "MemuAI Setup", "product": "MemuAI",
               "comments": "Installs MemuAI, MemuAI Proxy and MemuCode CLI for the current Windows user."},
    "proxy": {"name": "MemuAIProxy-Setup-stub", "icon": "icon.ico", "version_file": "build/setup-proxy-version.txt",
              "description": "MemuAI Proxy Setup", "product": "MemuAI Proxy",
              "comments": "Installs the standalone MemuAI Proxy for the current Windows user."},
}


def _hash_inputs(extra: str) -> str:
    digest = hashlib.sha256(extra.encode("utf-8"))
    files = [p for p in (ROOT / "installer").rglob("*")
             if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".exe", ".partial")]
    for path in sorted(files):
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def build_stub(build, product_id: str, version: str, clean: bool = False, force: bool = False,
               say: Callable[[str], None] = print) -> Path:
    """PyInstaller the Setup stub of *product_id*; returns the exe path."""
    target = dict(STUB_TARGETS[product_id])
    (ROOT / "build").mkdir(exist_ok=True)
    build.write_version_file(target, version)
    dist = ROOT / "build" / "setup-stub" / product_id
    work = ROOT / "build" / "setup-stub-work" / product_id
    exe = dist / (target["name"] + ".exe")
    icon = ROOT / target["icon"]
    stamp_name = f"setup-stub-{product_id}"
    key = _hash_inputs(version + target["name"] + hashlib.sha256(icon.read_bytes()).hexdigest()
                       + build.runtime_fingerprint())
    if not (clean or force) and exe.is_file() and build.read_stamp(stamp_name) == key:
        say(f"{target['name']}: исходники установщика не менялись — сборка пропущена.")
        return exe
    env = dict(os.environ, MEMUAI_SETUP_NAME=target["name"], MEMUAI_SETUP_ICON=str(icon),
               MEMUAI_SETUP_VERSION_FILE=str(ROOT / target["version_file"]))
    command = [str(build.VENV_PY), "-m", "PyInstaller", "--noconfirm", "--log-level", "WARN",
               "--distpath", str(dist), "--workpath", str(work), str(SPEC)]
    if clean:
        command.append("--clean")
    log_path = ROOT / "build" / f"setup-stub-{product_id}.log"
    say(f"Собираю {target['name']} (интерфейс установщика)…")
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        code = subprocess.call(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    if code or not exe.is_file():
        raise SystemExit(f"Сборка {target['name']} не удалась, подробности в {log_path}")
    build.write_stamp(stamp_name, key)
    return exe


def sign(path: Path, say: Callable[[str], None] = print) -> None:
    command = os.environ.get("MEMUAI_SIGN_COMMAND", "").strip()
    if not command:
        return
    parts = [part.replace("{file}", str(path)) for part in shlex.split(command, posix=(os.name != "nt"))]
    if not any(str(path) in part for part in parts):
        parts.append(str(path))
    say(f"Подписываю {path.name}…")
    result = subprocess.run(parts, cwd=ROOT)
    if result.returncode:
        raise SystemExit(f"Подпись {path.name} не удалась ({result.returncode})")
    with payload.Payload(path) as check:      # the payload must still be readable
        check.read_stub()


def build_setup(build, product_id: str, version: str, *, fast: bool = False, clean: bool = False,
                force: bool = False, out: str = "", say: Callable[[str], None] = print) -> Path:
    product = products.get(product_id)
    sources = []
    for comp in product.components:
        folder = ROOT / comp.source
        exe_name = Path(comp.exe.replace("\\", "/")).name
        if not (folder / exe_name).is_file():
            raise SystemExit(f"Нет файла {comp.source}/{exe_name} — сначала соберите программы (build.py exe)")
        sources.append((comp.id, folder, comp.dest.replace("\\", "/")))
    for need in ("memuai-icon.ico", "icon.ico"):
        if not (ROOT / need).is_file():
            raise SystemExit(f"Нет файла {need}")
    stub = build_stub(build, product_id, version, clean=clean, force=force, say=say)
    output_dir = Path(out).resolve() if out else ROOT / "installer"
    output_dir.mkdir(parents=True, exist_ok=True)
    final = output_dir / f"{product.setup_basename}-{version}.exe"
    staging = ROOT / "build" / "setup-output" / final.name
    staging.parent.mkdir(parents=True, exist_ok=True)
    preset = 1 if fast else 6 | lzma.PRESET_EXTREME
    meta = {"product": product.id, "version": version, "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "name": product.name}
    say(f"Упаковываю {product.name} {version} ({'быстрое' if fast else 'максимальное'} сжатие)…")
    started = time.time()
    # Round 36: unchanged programs reuse their compressed blocks (build/payload-cache, dropped by --clean).
    cache = ROOT / "build" / "payload-cache" / product.id
    if clean and cache.exists():
        shutil.rmtree(cache, ignore_errors=True)
    index = payload.assemble(stub, sources, meta, staging, preset=preset, cache_dir=cache)
    sign(staging, say)
    # Compile outside the release folder, then replace atomically: Windows may be scanning
    # the previous Setup in that folder.
    temporary = final.with_name(final.name + ".partial")
    try:
        shutil.copy2(staging, temporary)
        os.replace(temporary, final)
    finally:
        if temporary.exists():
            temporary.unlink()
    size = final.stat().st_size
    say(f"{final.name}: {size / 1048576:.1f} МБ (программы {index.meta['total_size'] / 1048576:.0f} МБ, "
        f"{index.meta['file_count']} файлов) за {time.time() - started:.0f} с")
    return final
