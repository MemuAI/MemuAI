"""Round 32 (photo): a small Photoshop PSD reader/writer — layers, groups, masks, opacity, blend modes.

Reader: PSD version 1, 8 bits per channel, RGB / Grayscale (other modes → ``UnsupportedPSD``, the caller falls back to
Pillow's merged image). Channel compression raw / RLE (PackBits) / ZIP / ZIP+prediction. Unicode layer names
(``luni``), groups (``lsct`` section dividers) and user masks (channel -2) are understood.

Writer: RGB + alpha; flat channels (alpha, masks, empty / graphic layers) PackBits (RLE) like Photoshop's own files,
photo channels raw (read by Photoshop, GIMP, Krita, Photopea, Pillow, psd-tools), groups,
masks, Unicode names, a merged image so viewers without layer support show the picture.

Pure Python + zlib; pixel planes are handled with ``bytes`` slicing (no per-pixel Python loops when writing).
"""
from __future__ import annotations

import io
import re
import struct
import zlib
from typing import Any, Dict, List, Optional, Tuple

BLEND_TO_CSS = {
    b"norm": "normal", b"mul ": "multiply", b"scrn": "screen", b"over": "overlay", b"dark": "darken", b"lite": "lighten",
    b"div ": "color-dodge", b"idiv": "color-burn", b"hLit": "hard-light", b"sLit": "soft-light", b"diff": "difference",
    b"smud": "exclusion", b"hue ": "hue", b"sat ": "saturation", b"colr": "color", b"lum ": "luminosity",
    b"pass": "normal", b"diss": "normal", b"lbrn": "color-burn", b"lddg": "color-dodge", b"vLit": "hard-light",
    b"lLit": "hard-light", b"pLit": "hard-light", b"hMix": "hard-light", b"dkCl": "darken", b"lgCl": "lighten",
    b"fsub": "difference", b"fdiv": "color-dodge",
}
CSS_TO_BLEND = {v: k for k, v in BLEND_TO_CSS.items() if k not in (b"pass", b"diss", b"lbrn", b"lddg", b"vLit", b"lLit",
                                                                    b"pLit", b"hMix", b"dkCl", b"lgCl", b"fsub", b"fdiv")}
MAX_SIDE = 30000


class UnsupportedPSD(ValueError):
    pass


# ----------------------------------------------------------------------------- PackBits
def unpackbits(data: bytes, size: int) -> bytes:
    out = bytearray()
    i, n = 0, len(data)
    while i < n and len(out) < size:
        h = data[i]
        i += 1
        if h < 128:
            out += data[i:i + h + 1]
            i += h + 1
        elif h > 128:
            out += bytes([data[i]]) * (257 - h)
            i += 1
    if len(out) < size:
        out += b"\x00" * (size - len(out))
    return bytes(out[:size])


_RUN = re.compile(rb"(.)\1{2,}", re.S)


def _literal(chunk: bytes, parts: List[bytes]) -> None:
    n = len(chunk)
    full = n - n % 128
    if full:
        parts.extend([b"\x7f" + chunk[k:k + 128] for k in range(0, full, 128)])
    if n > full:
        parts.append(bytes((n - full - 1,)) + chunk[full:])


def packbits_row(row: bytes) -> bytes:
    """PackBits (PSD "RLE") for one row: runs of ≥3 equal bytes → repeat packets, the rest → literal packets ≤128."""
    parts: List[bytes] = []
    pos = 0
    for m in _RUN.finditer(row):
        if m.start() > pos:
            _literal(row[pos:m.start()], parts)
        n, b = m.end() - m.start(), m.group(1)
        if n >= 128:
            parts.append((bytes((129,)) + b) * (n // 128))
            n %= 128
        if n >= 3:
            parts.append(bytes((257 - n,)) + b)
        elif n:
            _literal(b * n, parts)
        pos = m.end()
    if pos < len(row):
        _literal(row[pos:], parts)
    return b"".join(parts)


def _rle_plane(plane: bytes, w: int, h: int) -> Tuple[List[int], bytes]:
    rows = [packbits_row(plane[y * w:(y + 1) * w]) for y in range(h)]
    return [len(r) for r in rows], b"".join(rows)


def _worth_rle(plane: bytes, w: int, h: int) -> bool:
    """Flat planes (alpha, empty layers, masks, graphics) shrink a lot with PackBits; photo channels do not (and
    encoding them in Python is slow), so they stay raw. Decided on every 16th row with a fast zlib probe."""
    if w * h < 4096:
        return True
    step = max(1, h // 64)
    sample = b"".join(plane[y * w:(y + 1) * w] for y in range(0, h, step))
    return len(zlib.compress(sample, 1)) < len(sample) * 0.35


def _unpredict(raw: bytes, w: int, h: int) -> bytes:
    out = bytearray(raw)
    for y in range(h):
        row = y * w
        for x in range(1, w):
            out[row + x] = (out[row + x] + out[row + x - 1]) & 0xFF
    return bytes(out)


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.d = data
        self.p = 0

    def take(self, n: int) -> bytes:
        if self.p + n > len(self.d):
            raise ValueError("truncated PSD")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u8(self) -> int: return self.take(1)[0]
    def u16(self) -> int: return struct.unpack(">H", self.take(2))[0]
    def i16(self) -> int: return struct.unpack(">h", self.take(2))[0]
    def u32(self) -> int: return struct.unpack(">I", self.take(4))[0]
    def i32(self) -> int: return struct.unpack(">i", self.take(4))[0]


def _read_channel(r: _Reader, w: int, h: int, length: int) -> bytes:
    end = r.p + length
    if w <= 0 or h <= 0:
        r.p = end
        return b""
    comp = r.u16()
    size = w * h
    if comp == 0:
        data = r.take(min(size, max(0, end - r.p)))
        data = data + b"\x00" * (size - len(data))
    elif comp == 1:
        counts = [r.u16() for _ in range(h)]
        rows = []
        for c in counts:
            rows.append(unpackbits(r.take(c), w))
        data = b"".join(rows)
    elif comp in (2, 3):
        raw = zlib.decompress(r.take(end - r.p))
        data = _unpredict(raw, w, h) if comp == 3 else raw
        data = (data + b"\x00" * size)[:size]
    else:
        raise UnsupportedPSD(f"compression {comp}")
    r.p = end
    return data


def _interleave(planes: List[bytes], w: int, h: int) -> bytes:
    """[R, G, B, A] planes → RGBA bytes (via bytearray slice assignment: fast)."""
    out = bytearray(w * h * 4)
    for k, plane in enumerate(planes):
        out[k::4] = plane
    return bytes(out)


def _png(rgba: bytes, w: int, h: int, mode: str = "RGBA") -> bytes:
    from PIL import Image
    img = Image.frombytes(mode, (w, h), rgba)
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=1)
    return buf.getvalue()


def read_psd(data: bytes, with_pixels: bool = True) -> Dict[str, Any]:
    """``{width, height, layers:[tree], merged: png|None}``; a layer: ``{name, x, y, w, h, opacity, blend, visible,
    png, mask?, children? (group), open?}``."""
    r = _Reader(data)
    if r.take(4) != b"8BPS":
        raise ValueError("not a PSD file")
    version = r.u16()
    if version != 1:
        raise UnsupportedPSD("PSB (large document) is not supported")
    r.take(6)
    channels = r.u16()
    height, width = r.u32(), r.u32()
    depth, mode = r.u16(), r.u16()
    if depth != 8 or mode not in (1, 3):
        raise UnsupportedPSD(f"depth {depth} / colour mode {mode}")
    if width > MAX_SIDE or height > MAX_SIDE:
        raise UnsupportedPSD("too large")
    r.take(r.u32())          # colour mode data
    r.take(r.u32())          # image resources
    lmi_len = r.u32()
    lmi_end = r.p + lmi_len
    flat: List[Dict[str, Any]] = []
    if lmi_len:
        li_len = r.u32()
        li_end = r.p + li_len
        if li_len:
            count = abs(r.i16())
            records = []
            for _ in range(count):
                top, left, bottom, right = r.i32(), r.i32(), r.i32(), r.i32()
                nch = r.u16()
                chans = [(r.i16(), r.u32()) for _ in range(nch)]
                if r.take(4) != b"8BIM":
                    raise ValueError("bad layer record")
                key = r.take(4)
                opacity, clipping, flags = r.u8(), r.u8(), r.u8()
                r.u8()
                extra_len = r.u32()
                extra_end = r.p + extra_len
                mask = None
                mlen = r.u32()
                if mlen >= 20:
                    mt, ml, mb, mr = r.i32(), r.i32(), r.i32(), r.i32()
                    mdef, mflags = r.u8(), r.u8()
                    r.take(mlen - 18)
                    mask = {"x": ml, "y": mt, "w": mr - ml, "h": mb - mt, "default": mdef, "disabled": bool(mflags & 2)}
                else:
                    r.take(mlen)
                r.take(r.u32())                 # blending ranges
                nlen = r.u8()
                name = r.take(nlen).decode("latin-1", "replace")
                r.take((4 - (nlen + 1) % 4) % 4)
                section = 0
                while r.p + 12 <= extra_end:
                    sig = r.take(4)
                    if sig not in (b"8BIM", b"8B64"):
                        break
                    akey = r.take(4)
                    alen = r.u32()
                    adata = r.take(alen)
                    if alen % 2 and r.p < extra_end:
                        pass
                    if akey == b"luni" and len(adata) >= 4:
                        n = struct.unpack(">I", adata[:4])[0]
                        try:
                            name = adata[4:4 + n * 2].decode("utf-16-be").rstrip("\x00")
                        except UnicodeDecodeError:
                            pass
                    elif akey in (b"lsct", b"lsdk") and len(adata) >= 4:
                        section = struct.unpack(">I", adata[:4])[0]
                        if len(adata) >= 12 and adata[4:8] == b"8BIM":
                            key = adata[8:12]
                r.p = extra_end
                records.append({"top": top, "left": left, "bottom": bottom, "right": right, "chans": chans,
                                "key": key, "opacity": opacity, "clipping": clipping, "visible": not (flags & 2),
                                "name": name, "mask": mask, "section": section})
            for rec in records:
                w, h = rec["right"] - rec["left"], rec["bottom"] - rec["top"]
                planes: Dict[int, bytes] = {}
                for cid, clen in rec["chans"]:
                    if cid == -2 and rec["mask"]:
                        m = rec["mask"]
                        planes[cid] = _read_channel(r, m["w"], m["h"], clen)
                    elif cid >= -1:
                        planes[cid] = _read_channel(r, w, h, clen)
                    else:
                        r.p += clen
                layer: Dict[str, Any] = {"name": rec["name"] or "Layer", "x": rec["left"], "y": rec["top"], "w": max(0, w),
                                         "h": max(0, h), "opacity": round(rec["opacity"] / 255, 4),
                                         "blend": BLEND_TO_CSS.get(rec["key"], "normal"), "visible": rec["visible"],
                                         "section": rec["section"], "clipping": bool(rec["clipping"])}
                if with_pixels and w > 0 and h > 0 and rec["section"] not in (1, 2, 3):
                    size = w * h
                    if mode == 3:
                        rgb = [planes.get(i, b"\x00" * size) for i in (0, 1, 2)]
                    else:
                        g = planes.get(0, b"\x00" * size)
                        rgb = [g, g, g]
                    alpha = planes.get(-1, b"\xff" * size)
                    layer["png"] = _png(_interleave(rgb + [alpha], w, h), w, h)
                if with_pixels and rec["mask"] and -2 in planes and rec["mask"]["w"] > 0 and rec["mask"]["h"] > 0:
                    m = rec["mask"]
                    layer["mask"] = {**m, "png": _png(planes[-2], m["w"], m["h"], "L")}
                flat.append(layer)
        r.p = li_end
    r.p = lmi_end
    merged = None
    if with_pixels and r.p + 2 <= len(data):
        try:
            comp = r.u16()
            size = width * height
            nplanes = channels
            if comp == 1:
                counts = [r.u16() for _ in range(height * nplanes)]
                planes = []
                for c in range(nplanes):
                    rows = [unpackbits(r.take(counts[c * height + y]), width) for y in range(height)]
                    planes.append(b"".join(rows))
            elif comp == 0:
                planes = [r.take(size) for _ in range(nplanes)]
            else:
                planes = []
            if planes:
                if mode == 3:
                    rgb = planes[:3]
                    alpha = planes[3] if len(planes) > 3 and not flat else b"\xff" * size
                else:
                    rgb = [planes[0]] * 3
                    alpha = b"\xff" * size
                merged = _png(_interleave(list(rgb) + [alpha], width, height), width, height)
        except (ValueError, struct.error):
            merged = None
    return {"width": width, "height": height, "layers": _tree(flat), "merged": merged}


def _tree(flat: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """File order is bottom→top: a bounding divider (3) opens a group, a folder record (1/2) closes it."""
    root: List[Dict[str, Any]] = []
    stack: List[List[Dict[str, Any]]] = [root]
    for layer in flat:
        section = layer.pop("section", 0)
        if section == 3:
            stack.append([])
            continue
        if section in (1, 2):
            children = stack.pop() if len(stack) > 1 else []
            layer.update(type="group", children=children, open=section == 1)
            layer.pop("png", None)
            stack[-1].append(layer)
            continue
        layer["type"] = "raster"
        stack[-1].append(layer)
    while len(stack) > 1:        # unbalanced file: keep the layers
        orphan = stack.pop()
        stack[-1].extend(orphan)
    return root


# ----------------------------------------------------------------------------- writer
def _pascal(name: str) -> bytes:
    raw = name.encode("latin-1", "replace")[:255]
    b = bytes([len(raw)]) + raw
    return b + b"\x00" * ((4 - len(b) % 4) % 4)


def _luni(name: str) -> bytes:
    u = name.encode("utf-16-be")
    body = struct.pack(">I", len(u) // 2) + u
    if len(body) % 2:
        body += b"\x00"
    return b"8BIMluni" + struct.pack(">I", len(body)) + body


def _rgba_planes(png: bytes, mode: str = "RGBA") -> Tuple[int, int, List[bytes]]:
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    img = img.convert(mode)
    w, h = img.size
    return w, h, [band.tobytes() for band in img.split()]


def write_psd(width: int, height: int, layers: List[Dict[str, Any]], merged_png: Optional[bytes]) -> bytes:
    """*layers*: tree bottom→top, like :func:`read_psd`; a raster layer carries ``png`` (its own pixels, at x/y)."""
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
        raise ValueError("bad size")
    records = bytearray()
    channel_data = bytearray()
    count = 0

    def emit(layer: Dict[str, Any], section: int = 0) -> None:
        nonlocal count
        name = str(layer.get("name") or "Layer")[:250]
        if section == 3:
            name = "</Layer group>"
        x, y, planes, w, h = 0, 0, [b"", b"", b"", b""], 0, 0
        if section == 0 and layer.get("png"):
            w, h, planes = _rgba_planes(layer["png"])
            x, y = int(layer.get("x") or 0), int(layer.get("y") or 0)
        chans = [(-1, planes[3]), (0, planes[0]), (1, planes[1]), (2, planes[2])]
        mask = layer.get("mask") if section in (0, 1, 2) else None
        mask_block = struct.pack(">I", 0)
        mw = mh = 0
        if mask and mask.get("png"):
            mw, mh, mplanes = _rgba_planes(mask["png"], "L")
            mx, my = int(mask.get("x") or 0), int(mask.get("y") or 0)
            flags = 2 if mask.get("disabled") else 0
            mask_block = struct.pack(">IiiiiBBH", 20, my, mx, my + mh, mx + mw, int(mask.get("default") or 0) & 255, flags, 0)
            chans.append((-2, mplanes[0]))
        # channel data: PackBits (compression 1) like Photoshop writes it — every reader (Photoshop, GIMP, Krita,
        # psd-tools, Pillow) supports it, and flat / transparent layers shrink a lot
        blobs = []
        for cid, plane in chans:
            cw, chh = (mw, mh) if cid == -2 else (w, h)
            if not plane or cw <= 0 or chh <= 0:
                blobs.append(struct.pack(">H", 0) + plane)
                continue
            if not _worth_rle(plane, cw, chh):
                blobs.append(struct.pack(">H", 0) + plane)
                continue
            counts, packed = _rle_plane(plane, cw, chh)
            blobs.append(struct.pack(">H", 1) + struct.pack(f">{chh}H", *counts) + packed)
        rec = bytearray()
        rec += struct.pack(">iiiiH", y, x, y + h, x + w, len(chans))
        for (cid, _plane), blob in zip(chans, blobs):
            rec += struct.pack(">hI", cid, len(blob))
        blend = CSS_TO_BLEND.get(str(layer.get("blend") or "normal"), b"norm")
        if section in (1, 2) and layer.get("pass_through", True) and blend == b"norm":
            blend = b"pass"
        opacity = max(0, min(255, round(float(layer.get("opacity", 1)) * 255)))
        flags = 0 if layer.get("visible", True) else 2
        if section:
            flags |= 0x18          # bits 3/4: "pixel data irrelevant" (Photoshop sets these for group records)
        rec += b"8BIM" + blend + struct.pack(">BBBB", opacity, 1 if layer.get("clipping") else 0, flags, 0)
        extra = bytearray()
        extra += mask_block
        extra += struct.pack(">I", 0)              # blending ranges
        extra += _pascal(name)
        extra += _luni(name)
        if section:
            lsct = struct.pack(">I", section)
            if section in (1, 2):
                lsct += b"8BIM" + blend
            extra += b"8BIMlsct" + struct.pack(">I", len(lsct)) + lsct
        rec += struct.pack(">I", len(extra)) + extra
        records.extend(rec)
        for blob in blobs:
            channel_data.extend(blob)
        count += 1

    def walk(items: List[Dict[str, Any]]) -> None:
        for layer in items:
            if layer.get("type") == "group":
                emit({"name": layer.get("name")}, 3)
                walk(layer.get("children") or [])
                emit(layer, 1 if layer.get("open", True) else 2)
            else:
                emit(layer)

    walk(layers)
    layer_info = struct.pack(">h", -count if count else 0) + bytes(records) + bytes(channel_data)
    if len(layer_info) % 2:
        layer_info += b"\x00"
    lmi = struct.pack(">I", len(layer_info)) + layer_info + struct.pack(">I", 0)
    out = bytearray()
    out += b"8BPS" + struct.pack(">H", 1) + b"\x00" * 6 + struct.pack(">HIIHH", 4, height, width, 8, 3)
    out += struct.pack(">I", 0) + struct.pack(">I", 0)
    out += struct.pack(">I", len(lmi)) + lmi
    if merged_png:
        mw, mh, planes = _rgba_planes(merged_png)
        if (mw, mh) != (width, height):
            raise ValueError("merged image size mismatch")
        planes = [planes[k] for k in (0, 1, 2, 3)]
    else:
        planes = [b"\xff" * (width * height)] * 3 + [b"\x00" * (width * height)]
    if all(_worth_rle(plane, width, height) for plane in planes[:3]):   # one compression for the whole merged image
        counts: List[int] = []
        packed = []
        for plane in planes:
            c, p = _rle_plane(plane, width, height)
            counts += c
            packed.append(p)
        out += struct.pack(">H", 1) + struct.pack(f">{len(counts)}H", *counts)
        for p in packed:
            out += p
    else:
        out += struct.pack(">H", 0)
        for plane in planes:
            out += plane
    return bytes(out)
