"""Local, heuristic AI-generation provenance scan for an uploaded image.

This never calls the network: everything runs on the bytes the user gives
us. It looks for three kinds of evidence, roughly in order of how much they
are worth trusting:

  1. C2PA / Content Credentials hints — recognised container sections in
     PNG, JPEG and WebP. We do not parse or validate a signed manifest, so
     these are hints only; that limitation is spelled out in the result.
  2. IPTC DigitalSourceType — a controlled vocabulary term such as
     "trainedAlgorithmicMedia" embedded in XMP, per the IPTC photo metadata
     standard adopted for AI-generated image labelling.
  3. Plain metadata — PNG text chunks (Stable Diffusion/ComfyUI write their
     generation parameters here), EXIF Software/Make/Model tags, and known
     generator names in parsed metadata. Pixel bytes are never scanned.

None of this is cryptographic verification, and a cleared or stripped file
(most screenshots, re-saves and social-media re-uploads strip all of it)
will come back with nothing found — which proves nothing either way. That
caveat, plus links to the tools that *do* verify signed C2PA manifests, is
always included in the result so the UI cannot accidentally overstate it.
"""
from __future__ import annotations

import re
import struct
import zlib
from typing import Any, Dict, List, Optional, Tuple

PORTALS: List[Dict[str, str]] = [
    {"label": "Content Credentials Verify (C2PA official tool)", "url": "https://contentcredentials.org/verify"},
    {"label": "C2PA specification and conformant tools", "url": "https://c2pa.org/"},
    {"label": "Google SynthID (about, no public detector API)", "url": "https://deepmind.google/technologies/synthid/"},
]

CAVEATS_EN: List[str] = [
    "This is a local, heuristic scan of file metadata — not a cryptographic verification of a C2PA manifest, "
    "and not a watermark detector.",
    "Absence of any marker below proves nothing: most re-saves, screenshots, editors and social-media uploads strip "
    "all metadata, AI-generated or not.",
    "Presence of a marker is also not proof by itself — metadata can be edited or forged; only a validated, signed "
    "C2PA manifest (checked with an official tool, see the links) carries real assurance.",
    "SynthID and similar invisible watermarks are not publicly checkable through any API — the only official way is "
    "the vendor's own tool, where one exists.",
]
CAVEATS_RU: List[str] = [
    "Это локальная эвристическая проверка метаданных файла — не криптографическая проверка манифеста C2PA и не детектор водяных знаков.",
    "Отсутствие маркеров ниже ничего не доказывает: большинство пересохранений, скриншотов, редакторов и загрузок в соцсети стирают все метаданные — независимо от происхождения.",
    "Наличие маркера тоже не является доказательством само по себе — метаданные можно отредактировать или подделать; настоящую гарантию даёт только подписанный и проверенный манифест C2PA (см. ссылки на официальные инструменты).",
    "SynthID и похожие невидимые водяные знаки нельзя публично проверить через API — официальный способ есть только у части поставщиков и работает лишь через их собственный инструмент.",
]

# Known generator name substrings -> a human label. Matched case-insensitively
# against extracted PNG/XMP/EXIF metadata, not arbitrary image bytes.
_GENERATOR_PATTERNS: List[Tuple[str, str]] = [
    (r"gpt-image", "OpenAI gpt-image"),
    (r"dall-?e", "OpenAI DALL-E"),
    (r"\bopenai\b", "OpenAI"),
    (r"midjourney", "Midjourney"),
    (r"stable[ -]?diffusion", "Stable Diffusion"),
    (r"automatic1111", "Stable Diffusion (AUTOMATIC1111)"),
    (r"comfyui", "Stable Diffusion (ComfyUI)"),
    (r"stability\.?ai", "Stability AI"),
    (r"adobe firefly|firefly", "Adobe Firefly"),
    (r"\bimagen\b", "Google Imagen"),
    (r"synthid", "Google (SynthID-marked)"),
    (r"bing image creator", "Microsoft Bing Image Creator"),
    (r"microsoft designer", "Microsoft Designer"),
    (r"leonardo\.ai|leonardo ai", "Leonardo AI"),
    (r"novelai", "NovelAI"),
    (r"dreamstudio", "DreamStudio"),
    (r"canva", "Canva (may include AI features)"),
    (r"grok imagine|xai", "xAI (Grok Imagine)"),
]

_C2PA_MARKERS = [b"c2pa", b"C2PA", b"jumbf", b"JUMBF", b"application/c2pa", b"urn:c2pa", b"c2pa.claim",
                 b"c2pa.assertions", b"c2pa.manifest", b"contentcredentials", b"Content Credentials"]

_DIGITAL_SOURCE_RE = re.compile(rb"digitalsourcetype[^a-zA-Z]{0,8}([a-zA-Z]+)", re.I)
_DIGITAL_SOURCE_URL_RE = re.compile(rb"newscodes/digitalsourcetype/([a-zA-Z]+)", re.I)

# EXIF ASCII tags worth reading (tag id -> label).
_EXIF_ASCII_TAGS = {0x010F: "Make", 0x0110: "Model", 0x0131: "Software", 0x010E: "ImageDescription", 0x013B: "Artist"}


def _match_generator(haystack: bytes) -> Optional[str]:
    for pattern, label in _GENERATOR_PATTERNS:
        if re.search(pattern.encode("ascii"), haystack, re.I):
            return label
    return None


def _c2pa_hint(data: bytes) -> bool:
    return any(marker in data for marker in _C2PA_MARKERS)


def _digital_source_type(data: bytes) -> str:
    match = _DIGITAL_SOURCE_URL_RE.search(data) or _DIGITAL_SOURCE_RE.search(data)
    return match.group(1).decode("ascii", "ignore") if match else ""


def _png_chunks(data: bytes) -> List[Tuple[str, bytes]]:
    chunks: List[Tuple[str, bytes]] = []
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return chunks
    offset = 8
    while offset + 12 <= len(data):
        try:
            length = struct.unpack(">I", data[offset:offset + 4])[0]
        except struct.error:
            break
        if length > len(data) - offset - 12:
            break
        kind_bytes = data[offset + 4:offset + 8]
        kind = kind_bytes.decode("ascii", "replace")
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])[0]
        if zlib.crc32(kind_bytes + payload) & 0xFFFFFFFF != crc:
            break
        chunks.append((kind, payload))
        offset += 8 + length + 4  # length + type + data + crc
        if kind == "IEND":
            break
    return chunks


def _inflate_limited(payload: bytes, limit: int = 65_536) -> bytes:
    try:
        decoder = zlib.decompressobj()
        result = decoder.decompress(payload, limit + 1)
        if len(result) > limit or decoder.unconsumed_tail or not decoder.eof:
            return b""
        return result
    except zlib.error:
        return b""


def _png_text(kind: str, payload: bytes) -> Tuple[str, str]:
    """Decode PNG text chunks, capping decompressed metadata at 64 KiB."""
    if b"\x00" not in payload:
        return "", ""
    keyword, _, text = payload.partition(b"\x00")
    try:
        if kind == "zTXt":
            text = _inflate_limited(text[1:]) if text[:1] == b"\x00" else b""
        elif kind == "iTXt":
            if len(text) < 3 or text[0] not in (0, 1) or text[1] != 0:
                return "", ""
            fields = text[2:].split(b"\x00", 2)
            if len(fields) != 3:
                return "", ""
            text = _inflate_limited(fields[2]) if text[0] else fields[2]
        if not text:
            return "", ""
        return keyword.decode("latin-1"), text[:65_536].decode("utf-8" if kind == "iTXt" else "latin-1", "replace")
    except Exception:
        return "", ""


def _exif_ascii_tags(exif: bytes) -> Dict[str, str]:
    """Minimal TIFF/IFD0 reader for a handful of ASCII tags. Returns {} on
    anything unexpected rather than raising — this is best-effort metadata,
    not something the scan should ever crash on."""
    found: Dict[str, str] = {}
    try:
        if exif[:6] == b"Exif\x00\x00":
            exif = exif[6:]
        if exif[:2] == b"II":
            endian = "<"
        elif exif[:2] == b"MM":
            endian = ">"
        else:
            return found
        ifd0_offset = struct.unpack(endian + "I", exif[4:8])[0]
        count = struct.unpack(endian + "H", exif[ifd0_offset:ifd0_offset + 2])[0]
        entry_base = ifd0_offset + 2
        for index in range(min(count, 64)):
            entry = exif[entry_base + index * 12: entry_base + index * 12 + 12]
            if len(entry) < 12:
                break
            tag, typ, cnt = struct.unpack(endian + "HHI", entry[:8])
            if tag not in _EXIF_ASCII_TAGS or typ != 2:  # type 2 = ASCII
                continue
            value_bytes = entry[8:12]
            if cnt <= 4:
                raw = value_bytes[:cnt]
            else:
                value_offset = struct.unpack(endian + "I", value_bytes)[0]
                raw = exif[value_offset:value_offset + cnt]
            text = raw.split(b"\x00", 1)[0].decode("latin-1", "replace").strip()
            if text:
                found[_EXIF_ASCII_TAGS[tag]] = text
    except Exception:
        return found
    return found


def _sniff_format(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "TIFF"
    return "unknown"


def scan(data: bytes, filename: str = "", language: str = "en") -> Dict[str, Any]:
    """Scan raw file bytes and return a provenance report. Pure, offline,
    deterministic — safe to unit-test with hand-built fixtures."""
    findings: List[Dict[str, str]] = []
    fmt = _sniff_format(data)
    claimed_generator = ""
    metadata: List[bytes] = []

    def record_metadata(payload: bytes) -> None:
        nonlocal claimed_generator
        metadata.append(payload[:65_536])
        guess = _match_generator(payload)
        if guess and not claimed_generator:
            claimed_generator = guess

    if fmt == "PNG":
        for kind, payload in _png_chunks(data):
            if kind in ("tEXt", "iTXt", "zTXt"):
                keyword, text = _png_text(kind, payload)
                if not keyword:
                    continue
                record_metadata(text.encode("utf-8", "replace"))
                if keyword.lower() in ("parameters", "prompt", "workflow"):
                    findings.append({"source": f"png_text:{keyword}", "tag": "generation_parameters",
                                     "excerpt": text[:200]})
                elif keyword.lower() in ("software", "comment", "description"):
                    findings.append({"source": f"png_text:{keyword}", "tag": "software_or_comment", "excerpt": text[:200]})
            elif kind == "eXIf":
                tags = _exif_ascii_tags(payload)
                for label, value in tags.items():
                    findings.append({"source": f"png_exif:{label}", "tag": "exif", "excerpt": value[:200]})
                    record_metadata(value.encode("utf-8", "replace"))
            elif kind == "caBX":
                findings.append({"source": "png_cabx", "tag": "c2pa_jumbf_hint", "excerpt": ""})
    elif fmt == "JPEG":
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                break
            marker = data[offset + 1]
            if marker == 0xD9:  # anything after EOI is outside the image
                break
            if marker == 0xD8 or 0xD0 <= marker <= 0xD7:
                offset += 2
                continue
            if offset + 4 > len(data):
                break
            seg_len = struct.unpack(">H", data[offset + 2:offset + 4])[0]
            if seg_len < 2 or offset + 2 + seg_len > len(data):
                break
            segment = data[offset + 4: offset + 2 + seg_len]
            if marker == 0xE1 and segment[:6] == b"Exif\x00\x00":
                tags = _exif_ascii_tags(segment)
                for label, value in tags.items():
                    findings.append({"source": f"exif:{label}", "tag": "exif", "excerpt": value[:200]})
                    record_metadata(value.encode("utf-8", "replace"))
            elif marker == 0xE1 and (b"xap" in segment[:40].lower() or b"<rdf:" in segment[:40].lower()):
                findings.append({"source": "xmp_packet", "tag": "xmp", "excerpt": segment[:200].decode("latin-1", "replace")})
                record_metadata(segment)
            elif marker == 0xEB:  # APP11: JPEG XT / JUMBF, used to carry C2PA
                if b"jumb" in segment.lower() and _c2pa_hint(segment):
                    findings.append({"source": "app11_jumbf", "tag": "c2pa_jumbf_hint", "excerpt": ""})
                    record_metadata(segment)
            if marker == 0xDA:  # start of scan: no more markers follow
                break
            offset += 2 + seg_len
    elif fmt == "WEBP":
        riff_end = min(len(data), 8 + struct.unpack("<I", data[4:8])[0])
        offset = 12
        while offset + 8 <= riff_end:
            kind = data[offset:offset + 4]
            length = struct.unpack("<I", data[offset + 4:offset + 8])[0]
            if length > riff_end - offset - 8:
                break
            payload = data[offset + 8:offset + 8 + length]
            if kind in (b"XMP ", b"EXIF"):
                record_metadata(payload)
            elif kind == b"C2PA":
                findings.append({"source": "webp_c2pa", "tag": "c2pa_jumbf_hint", "excerpt": ""})
            offset += 8 + length + (length & 1)

    # Only inspect parsed metadata. Arbitrary strings in pixels or compressed
    # image data are not evidence of provenance.
    digital_source = next((value for item in metadata if (value := _digital_source_type(item))), "")
    if digital_source:
        findings.append({"source": "metadata", "tag": "iptc_digital_source_type", "excerpt": digital_source})
    if not claimed_generator and any(f["source"] == "png_text:parameters" for f in findings):
        # A1111/Stable-Diffusion-family tools write their generation params under
        # this exact PNG key without necessarily naming themselves in the text.
        claimed_generator = "Likely Stable Diffusion-family local pipeline (found 'parameters' metadata typical of that tooling)"

    signals = {
        "c2pa_jumbf_hint": any(f["tag"] == "c2pa_jumbf_hint" for f in findings),
        "iptc_digital_source_type": digital_source,
        "generation_parameters_found": any(f["tag"] == "generation_parameters" for f in findings),
    }
    ai_source_type = bool(digital_source and "algorithm" in digital_source.lower())

    return {
        "filename": str(filename or "")[:200],
        "format": fmt,
        "size_bytes": len(data),
        "claimed_generator": claimed_generator,
        "signals": signals,
        "ai_digital_source_type": ai_source_type,
        "findings": findings[:30],
        "caveats": CAVEATS_RU if language == "ru" else CAVEATS_EN,
        "portals": PORTALS,
    }
