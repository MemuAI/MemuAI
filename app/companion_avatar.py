"""Round 22: the animated companion (VRM / Live2D) of MemuAI Live and Quick Ask.

Not to be confused with ``app/companion.py`` — the text assistant of MemuAI
Proxy. This module is pure logic without HTTP or Win32, unit-tested in
``tests/companion_avatar_test.py``:

* ``PREF_DEFAULTS`` / :func:`normalize_prefs` — the ``companion_*`` keys of
  ``preferences.json`` and their migration (``pgdata`` merges both, like
  Quick Ask does);
* the model library under ``<data>/memuai/companion/models/<id>/``:
  :func:`validate_vrm` (GLB container + VRM 0.x / 1.0 extension, no external
  URIs), :func:`validate_live2d_zip` (zip-slip, zip-bomb and link checks,
  ``*.model3.json`` with its moc3 and textures present), :func:`import_model`,
  :func:`list_models`, :func:`delete_model`, :func:`model_file`;
* the optional Live2D runtime (:data:`RUNTIME_FILES`). Live2D Cubism Core is
  proprietary and an application that loads user models counts as an
  "Expandable Application" in Live2D's licence, so MemuAI never ships any
  Live2D code: the files are downloaded only after the user explicitly agrees
  (``consent=True``), from the official Live2D address and pinned npm builds.

The sample model (``pgstatic/companion/models/sample/``) is CC0 and built in.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import stat
import struct
import threading
import time
import uuid
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

STATIC_DIR = Path(__file__).parent / "pgstatic" / "companion"
SAMPLE_ID = "sample"
SAMPLE = {
    "id": SAMPLE_ID, "kind": "vrm", "builtin": True,
    "name": "Memu (HairSample_Female)", "file": "sample.vrm",
    "url": "/companion/models/sample/sample.vrm",
    "author": "VRoid Project (pixiv Inc.)", "license": "CC0 1.0",
    "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
    "source_url": "https://vroid.pixiv.help/hc/en-us/articles/4402614652569",
}

MAX_VRM_BYTES = 200 * 1024 * 1024
MAX_ZIP_BYTES = 200 * 1024 * 1024
MAX_UNPACKED_BYTES = 400 * 1024 * 1024
MAX_ZIP_FILES = 3000
MAX_RATIO = 200
MAX_MODELS = 60

EMOTIONS = ("neutral", "happy", "sad", "angry", "surprised", "relaxed", "thinking")
SURFACE_MODES = ("inline", "desktop", "both")
PLACEMENTS = ("panel", "window")
FRAMINGS = ("bust", "full")

PREF_DEFAULTS: Dict[str, Any] = {

    "companion_live_enabled": False,
    "companion_live_placement": "panel",

    "companion_quick_enabled": False,
    "companion_quick_mode": "inline",
    "companion_model_id": SAMPLE_ID,
    "companion_size": 100,
    "companion_framing": "bust",
    "companion_lipsync": True,
    "companion_emotions": True,
    "companion_emotion_tags": True,
    "companion_bubble": True,
    "companion_look_at": True,
    "companion_gestures": True,
    "companion_fps": 30,
    "companion_low_power": False,
    "companion_personality_link": True,
    "companion_live2d_consent": False,
    "companion_v": 1,
}

def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off", ""):
            return False
        return default
    return bool(value)

def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        if isinstance(value, str):
            value = value.strip().rstrip("%").strip()
        number = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(high, number))

def normalize_model_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", text) else SAMPLE_ID

def normalize_prefs(out: Dict[str, Any], raw: Any = None) -> Dict[str, Any]:
    """Validate the ``companion_*`` keys in place (called by pgdata).

    Migration (``companion_v`` 0 → 1): the early single switch
    ``companion_enabled`` turns on both surfaces it existed for; the
    ``companion_quick_window`` flag becomes ``companion_quick_mode``;
    sizes saved as ``"120%"`` or small/medium/large become numbers; an FPS
    other than 30/60 snaps to the nearest.
    """
    raw = raw if isinstance(raw, dict) else {}
    if not raw.get("companion_v"):
        legacy = raw.get("companion_enabled")
        if legacy is not None and "companion_live_enabled" not in raw:
            out["companion_live_enabled"] = _bool(legacy, False)
        if legacy is not None and "companion_quick_enabled" not in raw:
            out["companion_quick_enabled"] = _bool(legacy, False)
        if "companion_quick_mode" not in raw and "companion_quick_window" in raw:
            out["companion_quick_mode"] = "both" if _bool(raw.get("companion_quick_window"), False) else "inline"
    for key in ("companion_live_enabled", "companion_quick_enabled", "companion_lipsync",
                "companion_emotions", "companion_emotion_tags", "companion_bubble", "companion_look_at",
                "companion_gestures", "companion_low_power", "companion_personality_link",
                "companion_live2d_consent"):
        out[key] = _bool(out.get(key), PREF_DEFAULTS[key])
    mode = str(out.get("companion_quick_mode") or "").strip().lower()
    mode = {"window": "desktop", "separate": "desktop", "in-window": "inline", "inside": "inline"}.get(mode, mode)
    out["companion_quick_mode"] = mode if mode in SURFACE_MODES else "inline"
    placement = str(out.get("companion_live_placement") or "").strip().lower()
    placement = {"floating": "window", "overlay": "window", "inline": "panel"}.get(placement, placement)
    out["companion_live_placement"] = placement if placement in PLACEMENTS else "panel"
    framing = str(out.get("companion_framing") or "").strip().lower()
    out["companion_framing"] = framing if framing in FRAMINGS else "bust"
    size = out.get("companion_size")
    named = {"small": 75, "medium": 100, "large": 140, "huge": 180}
    if isinstance(size, str) and size.strip().lower() in named:
        size = named[size.strip().lower()]
    out["companion_size"] = _int(size, 100, 60, 200)
    fps = _int(out.get("companion_fps"), 30, 1, 240)
    out["companion_fps"] = 60 if fps >= 45 else 30
    out["companion_model_id"] = normalize_model_id(out.get("companion_model_id"))
    out.pop("companion_enabled", None)
    out.pop("companion_quick_window", None)
    out["companion_v"] = 1
    return out

class ModelError(ValueError):
    """A model file was rejected; ``code`` is stable for the UI."""

    def __init__(self, code: str, message: str, message_en: str = ""):
        super().__init__(message)
        self.code = code
        self.message_en = message_en or message

def _glb_json(data: bytes) -> Tuple[Dict[str, Any], int]:
    if len(data) < 28 or data[:4] != b"glTF":
        raise ModelError("not_glb", "Файл не является VRM/GLB (нет сигнатуры glTF)",
                         "The file is not a VRM/GLB (no glTF signature)")
    version, total = struct.unpack_from("<II", data, 4)
    if version != 2:
        raise ModelError("glb_version", f"Поддерживается только glTF 2.0, а не {version}",
                         f"Only glTF 2.0 is supported, not {version}")
    if total != len(data):
        raise ModelError("truncated", "Файл повреждён или обрезан: длина не совпадает с заголовком",
                         "The file is damaged or truncated: its length does not match the header")
    chunk_len, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != 0x4E4F534A or 20 + chunk_len > len(data):
        raise ModelError("no_json", "В файле нет описания модели (JSON-блока)",
                         "The file has no model description (JSON chunk)")
    try:
        doc = json.loads(data[20:20 + chunk_len].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ModelError("bad_json", "Описание модели повреждено", "The model description is damaged")
    if not isinstance(doc, dict):
        raise ModelError("bad_json", "Описание модели повреждено", "The model description is damaged")
    bin_len = 0
    offset = 20 + chunk_len
    if offset + 8 <= len(data):
        bin_len, bin_type = struct.unpack_from("<II", data, offset)
        if bin_type != 0x004E4942 or offset + 8 + bin_len > len(data):
            raise ModelError("bad_bin", "Двоичный блок модели повреждён", "The binary chunk of the model is damaged")
    return doc, bin_len

def validate_vrm(data: bytes) -> Dict[str, Any]:
    """Check a .vrm file and return its public metadata.

    VRM 0.x keeps everything in ``extensions.VRM``, VRM 1.0 in
    ``extensions.VRMC_vrm``. A plain glTF without either is refused (it has
    no humanoid bones or expressions to animate). External URIs are refused:
    the renderer only loads self-contained files and must not reach the
    network or the disk on behalf of a model.
    """
    if len(data) > MAX_VRM_BYTES:
        raise ModelError("too_large", "Модель больше 200 МБ", "The model is larger than 200 MB")
    doc, bin_len = _glb_json(data)
    ext = doc.get("extensions") if isinstance(doc.get("extensions"), dict) else {}
    if isinstance(ext.get("VRMC_vrm"), dict):
        version, vrm = "1.0", ext["VRMC_vrm"]
        meta = vrm.get("meta") if isinstance(vrm.get("meta"), dict) else {}
        title, author = meta.get("name"), ", ".join(str(a) for a in (meta.get("authors") or [])[:4])
        license_name = meta.get("licenseUrl") or ""
        redistribution = meta.get("allowRedistribution")
        expressions = list(((vrm.get("expressions") or {}).get("preset") or {}).keys())
        has_bones = bool(((vrm.get("humanoid") or {}).get("humanBones") or {}))
    elif isinstance(ext.get("VRM"), dict):
        version, vrm = "0.x", ext["VRM"]
        meta = vrm.get("meta") if isinstance(vrm.get("meta"), dict) else {}
        title, author = meta.get("title"), meta.get("author")
        license_name = meta.get("licenseName") or ""
        if license_name == "Other" and meta.get("otherLicenseUrl"):
            license_name = meta.get("otherLicenseUrl")
        redistribution = None
        groups = (vrm.get("blendShapeMaster") or {}).get("blendShapeGroups") or []
        expressions = [str(g.get("presetName")) for g in groups if isinstance(g, dict)
                       and g.get("presetName") not in (None, "", "unknown")]
        has_bones = bool((vrm.get("humanoid") or {}).get("humanBones"))
    else:
        raise ModelError("not_vrm", "Это обычный glTF без расширения VRM — нет костей и выражений для анимации",
                         "This is a plain glTF without the VRM extension — no bones or expressions to animate")
    if not has_bones:
        raise ModelError("no_humanoid", "В модели нет гуманоидного скелета VRM",
                         "The model has no VRM humanoid skeleton")
    for key in ("buffers", "images"):
        for item in doc.get(key) or []:
            uri = item.get("uri") if isinstance(item, dict) else None
            if uri and not str(uri).startswith("data:"):
                raise ModelError("external_uri", "Модель ссылается на внешние файлы — нужен самодостаточный .vrm",
                                 "The model refers to external files — a self-contained .vrm is needed")
    if doc.get("bufferViews") and not bin_len and not any(
            isinstance(b, dict) and str(b.get("uri", "")).startswith("data:") for b in doc.get("buffers") or []):
        raise ModelError("bad_bin", "В модели нет двоичных данных", "The model has no binary data")
    return {
        "kind": "vrm", "vrm_version": version,
        "title": str(title or "")[:120], "author": str(author or "")[:160],
        "license": str(license_name or "")[:300],
        "allow_redistribution": redistribution if isinstance(redistribution, bool) else None,
        "expressions": sorted(set(e for e in expressions if e))[:60],
    }

_WIN_RESERVED = re.compile(r"^(?:con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³]|conin\$|conout\$)(?:\..*)?$", re.I)
_WIN_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')

def _safe_member(name: str) -> Optional[str]:
    """Normalise a zip member path; ``None`` for anything that could escape.

    Round 23: also refuses names Windows treats specially — ``a.txt:stream`` (NTFS streams),
    ``CON.png``/``NUL`` (devices), components ending in a dot or a space (Win32 silently strips
    them, so ``".. "`` turns into ``".."``) and the characters Windows forbids in file names."""
    name = name.replace("\\", "/")
    if not name or name.startswith("/") or re.match(r"^[A-Za-z]:", name) or "\x00" in name:
        return None
    parts = [p for p in PurePosixPath(name).parts if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    for part in parts:
        if _WIN_BAD_CHARS.search(part) or part.endswith((".", " ")) or _WIN_RESERVED.match(part):
            return None
    return "/".join(parts)

def validate_live2d_zip(data: bytes) -> Tuple[Dict[str, Any], List[Tuple[zipfile.ZipInfo, str]]]:
    """Check a Live2D Cubism 3/4/5 model zip; return (meta, safe members).

    Refuses zip-slip paths, symlinks, absurd compression ratios, too many or
    too large files, Cubism 2 (``*.model.json`` / ``.moc``) and models whose
    ``model3.json`` points at missing moc3/texture files.
    """
    if len(data) > MAX_ZIP_BYTES:
        raise ModelError("too_large", "Архив больше 200 МБ", "The archive is larger than 200 MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ModelError("not_zip", "Файл не является zip-архивом", "The file is not a zip archive")
    infos = [i for i in archive.infolist() if not i.is_dir()]
    if len(infos) > MAX_ZIP_FILES:
        raise ModelError("too_many_files", "В архиве слишком много файлов", "The archive has too many files")
    for info in infos:
        if info.flag_bits & 0x1:
            raise ModelError("encrypted", "Архив защищён паролем — распакуйте его и запакуйте без пароля",
                             "The archive is password-protected — unpack it and zip it again without a password")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ModelError("bad_zip", "Архив сжат неподдерживаемым методом — пересохраните его как обычный .zip",
                             "The archive uses an unsupported compression method — save it again as a regular .zip")
    members: List[Tuple[zipfile.ZipInfo, str]] = []
    total = 0
    for info in infos:
        safe = _safe_member(info.filename)
        if safe is None:
            raise ModelError("unsafe_path", f"Небезопасный путь в архиве: {info.filename[:120]}",
                             f"Unsafe path in the archive: {info.filename[:120]}")
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise ModelError("symlink", "Архив содержит символические ссылки", "The archive contains symbolic links")
        total += info.file_size
        if total > MAX_UNPACKED_BYTES:
            raise ModelError("too_large", "Распакованный архив больше 400 МБ",
                             "The unpacked archive is larger than 400 MB")
        if info.compress_size and info.file_size / max(1, info.compress_size) > MAX_RATIO and info.file_size > 1_000_000:
            raise ModelError("zip_bomb", "Подозрительно сильно сжатый файл в архиве",
                             "A suspiciously highly compressed file in the archive")
        members.append((info, safe))
    names = {safe: info for info, safe in members}
    model_files = sorted((n for n in names if n.lower().endswith(".model3.json")),
                         key=lambda n: (n.count("/"), n))
    if not model_files:
        if any(n.lower().endswith(".model.json") or n.lower().endswith(".moc") for n in names):
            raise ModelError("cubism2", "Это модель Cubism 2 — поддерживаются Cubism 3, 4 и 5 (*.model3.json)",
                             "This is a Cubism 2 model — Cubism 3, 4 and 5 (*.model3.json) are supported")
        raise ModelError("no_model3", "В архиве нет файла *.model3.json", "The archive has no *.model3.json file")
    entry = model_files[0]
    try:
        settings = json.loads(_read_member(archive, names[entry]).decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, KeyError):
        raise ModelError("bad_json", "Файл model3.json повреждён", "The model3.json file is damaged")
    refs = settings.get("FileReferences") if isinstance(settings, dict) else None
    if not isinstance(refs, dict) or not refs.get("Moc"):
        raise ModelError("bad_json", "В model3.json нет ссылки на .moc3", "model3.json does not refer to a .moc3 file")
    base = PurePosixPath(entry).parent

    def resolve(rel: Any) -> str:
        safe = _safe_member(str(PurePosixPath(base, str(rel)))) if rel else None
        if safe is None or safe not in names:
            raise ModelError("missing_file", f"В архиве нет файла, на который ссылается модель: {str(rel)[:120]}",
                             f"The archive lacks a file the model refers to: {str(rel)[:120]}")
        return safe

    moc = resolve(refs["Moc"])
    if _read_member(archive, names[moc])[:4] != b"MOC3":
        raise ModelError("bad_moc", "Файл .moc3 повреждён", "The .moc3 file is damaged")
    textures = [resolve(t) for t in (refs.get("Textures") or [])]
    if not textures:
        raise ModelError("no_textures", "В модели нет текстур", "The model has no textures")
    expressions = [str(e.get("Name") or "") for e in (refs.get("Expressions") or []) if isinstance(e, dict)]
    for e in refs.get("Expressions") or []:
        if isinstance(e, dict) and e.get("File"):
            resolve(e["File"])
    motions = {str(group): len(items or []) for group, items in (refs.get("Motions") or {}).items()} \
        if isinstance(refs.get("Motions"), dict) else {}
    meta = {
        "kind": "live2d", "entry": entry, "title": PurePosixPath(entry).name[:-len(".model3.json")][:120],
        "expressions": [e for e in expressions if e][:80], "motions": motions,
        "author": "", "license": "", "allow_redistribution": None,
    }
    return meta, members

def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """``archive.read`` with every low-level failure turned into a readable ModelError."""
    try:
        return archive.read(info)
    except ModelError:
        raise
    except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError, OSError) as exc:
        raise ModelError("bad_zip", f"Архив повреждён: {info.filename[:120]} ({type(exc).__name__})",
                         f"The archive is damaged: {info.filename[:120]} ({type(exc).__name__})") from None

class Library:
    """The user's imported models; the sample is listed but never stored here."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    def _meta(self, folder: Path) -> Optional[Dict[str, Any]]:
        try:
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(meta, dict) or meta.get("id") != folder.name:
            return None
        return meta

    def list(self) -> List[Dict[str, Any]]:
        rows = [dict(SAMPLE)]
        if self.models_dir.is_dir():
            items = []
            for folder in self.models_dir.iterdir():
                if folder.is_dir() and not folder.is_symlink():
                    meta = self._meta(folder)
                    if meta:
                        items.append(public_model(meta))
            rows += sorted(items, key=lambda m: m.get("created", 0))
        return rows

    def get(self, model_id: str) -> Optional[Dict[str, Any]]:
        model_id = normalize_model_id(model_id)
        if model_id == SAMPLE_ID:
            return dict(SAMPLE)
        folder = self.models_dir / model_id
        meta = self._meta(folder) if folder.is_dir() else None
        return public_model(meta) if meta else None

    def import_bytes(self, filename: str, data: bytes, name: str = "") -> Dict[str, Any]:
        lower = (filename or "").lower()
        if lower.endswith(".vrm") or data[:4] == b"glTF":
            meta = validate_vrm(data)
            members = None
        elif lower.endswith(".zip") or data[:2] == b"PK":
            meta, members = validate_live2d_zip(data)
        else:
            raise ModelError("unsupported", "Поддерживаются .vrm и .zip с моделью Live2D (*.model3.json)",
                             "Supported: .vrm and .zip with a Live2D model (*.model3.json)")
        with self._lock:
            existing = [m for m in self.list() if not m.get("builtin")]
            if len(existing) >= MAX_MODELS:
                raise ModelError("too_many_models", f"В библиотеке уже {MAX_MODELS} моделей — удалите ненужные",
                                 f"The library already has {MAX_MODELS} models — delete the ones you do not need")
            model_id = "m-" + uuid.uuid4().hex[:10]
            folder = self.models_dir / model_id
            tmp = self.models_dir / (".tmp-" + model_id)
            tmp.mkdir(parents=True, exist_ok=False)
            try:
                if members is None:
                    (tmp / "model.vrm").write_bytes(data)
                    meta["file"] = "model.vrm"
                else:
                    archive = zipfile.ZipFile(io.BytesIO(data))
                    for info, safe in members:
                        target = tmp.joinpath(*safe.split("/"))
                        if tmp.resolve() not in target.resolve().parents:
                            raise ModelError("unsafe_path", "Небезопасный путь в архиве", "Unsafe path in the archive")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            with archive.open(info) as src, open(target, "wb") as dst:
                                shutil.copyfileobj(src, dst, 1024 * 1024)
                        except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError) as exc:

                            raise ModelError("bad_zip",
                                             f"Архив повреждён: {info.filename[:120]} ({type(exc).__name__})",
                                                         f"The archive is damaged: {info.filename[:120]} "
                                                         f"({type(exc).__name__})") from None
                    meta["file"] = meta["entry"]
                display = (name or meta.get("title") or PurePosixPath(filename or "model").stem).strip()
                meta.update({"id": model_id, "name": display[:80] or model_id,
                             "source_file": PurePosixPath((filename or "").replace("\\", "/")).name[:160],
                             "size": len(data), "created": time.time_ns() // 1000})
                (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.rename(folder)
            except Exception:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
        return public_model(meta)

    def rename(self, model_id: str, name: str) -> Dict[str, Any]:
        model_id = normalize_model_id(model_id)
        folder = self.models_dir / model_id
        meta = self._meta(folder) if model_id != SAMPLE_ID and folder.is_dir() else None
        if not meta:
            raise ModelError("not_found", "Модель не найдена", "Model not found")
        meta["name"] = (str(name or "").strip() or meta["name"])[:80]
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        return public_model(meta)

    def delete(self, model_id: str) -> bool:
        model_id = normalize_model_id(model_id)
        if model_id == SAMPLE_ID:
            raise ModelError("builtin", "Встроенную модель удалить нельзя", "The built-in model cannot be deleted")
        folder = self.models_dir / model_id
        if not folder.is_dir() or folder.is_symlink() or not self._meta(folder):
            return False
        shutil.rmtree(folder)
        return True

    def file(self, model_id: str, rel: str) -> Optional[Path]:
        """Resolve a file of an imported model; ``None`` when it escapes the folder."""
        model_id = normalize_model_id(model_id)
        if model_id == SAMPLE_ID:
            return None
        safe = _safe_member(rel or "")
        if safe is None or safe == "meta.json":
            return None
        folder = (self.models_dir / model_id).resolve()
        target = folder.joinpath(*safe.split("/")).resolve()
        if folder not in target.parents or not target.is_file():
            return None
        return target

def public_model(meta: Dict[str, Any]) -> Dict[str, Any]:
    row = {k: meta.get(k) for k in ("id", "kind", "name", "title", "author", "license", "vrm_version",
                                    "allow_redistribution", "expressions", "motions", "size", "created",
                                    "source_file", "file")}
    row["builtin"] = False
    row["url"] = f"/api/companion/models/{meta['id']}/files/{meta['file']}"
    return {k: v for k, v in row.items() if v is not None}

LIVE2D_LICENSE_URL = "https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html"
LIVE2D_EXPANDABLE_URL = "https://www.live2d.com/en/sdk/license/expandable/"

RUNTIME_FILES: Dict[str, Tuple[str, Optional[str], bytes]] = {
    "live2dcubismcore.min.js": (
        "https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js",
        None,
        b"Live2DCubismCore"),
    "pixi.min.js": (
        "https://cdn.jsdelivr.net/npm/pixi.js@6.5.10/dist/browser/pixi.min.js",
        "403f2f2ee8145fa17f60c5c89403056efe2680e5096ec2762036486914ed19c5", b"PIXI"),
    "unsafe-eval.min.js": (
        "https://cdn.jsdelivr.net/npm/@pixi/unsafe-eval@6.5.10/dist/browser/unsafe-eval.min.js",
        "94bb4bfdbd015087772ee03bc4117e253c326e8ca73a61646bd331b22f6847ad", b"@pixi/unsafe-eval"),
    "cubism4.min.js": (
        "https://cdn.jsdelivr.net/npm/pixi-live2d-display@0.4.0/dist/cubism4.min.js",
        "af1267e6d52759b245766c578d905bfa025b532d5c3cc727c370957c4409e21b", b"Live2DModel"),
}
RUNTIME_MAX_BYTES = 8 * 1024 * 1024

def runtime_status(root: Path) -> Dict[str, Any]:
    folder = Path(root) / "runtime"
    files = {name: (folder / name).is_file() for name in RUNTIME_FILES}
    return {"installed": all(files.values()), "files": files,
            "license_url": LIVE2D_LICENSE_URL, "expandable_url": LIVE2D_EXPANDABLE_URL}

def check_runtime_file(name: str, data: bytes) -> None:
    url, digest, marker = RUNTIME_FILES[name]
    if not data or len(data) > RUNTIME_MAX_BYTES:
        raise ModelError("runtime_size", f"{name}: неожиданный размер файла", f"{name}: unexpected file size")
    if digest and hashlib.sha256(data).hexdigest() != digest:
        raise ModelError("runtime_hash", f"{name}: контрольная сумма не совпала — файл не тот, что проверен",
                         f"{name}: checksum mismatch — not the verified file")
    if marker not in data:
        raise ModelError("runtime_marker", f"{name}: содержимое не похоже на ожидаемую библиотеку",
                         f"{name}: the content does not look like the expected library")

def runtime_file(root: Path, name: str) -> Optional[Path]:
    if name not in RUNTIME_FILES:
        return None
    path = Path(root) / "runtime" / name
    return path if path.is_file() else None

def load_state(root: Path) -> Dict[str, Any]:
    try:
        value = json.loads((Path(root) / "state.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

_STATE_LOCK = threading.Lock()

def save_state(root: Path, patch: Dict[str, Any]) -> Dict[str, Any]:

    with _STATE_LOCK:
        return _save_state(root, patch)

def _save_state(root: Path, patch: Dict[str, Any]) -> Dict[str, Any]:
    state = load_state(root)
    for key, value in (patch or {}).items():
        if key == "desktop" and isinstance(value, dict):
            rect = {}
            for k in ("x", "y", "w", "h"):
                if k in value:
                    rect[k] = _int(value[k], 0, -100000, 100000)
            if "w" in rect:
                rect["w"] = max(160, min(1600, rect["w"]))
            if "h" in rect:
                rect["h"] = max(200, min(2000, rect["h"]))
            state["desktop"] = {**(state.get("desktop") or {}), **rect}
        elif key == "quick" and isinstance(value, dict):

            quick = dict(state.get("quick") or {})
            if value.get("place") in ("inline", "desktop"):
                quick["place"] = value["place"]
            if isinstance(value.get("pinned"), bool):
                quick["pinned"] = value["pinned"]
            if value.get("align") in ("left", "center", "right"):
                quick["align"] = value["align"]
            state["quick"] = quick
        elif key == "desktop_by_monitor" and isinstance(value, dict):

            places = dict(state.get("desktop_by_monitor") or {})
            for mon, rect in list(value.items())[:16]:
                if isinstance(mon, str) and len(mon) <= 64 and isinstance(rect, dict) and all(k in rect for k in ("x", "y", "w", "h")):
                    places[mon] = {k: _int(rect[k], 0, -100000, 100000) for k in ("x", "y", "w", "h")}
            state["desktop_by_monitor"] = dict(list(places.items())[-16:])
        elif key == "live_window" and isinstance(value, dict):
            state["live_window"] = {k: float(max(0.0, min(1.0, float(value[k])))) for k in ("x", "y")
                                    if isinstance(value.get(k), (int, float))}
    Path(root).mkdir(parents=True, exist_ok=True)
    tmp = Path(root) / f"state.json.{uuid.uuid4().hex[:8]}.tmp"
    try:
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(Path(root) / "state.json")
    finally:
        tmp.unlink(missing_ok=True)
    return state
