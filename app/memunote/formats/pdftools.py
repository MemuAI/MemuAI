"""PDF helpers of MemuNote (Round 31).

Editing itself happens in the page (pdf.js renders, pdf-lib writes, see ``static/js/pdfedit.js``);
the backend only supplies:

* :func:`unicode_font_path` — a system TrueType font with Cyrillic for text added to a PDF
  (the 14 standard PDF fonts are WinAnsi only: "Привет" cannot be written with them);
* :func:`extract` — text or Markdown of a PDF with ``pypdf`` (big PDFs, tests, AI context),
  headings guessed from font sizes, pages separated by ``---``.
"""
from __future__ import annotations

import os
import re
import statistics
from typing import List, Optional, Tuple

_FONT_CANDIDATES = {
    "": [
        # Windows (Arial / Segoe UI / Calibri / Tahoma all carry Cyrillic and Greek)
        "{windir}/Fonts/arial.ttf", "{windir}/Fonts/segoeui.ttf", "{windir}/Fonts/calibri.ttf", "{windir}/Fonts/tahoma.ttf",
        "{windir}/Fonts/verdana.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        # macOS
        "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
    ],
    "bold": [
        "{windir}/Fonts/arialbd.ttf", "{windir}/Fonts/segoeuib.ttf", "{windir}/Fonts/calibrib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ],
}


def unicode_font_path(style: str = "") -> Optional[str]:
    """Path of a ``.ttf`` (not ``.ttc``: pdf-lib embeds single fonts) with Cyrillic; None when none is found."""
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    for pattern in _FONT_CANDIDATES.get(style if style in _FONT_CANDIDATES else "", []) + _FONT_CANDIDATES[""]:
        path = pattern.format(windir=windir)
        if os.path.isfile(path):
            return path
    return None


def _page_lines(page) -> List[Tuple[str, float]]:
    """Lines of a page with the biggest font size used in each (pypdf visitor)."""
    rows: List[Tuple[float, str, float]] = []     # (y, text, size)

    def visit(text, cm, tm, font, size):  # noqa: ANN001 - pypdf callback
        if not text:
            return
        try:
            scale = abs(float(tm[3]) * float(cm[3])) or abs(float(tm[0]) * float(cm[0])) or 1.0
            y = float(tm[5]) * float(cm[3]) + float(cm[5])
        except (TypeError, ValueError, IndexError):
            scale, y = 1.0, 0.0
        rows.append((y, text, float(size or 0) * scale))

    try:
        page.extract_text(visitor_text=visit)
    except Exception:  # noqa: BLE001 - a damaged page: plain extraction below
        rows.clear()
    if not rows:
        try:
            return [(line, 0.0) for line in (page.extract_text() or "").splitlines()]
        except Exception:  # noqa: BLE001
            return []
    lines: List[Tuple[str, float]] = []
    text, size = "", 0.0
    for _y, piece, piece_size in rows:
        parts = piece.split("\n")
        for i, part in enumerate(parts):
            if i:
                lines.append((text, size))
                text, size = "", 0.0
            text += part
            if part.strip():
                size = max(size, piece_size)
    lines.append((text, size))
    return [(t.rstrip(), s) for t, s in lines]


def extract(path: str, fmt: str = "text", max_chars: int = 2_000_000) -> str:
    """Text (``fmt="text"``) or Markdown (``"markdown"``) of the PDF at *path*."""
    from pypdf import PdfReader      # lazy: only this feature needs it

    reader = PdfReader(path)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            raise ValueError("encrypted PDF") from None
    pages: List[List[Tuple[str, float]]] = []
    total = 0
    for page in reader.pages:
        lines = _page_lines(page)
        pages.append(lines)
        total += sum(len(t) for t, _ in lines)
        if total > max_chars:
            break
    if fmt != "markdown":
        out = "\n\n".join(f"--- {n} ---\n" + "\n".join(t for t, _ in lines).strip() for n, lines in enumerate(pages, 1))
        return out[:max_chars]
    sizes = [s for lines in pages for t, s in lines if t.strip() and s > 0]
    body = statistics.median(sizes) if sizes else 0.0
    blocks: List[str] = []
    for n, lines in enumerate(pages):
        if n:
            blocks.append("---")
        para: List[str] = []

        def flush() -> None:
            if para:
                blocks.append(" ".join(para))
                para.clear()
        for text, size in lines:
            line = text.strip()
            if not line:
                flush()
                continue
            if body and size >= body * 1.6 and len(line) < 120:
                flush(); blocks.append("# " + line)
            elif body and size >= body * 1.25 and len(line) < 160:
                flush(); blocks.append("## " + line)
            elif re.match(r"^[•●▪◦‣∙·\-–*]\s+", line):
                flush(); blocks.append("- " + re.sub(r"^[•●▪◦‣∙·\-–*]\s+", "", line))
            elif re.match(r"^\d{1,3}[.)]\s+", line):
                flush(); blocks.append(line)
            else:
                if para and para[-1].endswith("-") and not para[-1].endswith(" -"):
                    para[-1] = para[-1][:-1] + line          # a word hyphenated across lines
                else:
                    para.append(line)
        flush()
    return "\n\n".join(blocks).strip()[:max_chars] + "\n"
