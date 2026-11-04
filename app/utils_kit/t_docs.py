# -*- coding: utf-8 -*-
"""PDF and document utilities: merge, split, pages, rotate, text, images, password, watermark, page numbers,
metadata, compress, crop; document conversion (DOCX/ODT/RTF/HTML/Markdown/TXT/EPUB/FB2 → DOCX/ODT/RTF/HTML/MD/TXT/PDF),
plain text from any document, text encodings, Markdown ↔ HTML and e-mail (.eml) reading.

PDF work is pypdf only; a PDF out of HTML goes through :meth:`Ctx.pdf_job` (headless Edge on Windows, a raster
fallback elsewhere), which the async runner finishes after the utility returns.
"""
from __future__ import annotations

import html as _html
import io
import re
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .core import O, UtilError, as_float, as_int, human_size, page_ranges, table, utility

PDF = ".pdf"
DOC_ACCEPT = ".docx,.odt,.rtf,.html,.htm,.md,.markdown,.txt,.epub,.fb2,.pdf"

def _pypdf():
    try:
        import pypdf
    except ImportError:
        raise UtilError("Библиотека pypdf недоступна", "pypdf is not available")
    return pypdf

def open_pdf(f, password: str = ""):
    pypdf = _pypdf()
    try:
        reader = pypdf.PdfReader(io.BytesIO(f.data))
    except Exception as exc:  # noqa: BLE001
        raise UtilError(f"«{f.name}» не открывается как PDF: {exc}", f"“{f.name}” cannot be opened as a PDF: {exc}")
    if reader.is_encrypted:
        ok = 0
        for attempt in ([password] if password else []) + [""]:
            try:
                ok = reader.decrypt(attempt)
            except Exception:  # noqa: BLE001 - an AES file without the crypto library, a broken handler
                ok = 0
            if ok:
                break
        if not ok:
            raise UtilError(f"«{f.name}» защищён паролем — снимите защиту утилитой «Снять пароль с PDF»",
                            f"“{f.name}” is password-protected — remove it with “Unlock PDF” first")
    return reader

def _writer_bytes(writer) -> bytes:
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()

def _emit_many(ctx, files: List[Tuple[str, bytes]], zip_name: str, zip_over: int = 12) -> None:
    if len(files) > zip_over or ctx.o.get("zip"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
            seen = set()
            for name, data in files:
                base, n = name, 2
                while name in seen:
                    stem, dot, ext = base.rpartition(".")
                    name = f"{stem} ({n}).{ext}" if dot else f"{base} ({n})"
                    n += 1
                seen.add(name)
                archive.writestr(name, data)
        ctx.out_bytes(zip_name, buf.getvalue())
    else:
        for name, data in files:
            ctx.out_bytes(name, data)

def _dedupe(writer) -> None:
    try:
        writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    except TypeError:
        try:
            writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass

def _pages_note(ctx, n: int) -> str:
    return ctx.tr(f"Страниц: {n}", f"Pages: {n}")

# ── merge / split / pages ────────────────────────────────────────────────────────────────────────────────────────────
@utility("pdf.merge", "pdf", "Объединить PDF", "Merge PDFs",
         "Склеивает несколько PDF (и картинок) в один файл в выбранном порядке, с закладкой на каждый файл.",
         "Joins several PDFs (and images) into one file in the given order, with a bookmark per file.",
         input="files", accept=".pdf,image/*", ai_default=True,
         opts=[O("bookmarks", "bool", "Закладка на каждый файл", "A bookmark per file", True),
               O("name", "text", "Имя результата", "Result name", "merged.pdf")],
         keys="склеить объединить соединить pdf merge join combine")
def pdf_merge(ctx):
    pypdf = _pypdf()
    if len(ctx.files) < 2:
        raise UtilError("Добавьте хотя бы два файла", "Add at least two files")
    writer = pypdf.PdfWriter()
    total = 0
    for f in ctx.files:
        if f.ext == "pdf" or f.data[:5] == b"%PDF-":
            reader = open_pdf(f)
            start = len(writer.pages)
            for page in reader.pages:
                writer.add_page(page)
        else:
            from .t_image import open_image
            img = open_image(f).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "PDF", resolution=150)
            start = len(writer.pages)
            writer.add_page(pypdf.PdfReader(io.BytesIO(buf.getvalue())).pages[0])
        if ctx.o["bookmarks"]:
            writer.add_outline_item(f.stem, start)
        total = len(writer.pages)
    name = ctx.o["name"] or "merged.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    ctx.out_bytes(name, _writer_bytes(writer))
    return ctx.tr(f"Готово: {len(ctx.files)} файлов → {total} стр.", f"Done: {len(ctx.files)} files → {total} pages")

@utility("pdf.split", "pdf", "Разделить PDF", "Split a PDF",
         "Режет PDF на части: каждую страницу отдельно, по N страниц или по своим диапазонам (1-3,4-9,10-).",
         "Cuts a PDF into parts: every page, every N pages or by your own ranges (1-3,4-9,10-).",
         input="file", accept=PDF, ai_default=True,
         opts=[O("mode", "choice", "Как делить", "How", "ranges", choices=[("ranges", "По диапазонам", "By ranges"), ("every", "Каждые N страниц", "Every N pages"), ("each", "Каждая страница", "Each page")]),
               O("ranges", "text", "Диапазоны (через запятую)", "Ranges (comma-separated)", "1-2,3-", show="mode=ranges"),
               O("n", "int", "Страниц в части", "Pages per part", 2, min=1, max=10000, show="mode=every"),
               O("zip", "bool", "Упаковать в ZIP", "Pack into a ZIP", False)],
         keys="разрезать разбить разделить split pdf страницы")
def pdf_split(ctx):
    pypdf = _pypdf()
    f = ctx.file()
    reader = open_pdf(f)
    total = len(reader.pages)
    groups: List[List[int]] = []
    if ctx.o["mode"] == "each":
        groups = [[i] for i in range(total)]
    elif ctx.o["mode"] == "every":
        n = max(1, ctx.o["n"])
        groups = [list(range(i, min(total, i + n))) for i in range(0, total, n)]
    else:
        for part in str(ctx.o["ranges"] or "").split(","):
            if part.strip():
                groups.append(page_ranges(part, total))
        if not groups:
            raise UtilError("Укажите диапазоны, например 1-3,4-", "Enter ranges, e.g. 1-3,4-")
    files = []
    for idx, group in enumerate(groups, 1):
        writer = pypdf.PdfWriter()
        for i in group:
            writer.add_page(reader.pages[i])
        label = f"{group[0] + 1}" if len(group) == 1 else f"{group[0] + 1}-{group[-1] + 1}"
        files.append((f"{f.stem} — {label}.pdf", _writer_bytes(writer)))
    _emit_many(ctx, files, f"{f.stem} — parts.zip")
    return ctx.tr(f"{total} стр. → {len(files)} частей", f"{total} pages → {len(files)} parts")

@utility("pdf.pages", "pdf", "Страницы PDF: выбрать, удалить, порядок", "PDF pages: pick, delete, reorder",
         "Оставляет нужные страницы, удаляет лишние, переворачивает порядок или собирает свой (3,1,2,5-).",
         "Keeps the pages you need, deletes the rest, reverses the order or builds your own (3,1,2,5-).",
         input="file", accept=PDF, ai_default=True,
         opts=[O("action", "choice", "Действие", "Action", "keep", choices=[("keep", "Оставить страницы", "Keep pages"), ("delete", "Удалить страницы", "Delete pages"),
                                                                            ("order", "Новый порядок", "New order"), ("reverse", "Обратный порядок", "Reverse order"),
                                                                            ("odd", "Только нечётные", "Odd pages only"), ("even", "Только чётные", "Even pages only")]),
               O("pages", "text", "Страницы", "Pages", "1-3", show="action=keep|delete|order", hint="Например 1-3,5,8-", hint_en="E.g. 1-3,5,8-")],
         keys="удалить страницы извлечь порядок перевернуть pdf pages extract delete reorder")
def pdf_pages(ctx):
    pypdf = _pypdf()
    f = ctx.file()
    reader = open_pdf(f)
    total = len(reader.pages)
    action = ctx.o["action"]
    if action == "keep":
        order = page_ranges(ctx.o["pages"], total)
    elif action == "delete":
        drop = set(page_ranges(ctx.o["pages"], total))
        order = [i for i in range(total) if i not in drop]
    elif action == "order":
        order = []
        for part in str(ctx.o["pages"] or "").split(","):
            if part.strip():
                order += [i for i in page_ranges(part, total)]
    elif action == "reverse":
        order = list(range(total - 1, -1, -1))
    elif action == "odd":
        order = list(range(0, total, 2))
    else:
        order = list(range(1, total, 2))
    if not order:
        raise UtilError("Не осталось ни одной страницы", "No pages are left")
    writer = pypdf.PdfWriter()
    for i in order:
        writer.add_page(reader.pages[i])
    ctx.out_bytes(f"{f.stem} — {len(order)} pages.pdf", _writer_bytes(writer))
    return ctx.tr(f"Было {total} стр., стало {len(order)}", f"{total} pages before, {len(order)} now")

@utility("pdf.rotate", "pdf", "Повернуть страницы PDF", "Rotate PDF pages",
         "Поворачивает все или выбранные страницы на 90°, 180° или 270°.", "Rotates all or selected pages by 90°, 180° or 270°.",
         input="file", accept=PDF, ai_default=True,
         opts=[O("angle", "choice", "Угол", "Angle", "90", choices=[("90", "90° по часовой", "90° clockwise"), ("180", "180°", "180°"), ("270", "90° против часовой", "90° counter-clockwise")]),
               O("pages", "text", "Страницы (пусто — все)", "Pages (empty = all)", "")],
         keys="повернуть развернуть rotate pdf")
def pdf_rotate(ctx):
    pypdf = _pypdf()
    f = ctx.file()
    reader = open_pdf(f)
    which = set(page_ranges(ctx.o["pages"], len(reader.pages)))
    writer = pypdf.PdfWriter()
    for i, page in enumerate(reader.pages):
        added = writer.add_page(page)
        if i in which:
            added.rotate(int(ctx.o["angle"]))
    ctx.out_bytes(f"{f.stem} — rotated.pdf", _writer_bytes(writer))
    return ctx.tr(f"Повёрнуто страниц: {len(which)}", f"Pages rotated: {len(which)}")

@utility("pdf.text", "pdf", "Текст из PDF", "Text from a PDF",
         "Достаёт текст из PDF простым текстом или Markdown с заголовками и списками. Сканы без текстового слоя — через «Распознать текст (ИИ)».",
         "Pulls the text out of a PDF as plain text or Markdown with headings and lists. Scans without a text layer: use “Recognize text (AI)”.",
         input="file", accept=PDF, ai_default=True,
         opts=[O("fmt", "choice", "Формат", "Format", "text", choices=[("text", "Текст", "Text"), ("markdown", "Markdown", "Markdown")]),
               O("pages", "text", "Страницы (пусто — все)", "Pages (empty = all)", ""),
               O("file", "bool", "Сохранить файлом", "Save as a file", False)],
         keys="извлечь текст pdf to text markdown распознать")
def pdf_text(ctx):
    from ..memunote.formats import pdftools
    pypdf = _pypdf()
    f = ctx.file()
    reader = open_pdf(f)
    source = f
    if ctx.o["pages"] or reader.is_encrypted:
        writer = pypdf.PdfWriter()
        for i in page_ranges(ctx.o["pages"], len(reader.pages)):
            writer.add_page(reader.pages[i])
        from .core import InFile
        source = InFile(f.name, _writer_bytes(writer), ctx.work)
    try:
        text = pdftools.extract(str(source.path()), ctx.o["fmt"])
    except Exception as exc:  # noqa: BLE001
        raise UtilError(f"Не удалось прочитать текст: {exc}", f"Could not read the text: {exc}")
    if not re.sub(r"---\s*\d+\s*---|\s", "", text):
        raise UtilError("В PDF нет текстового слоя (скан?) — попробуйте «Распознать текст (ИИ)» в окне MemuUtils",
                        "The PDF has no text layer (a scan?) — try “Recognize text (AI)” in the MemuUtils window")
    if ctx.o["file"] or len(text) > 200_000:
        ctx.out_text(f"{f.stem}.{'md' if ctx.o['fmt'] == 'markdown' else 'txt'}", text)
    return {"text": text[:200_000], "lang": "markdown" if ctx.o["fmt"] == "markdown" else "",
            "note": ctx.tr("Показаны первые 200 000 символов, целиком — в файле", "The first 200,000 characters are shown, the whole text is in the file") if len(text) > 200_000 else ""}

@utility("pdf.images", "pdf", "Картинки из PDF", "Images from a PDF",
         "Сохраняет все встроенные в PDF изображения в исходном качестве (несколько файлов или ZIP).",
         "Saves every image embedded in a PDF at its original quality (separate files or a ZIP).",
         input="file", accept=PDF, ai_default=True,
         opts=[O("pages", "text", "Страницы (пусто — все)", "Pages (empty = all)", ""), O("min", "int", "Не меньше, px", "At least, px", 32, min=1, max=10000),
               O("zip", "bool", "Упаковать в ZIP", "Pack into a ZIP", False)],
         keys="извлечь картинки изображения фото из pdf extract images")
def pdf_images(ctx):
    f = ctx.file()
    reader = open_pdf(f)
    files = []
    for i in page_ranges(ctx.o["pages"], len(reader.pages)):
        try:
            images = list(reader.pages[i].images)
        except Exception:  # noqa: BLE001 - an unreadable image dictionary on one page
            continue
        for k, img in enumerate(images, 1):
            try:
                pil = img.image
                if pil is not None and min(pil.size) < ctx.o["min"]:
                    continue
                name = img.name or f"image{k}.png"
                ext = Path(name).suffix.lower() or ".png"
                data = img.data
                if ext not in (".jpg", ".jpeg", ".png", ".jp2", ".tif", ".tiff", ".webp", ".gif", ".bmp") and pil is not None:
                    buf = io.BytesIO()
                    pil.save(buf, "PNG")
                    data, ext = buf.getvalue(), ".png"
                files.append((f"{f.stem} — p{i + 1}-{k}{ext}", data))
            except Exception:  # noqa: BLE001
                continue
    if not files:
        raise UtilError("Встроенных изображений не нашлось", "No embedded images were found")
    _emit_many(ctx, files, f"{f.stem} — images.zip")
    return ctx.tr(f"Найдено изображений: {len(files)}", f"Images found: {len(files)}")

# ── password ─────────────────────────────────────────────────────────────────────────────────────────────────────────
@utility("pdf.protect", "pdf", "Поставить пароль на PDF", "Password-protect a PDF",
         "Шифрует PDF паролем на открытие (AES-256). Можно запретить печать и копирование текста.",
         "Encrypts a PDF with an open password (AES-256). Printing and copying can be forbidden too.",
         input="file", accept=PDF,
         opts=[O("password", "password", "Пароль", "Password", "", ai=False), O("owner", "password", "Пароль владельца (необязательно)", "Owner password (optional)", "", ai=False),
               O("print", "bool", "Разрешить печать", "Allow printing", True), O("copy", "bool", "Разрешить копирование", "Allow copying", True)],
         keys="защитить пароль зашифровать encrypt protect password pdf")
def pdf_protect(ctx):
    pypdf = _pypdf()
    f = ctx.file()
    if not ctx.o["password"]:
        raise UtilError("Введите пароль", "Enter a password")
    reader = open_pdf(f)
    writer = pypdf.PdfWriter(clone_from=reader)
    from pypdf.constants import UserAccessPermissions as P
    perms = P(4294967292)  # pypdf's default: everything a reader may do
    if not ctx.o["print"]:
        perms &= ~(P.PRINT | P.PRINT_TO_REPRESENTATION)
    if not ctx.o["copy"]:
        perms &= ~(P.EXTRACT | P.EXTRACT_TEXT_AND_GRAPHICS)
    note = ""
    try:
        writer.encrypt(user_password=ctx.o["password"], owner_password=ctx.o["owner"] or None, permissions_flag=perms, algorithm="AES-256")
    except Exception:  # noqa: BLE001 - no AES provider (cryptography) in this build
        writer = pypdf.PdfWriter(clone_from=reader)
        writer.encrypt(user_password=ctx.o["password"], owner_password=ctx.o["owner"] or None, permissions_flag=perms, algorithm="RC4-128")
        note = ctx.tr("AES недоступен в этой сборке — использовано RC4-128", "AES is not available in this build — RC4-128 was used")
    ctx.out_bytes(f"{f.stem} — protected.pdf", _writer_bytes(writer))
    return {"text": ctx.tr("PDF зашифрован. Без пароля его не открыть — сохраните пароль в надёжном месте.",
                           "The PDF is encrypted. It cannot be opened without the password — keep it somewhere safe."), "note": note}

@utility("pdf.unlock", "pdf", "Снять пароль с PDF", "Unlock a PDF",
         "Сохраняет копию PDF без пароля и ограничений (нужно знать пароль, если он стоит на открытие).",
         "Saves a copy of the PDF without a password and restrictions (you need the password if it locks opening).",
         input="file", accept=PDF,
         opts=[O("password", "password", "Пароль", "Password", "", ai=False)],
         keys="снять пароль расшифровать разблокировать decrypt unlock remove password pdf")
def pdf_unlock(ctx):
    pypdf = _pypdf()
    f = ctx.file()
    try:
        reader = pypdf.PdfReader(io.BytesIO(f.data))
    except Exception as exc:  # noqa: BLE001
        raise UtilError(f"«{f.name}» не открывается как PDF: {exc}", f"“{f.name}” cannot be opened as a PDF: {exc}")
    if not reader.is_encrypted:
        raise UtilError("На этом PDF нет пароля", "This PDF has no password")
    ok = 0
    for attempt in (ctx.o["password"], ""):
        try:
            ok = reader.decrypt(attempt or "")
        except Exception as exc:  # noqa: BLE001
            if "cryptography" in str(exc).lower() or "aes" in str(exc).lower():
                raise UtilError("Для AES-шифрования нужна библиотека cryptography", "AES encryption needs the cryptography library")
            ok = 0
        if ok:
            break
    if not ok:
        raise UtilError("Неверный пароль", "Wrong password")
    writer = pypdf.PdfWriter(clone_from=reader)
    ctx.out_bytes(f"{f.stem} — unlocked.pdf", _writer_bytes(writer))
    return ctx.tr("Готово: копия без пароля", "Done: a copy without the password")

# ── info / metadata ──────────────────────────────────────────────────────────────────────────────────────────────────
def _mm(points: float) -> int:
    return round(float(points) * 25.4 / 72)

PAPER = {(210, 297): "A4", (148, 210): "A5", (297, 420): "A3", (216, 279): "Letter", (216, 356): "Legal", (105, 148): "A6", (250, 353): "B4", (176, 250): "B5"}

def _paper(w: float, h: float) -> str:
    a, b = sorted((_mm(w), _mm(h)))
    for (pw, ph), name in PAPER.items():
        if abs(a - pw) <= 2 and abs(b - ph) <= 2:
            return name
    return ""

@utility("pdf.info", "pdf", "Сведения о PDF", "PDF info",
         "Страницы, размеры листов, версия, шифрование, автор, программа, даты, шрифты, вложения и закладки.",
         "Pages, paper sizes, version, encryption, author, producer, dates, fonts, attachments and bookmarks.",
         input="file", accept=PDF, ai_default=True, keys="информация свойства метаданные pdf info properties")
def pdf_info(ctx):
    pypdf = _pypdf()
    f = ctx.file()
    reader = pypdf.PdfReader(io.BytesIO(f.data))
    rows: List[List[str]] = [[ctx.tr("Файл", "File"), f"{f.name} · {human_size(f.size, ctx.en)}"]]
    encrypted = reader.is_encrypted
    if encrypted:
        try:
            if not reader.decrypt(""):
                return {"info": rows + [[ctx.tr("Шифрование", "Encryption"), ctx.tr("да, нужен пароль на открытие", "yes, an open password is required")]]}
        except Exception:  # noqa: BLE001
            return {"info": rows + [[ctx.tr("Шифрование", "Encryption"), ctx.tr("да", "yes")]]}
    rows.append([ctx.tr("Версия PDF", "PDF version"), str(getattr(reader, "pdf_header", "") or "").replace("%PDF-", "")])
    rows.append([ctx.tr("Страниц", "Pages"), str(len(reader.pages))])
    sizes: Dict[str, int] = {}
    fonts = set()
    images = 0
    for page in reader.pages:
        box = page.mediabox
        w, h = float(box.width), float(box.height)
        if (page.get("/Rotate") or 0) in (90, 270):
            w, h = h, w
        label = f"{_mm(w)}×{_mm(h)} " + ctx.tr("мм", "mm") + (f" ({_paper(w, h)})" if _paper(w, h) else "")
        sizes[label] = sizes.get(label, 0) + 1
        try:
            res = page.get("/Resources") or {}
            res = res.get_object() if hasattr(res, "get_object") else res
            for font in ((res.get("/Font") or {}).get_object() or {}).values():
                base = str(font.get_object().get("/BaseFont") or "")
                if base:
                    fonts.add(base.lstrip("/").split("+")[-1])
            xobjects = (res.get("/XObject") or {})
            xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
            images += sum(1 for x in xobjects.values() if x.get_object().get("/Subtype") == "/Image")
        except Exception:  # noqa: BLE001
            pass
    rows.append([ctx.tr("Размер листов", "Page size"), "; ".join(f"{k} × {v}" if len(sizes) > 1 else k for k, v in sizes.items())])
    meta = reader.metadata or {}
    names = {"/Title": ("Название", "Title"), "/Author": ("Автор", "Author"), "/Subject": ("Тема", "Subject"), "/Keywords": ("Ключевые слова", "Keywords"),
             "/Creator": ("Создано в", "Creator"), "/Producer": ("PDF-движок", "Producer"), "/CreationDate": ("Создан", "Created"), "/ModDate": ("Изменён", "Modified")}
    for key, (ru, en) in names.items():
        value = meta.get(key) if hasattr(meta, "get") else None
        if value:
            value = str(value)
            m = re.match(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?", value)
            if m:
                parts = [p or "01" for p in m.groups()]
                value = f"{parts[0]}-{parts[1]}-{parts[2]} {m.group(4) or '00'}:{m.group(5) or '00'}"
            rows.append([ctx.tr(ru, en), value])
    rows.append([ctx.tr("Шифрование", "Encryption"), ctx.tr("да (без пароля на открытие)", "yes (no open password)") if encrypted else ctx.tr("нет", "no")])
    try:
        outline = reader.outline
        count = 0
        stack = list(outline)
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack += item
            else:
                count += 1
        rows.append([ctx.tr("Закладок", "Bookmarks"), str(count)])
    except Exception:  # noqa: BLE001
        pass
    try:
        attachments = reader.attachments
        if attachments:
            rows.append([ctx.tr("Вложения", "Attachments"), ", ".join(list(attachments)[:20])])
    except Exception:  # noqa: BLE001
        pass
    try:
        rows.append([ctx.tr("Формы", "Forms"), ctx.tr(f"да, полей: {len(reader.get_fields() or {})}", f"yes, fields: {len(reader.get_fields() or {})}") if reader.get_fields() else ctx.tr("нет", "no")])
    except Exception:  # noqa: BLE001
        pass
    rows.append([ctx.tr("Картинок", "Images"), str(images)])
    if fonts:
        rows.append([ctx.tr("Шрифты", "Fonts"), ", ".join(sorted(fonts)[:30])])
    return {"info": rows}

@utility("pdf.meta", "pdf", "Метаданные PDF", "PDF metadata",
         "Меняет название, автора, тему и ключевые слова PDF или стирает все метаданные перед отправкой.",
         "Changes a PDF's title, author, subject and keywords or wipes all metadata before sending.",
         input="file", accept=PDF,
         opts=[O("mode", "choice", "Действие", "Action", "set", choices=[("set", "Изменить", "Change"), ("clear", "Стереть всё", "Wipe all")]),
               O("title", "text", "Название", "Title", "", show="mode=set"), O("author", "text", "Автор", "Author", "", show="mode=set"),
               O("subject", "text", "Тема", "Subject", "", show="mode=set"), O("keywords", "text", "Ключевые слова", "Keywords", "", show="mode=set")],
         keys="метаданные автор название свойства очистить pdf metadata author title")
def pdf_meta(ctx):
    pypdf = _pypdf()
    f = ctx.file()
    reader = open_pdf(f)
    writer = pypdf.PdfWriter(clone_from=reader)
    if ctx.o["mode"] == "clear":
        try:
            writer.metadata = None
        except Exception:  # noqa: BLE001 - older pypdf: overwrite with empty values
            writer.add_metadata({"/Producer": ""})
        try:
            if "/Metadata" in writer.root_object:
                del writer.root_object["/Metadata"]
        except Exception:  # noqa: BLE001
            pass
        ctx.out_bytes(f"{f.stem} — clean.pdf", _writer_bytes(writer))
        return ctx.tr("Метаданные стёрты", "Metadata wiped")
    values = {"/Title": ctx.o["title"], "/Author": ctx.o["author"], "/Subject": ctx.o["subject"], "/Keywords": ctx.o["keywords"]}
    values = {k: v for k, v in values.items() if v}
    if not values:
        raise UtilError("Заполните хотя бы одно поле", "Fill in at least one field")
    writer.add_metadata(values)
    ctx.out_bytes(f.name, _writer_bytes(writer))
    return ctx.tr("Метаданные обновлены", "Metadata updated")

# ── overlays: watermark and page numbers ────────────────────────────────────────────────────────────────────────────
def _overlay_pdf(box: Tuple[float, float, float, float], rotate: int, items: Sequence[Tuple[Any, float, float, float, float]]) -> bytes:
    """A one-page PDF with transparent RGBA images placed in *visual* page coordinates (rotation-aware)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    rot = {90: f"0 1 -1 0 {w:.3f} 0", 180: f"-1 0 0 -1 {w:.3f} {h:.3f}", 270: f"0 -1 1 0 0 {h:.3f}"}.get(rotate % 360, "1 0 0 1 0 0")
    objs: List[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", b""]
    names, draw = [], [f"q 1 0 0 1 {x0:.3f} {y0:.3f} cm {rot} cm"]
    for n, (img, vx, vy, vw, vh) in enumerate(items):
        rgba = img.convert("RGBA")
        rgb = zlib.compress(rgba.convert("RGB").tobytes(), 9)
        alpha = zlib.compress(rgba.getchannel("A").tobytes(), 9)
        mask_id = len(objs) + 2
        objs.append(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /SMask %d 0 R /Length %d >>\nstream\n"
                    % (rgba.width, rgba.height, mask_id, len(rgb)) + rgb + b"\nendstream")
        objs.append(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n"
                    % (rgba.width, rgba.height, len(alpha)) + alpha + b"\nendstream")
        names.append(f"/Im{n} {len(objs) - 1} 0 R")
        draw.append(f"q {vw:.3f} 0 0 {vh:.3f} {vx:.3f} {vy:.3f} cm /Im{n} Do Q")
    draw.append("Q")
    content = " ".join(draw).encode()
    objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objs[2] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [{x0:.3f} {y0:.3f} {x1:.3f} {y1:.3f}] /Resources << /XObject << {' '.join(names)} >> >> /Contents {len(objs)} 0 R /Rotate {rotate % 360} >>").encode()
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()

def _page_geometry(page) -> Tuple[Tuple[float, float, float, float], int, float, float]:
    box = page.cropbox if page.get("/CropBox") is not None else page.mediabox
    x0, y0, x1, y1 = (float(v) for v in (box.left, box.bottom, box.right, box.top))
    rotate = int(page.get("/Rotate") or 0) % 360
    vw, vh = (y1 - y0, x1 - x0) if rotate in (90, 270) else (x1 - x0, y1 - y0)
    return (x0, y0, x1, y1), rotate, vw, vh

def _text_image(text: str, size_px: int, color, opacity: float, angle: float = 0.0, bold: bool = True):
    from PIL import Image, ImageDraw
    from .t_gen import font
    fnt = font(size_px, bold)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=fnt)
    pad = max(2, size_px // 8)
    img = Image.new("RGBA", (right - left + pad * 2, bottom - top + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - left, pad - top), text, font=fnt, fill=tuple(color[:3]) + (int(255 * max(0.02, min(1.0, opacity))),))
    if angle:
        img = img.rotate(angle, expand=True, resample=Image.BICUBIC)
    return img

def _merge_overlay(page, overlay_bytes: bytes) -> None:
    pypdf = _pypdf()
    over = pypdf.PdfReader(io.BytesIO(overlay_bytes)).pages[0]
    page.merge_page(over, over=True)

@utility("pdf.watermark", "pdf", "Водяной знак на PDF", "PDF watermark",
         "Накладывает полупрозрачную надпись («Черновик», «Конфиденциально», имя) на все или выбранные страницы — по центру, по диагонали или мозаикой.",
         "Stamps a semi-transparent label (“Draft”, “Confidential”, a name) over all or selected pages — centered, diagonal or tiled.",
         input="file", accept=PDF,
         opts=[O("text", "text", "Текст", "Text", "КОНФИДЕНЦИАЛЬНО"), O("layout", "choice", "Расположение", "Layout", "diagonal",
                                                                     choices=[("diagonal", "По диагонали", "Diagonal"), ("center", "По центру", "Centered"), ("tile", "Мозаикой", "Tiled"),
                                                                              ("top", "Сверху", "Top"), ("bottom", "Снизу", "Bottom")]),
               O("size", "int", "Размер, % ширины", "Size, % of width", 60, min=5, max=100), O("opacity", "int", "Непрозрачность, %", "Opacity, %", 18, min=3, max=100),
               O("color", "color", "Цвет", "Color", "#d32f2f"), O("pages", "text", "Страницы (пусто — все)", "Pages (empty = all)", "")],
         keys="водяной знак штамп надпись черновик watermark stamp confidential draft")
def pdf_watermark(ctx):
    from .core import parse_color
    pypdf = _pypdf()
    f = ctx.file()
    text = str(ctx.o["text"] or "").strip()
    if not text:
        raise UtilError("Введите текст водяного знака", "Enter the watermark text")
    reader = open_pdf(f)
    writer = pypdf.PdfWriter(clone_from=reader)
    which = set(page_ranges(ctx.o["pages"], len(writer.pages)))
    color = parse_color(ctx.o["color"], (211, 47, 47))
    opacity = ctx.o["opacity"] / 100
    cache: Dict[Tuple, bytes] = {}
    scale = 2.0
    for i, page in enumerate(writer.pages):
        if i not in which:
            continue
        box, rotate, vw, vh = _page_geometry(page)
        key = (box, rotate)
        if key not in cache:
            layout = ctx.o["layout"]
            angle = math_degrees(vw, vh) if layout in ("diagonal", "tile") else 0.0
            target_w = vw * ctx.o["size"] / 100 * scale
            probe = _text_image(text, 100, color, opacity)
            px = max(8, int(100 * target_w / max(1, probe.width - 25)))
            img = _text_image(text, min(px, 1200), color, opacity, angle if layout != "tile" else 30.0)
            items = []
            iw, ih = img.width / scale, img.height / scale
            if iw > vw or ih > vh:
                k = min(vw / iw, vh / ih) * 0.98
                iw, ih = iw * k, ih * k
            if layout == "tile":
                small = _text_image(text, max(8, px // 3), color, opacity, 30.0)
                sw, sh = small.width / scale, small.height / scale
                y = 0.0
                while y < vh:
                    x = -sw / 2 if int(y / max(1, sh)) % 2 else 0.0
                    while x < vw:
                        items.append((small, x, y, sw, sh))
                        x += sw * 1.3
                    y += sh * 1.6
            elif layout == "top":
                items.append((img, (vw - iw) / 2, vh - ih - vh * 0.03, iw, ih))
            elif layout == "bottom":
                items.append((img, (vw - iw) / 2, vh * 0.03, iw, ih))
            else:
                items.append((img, (vw - iw) / 2, (vh - ih) / 2, iw, ih))
            cache[key] = _overlay_pdf(box, rotate, items)
        _merge_overlay(page, cache[key])
    _dedupe(writer)
    ctx.out_bytes(f"{f.stem} — watermark.pdf", _writer_bytes(writer))
    return ctx.tr(f"Водяной знак поставлен на {len(which)} стр.", f"Watermark added to {len(which)} pages")

def math_degrees(w: float, h: float) -> float:
    import math
    return math.degrees(math.atan2(h, w))

@utility("pdf.numbers", "pdf", "Нумерация страниц PDF", "PDF page numbers",
         "Проставляет номера страниц: снизу или сверху, по центру или с краю, в формате «5», «5 / 12», «Стр. 5 из 12».",
         "Adds page numbers: bottom or top, centered or at the edge, as “5”, “5 / 12”, “Page 5 of 12”.",
         input="file", accept=PDF, ai_default=True,
         opts=[O("format", "text", "Формат ({n} — номер, {total} — всего)", "Format ({n} = number, {total} = total)", "{n} / {total}"),
               O("position", "choice", "Где", "Where", "bottom-center", choices=[("bottom-center", "Снизу по центру", "Bottom center"), ("bottom-right", "Снизу справа", "Bottom right"),
                                                                                ("bottom-left", "Снизу слева", "Bottom left"), ("top-center", "Сверху по центру", "Top center"),
                                                                                ("top-right", "Сверху справа", "Top right"), ("outer", "Снаружи (как в книге)", "Outer edge (book)")]),
               O("start", "int", "Начать с номера", "Start at", 1, min=0, max=100000), O("skip", "int", "Пропустить первых страниц", "Skip first pages", 0, min=0, max=10000),
               O("size", "int", "Размер шрифта, pt", "Font size, pt", 10, min=5, max=48), O("color", "color", "Цвет", "Color", "#333333")],
         keys="номера страниц нумерация пагинация page numbers pagination")
def pdf_numbers(ctx):
    from .core import parse_color
    pypdf = _pypdf()
    f = ctx.file()
    reader = open_pdf(f)
    writer = pypdf.PdfWriter(clone_from=reader)
    total_pages = len(writer.pages)
    skip = min(ctx.o["skip"], total_pages)
    numbered_total = total_pages - skip + ctx.o["start"] - 1
    color = parse_color(ctx.o["color"], (51, 51, 51))
    fmt = str(ctx.o["format"] or "{n}")
    scale = 4.0
    for i, page in enumerate(writer.pages):
        if i < skip:
            continue
        n = i - skip + ctx.o["start"]
        try:
            label = fmt.replace("{total}", str(numbered_total)).replace("{n}", str(n))
        except Exception:  # noqa: BLE001
            label = str(n)
        box, rotate, vw, vh = _page_geometry(page)
        img = _text_image(label, int(ctx.o["size"] * scale), color, 1.0, bold=False)
        iw, ih = img.width / scale, img.height / scale
        margin = max(18.0, min(vw, vh) * 0.035)
        pos = ctx.o["position"]
        if pos == "outer":
            pos = "bottom-right" if (n % 2) else "bottom-left"
        x = {"center": (vw - iw) / 2, "right": vw - margin - iw, "left": margin}[pos.split("-")[1]]
        y = margin - ih / 2 if pos.startswith("bottom") else vh - margin - ih / 2
        _merge_overlay(page, _overlay_pdf(box, rotate, [(img, x, y, iw, ih)]))
    ctx.out_bytes(f"{f.stem} — numbered.pdf", _writer_bytes(writer))
    return ctx.tr(f"Пронумеровано страниц: {total_pages - skip}", f"Pages numbered: {total_pages - skip}")

# ── compress / crop ──────────────────────────────────────────────────────────────────────────────────────────────────
def _flate_raw_streams(writer) -> int:
    """Packs streams stored without any filter (some generators write raw pixels): lossless, keeps everything."""
    from pypdf.generic import StreamObject
    packed = 0
    objs = getattr(writer, "_objects", [])
    for i, obj in enumerate(objs):
        try:
            if not isinstance(obj, StreamObject) or "/Filter" in obj:
                continue
            raw = obj.get_data()
            if len(raw) < 512:
                continue
            enc = obj.flate_encode(level=9)
            if len(enc._data) < len(raw) * 0.95:
                objs[i] = enc
                packed += 1
        except Exception:  # noqa: BLE001 - leave an odd stream as it is
            continue
    return packed

@utility("pdf.compress", "pdf", "Сжать PDF", "Compress a PDF",
         "Уменьшает PDF: пережимает картинки (качество и размер), сжимает потоки, убирает дубликаты и метаданные.",
         "Shrinks a PDF: recompresses images (quality and size), compresses streams, removes duplicates and metadata.",
         input="file", accept=PDF, ai_default=True,
         opts=[O("level", "choice", "Степень", "Level", "medium", choices=[("light", "Лёгкая (почти без потерь)", "Light (nearly lossless)"), ("medium", "Средняя", "Medium"), ("strong", "Сильная", "Strong")]),
               O("strip", "bool", "Убрать метаданные", "Remove metadata", False)],
         keys="сжать уменьшить размер pdf compress shrink reduce size")
def pdf_compress(ctx):
    pypdf = _pypdf()
    f = ctx.file()
    reader = open_pdf(f)
    writer = pypdf.PdfWriter(clone_from=reader)
    quality, max_side = {"light": (88, 4000), "medium": (72, 2200), "strong": (55, 1400)}[ctx.o["level"]]
    touched = 0
    if ctx.o["level"] != "light":
        for page in writer.pages:
            try:
                images = list(page.images)
            except Exception:  # noqa: BLE001
                continue
            for img in images:
                try:
                    pil = img.image
                    if pil is None or pil.mode in ("1",) or min(pil.size) < 64:
                        continue
                    if pil.mode in ("RGBA", "LA", "PA") or (pil.mode == "P" and "transparency" in pil.info):
                        continue
                    if max(pil.size) > max_side:
                        pil = pil.copy()
                        pil.thumbnail((max_side, max_side))
                    if pil.mode not in ("RGB", "L", "CMYK"):
                        pil = pil.convert("RGB")
                    before = len(img.data)
                    img.replace(pil, quality=quality)
                    touched += 1 if before else 0
                except Exception:  # noqa: BLE001 - leave an image pypdf cannot re-encode as it is
                    continue
    for page in writer.pages:
        try:
            page.compress_content_streams(level=9)
        except Exception:  # noqa: BLE001
            pass
    flated = _flate_raw_streams(writer)
    _dedupe(writer)
    if ctx.o["strip"]:
        try:
            writer.metadata = None
        except Exception:  # noqa: BLE001
            pass
    data = _writer_bytes(writer)
    if len(data) >= f.size:
        ctx.out_bytes(f"{f.stem} — compressed.pdf", f.data)
        return {"text": ctx.tr("Файл уже сжат: меньше не получилось, сохранена исходная копия", "The file is already compact: it could not get smaller, the original was kept"),
                "info": [[ctx.tr("Размер", "Size"), human_size(f.size, ctx.en)]]}
    ctx.out_bytes(f"{f.stem} — compressed.pdf", data)
    saved = 100 - len(data) * 100 // max(1, f.size)
    return {"info": [[ctx.tr("Было", "Before"), human_size(f.size, ctx.en)], [ctx.tr("Стало", "After"), human_size(len(data), ctx.en)],
                     [ctx.tr("Экономия", "Saved"), f"{saved}%"], [ctx.tr("Пережато картинок", "Images recompressed"), str(touched)],
                     [ctx.tr("Сжато несжатых потоков", "Uncompressed streams packed"), str(flated)]]}

@utility("pdf.crop", "pdf", "Обрезать поля PDF", "Crop PDF margins",
         "Срезает поля страниц на заданное число миллиметров (например, белые края сканов или поля для чтения с телефона).",
         "Trims page margins by the given millimetres (white scan edges, margins for phone reading).",
         input="file", accept=PDF,
         opts=[O("top", "number", "Сверху, мм", "Top, mm", 10, min=0, max=200), O("bottom", "number", "Снизу, мм", "Bottom, mm", 10, min=0, max=200),
               O("left", "number", "Слева, мм", "Left, mm", 10, min=0, max=200), O("right", "number", "Справа, мм", "Right, mm", 10, min=0, max=200),
               O("pages", "text", "Страницы (пусто — все)", "Pages (empty = all)", "")],
         keys="обрезать поля кроп crop margins trim pdf")
def pdf_crop(ctx):
    pypdf = _pypdf()
    f = ctx.file()
    reader = open_pdf(f)
    writer = pypdf.PdfWriter(clone_from=reader)
    which = set(page_ranges(ctx.o["pages"], len(writer.pages)))
    k = 72 / 25.4
    t, b, l, r = (ctx.o[x] * k for x in ("top", "bottom", "left", "right"))
    for i, page in enumerate(writer.pages):
        if i not in which:
            continue
        box = page.mediabox
        x0, y0, x1, y1 = float(box.left), float(box.bottom), float(box.right), float(box.top)
        rot = int(page.get("/Rotate") or 0) % 360
        # margins are given for the page as it is shown: map them to the unrotated box
        side = {0: (l, b, r, t), 90: (b, r, t, l), 180: (r, t, l, b), 270: (t, l, b, r)}[rot]
        nx0, ny0, nx1, ny1 = x0 + side[0], y0 + side[1], x1 - side[2], y1 - side[3]
        if nx1 - nx0 < 36 or ny1 - ny0 < 36:
            raise UtilError("Поля больше самой страницы", "The margins are larger than the page")
        page.cropbox = pypdf.generic.RectangleObject([nx0, ny0, nx1, ny1])
        page.mediabox = pypdf.generic.RectangleObject([nx0, ny0, nx1, ny1])
    ctx.out_bytes(f"{f.stem} — cropped.pdf", _writer_bytes(writer))
    return ctx.tr(f"Обрезано страниц: {len(which)}", f"Pages cropped: {len(which)}")

# ── Markdown → HTML (small CommonMark/GFM subset) ───────────────────────────────────────────────────────────────────
_INLINE_CODE = re.compile(r"(`+)(.+?)\1", re.S)

def _md_inline(text: str) -> str:
    codes: List[str] = []

    def keep(m):
        codes.append(f"<code>{_html.escape(m.group(2).strip())}</code>")
        return f"\x00{len(codes) - 1}\x00"
    text = _INLINE_CODE.sub(keep, text)
    text = _html.escape(text, quote=False)
    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;([^&]*)&quot;)?\)", lambda m: f'<img src="{_safe_url(m.group(2))}" alt="{m.group(1)}">', text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)", lambda m: f'<a href="{_safe_url(m.group(2))}">{m.group(1)}</a>', text)
    text = re.sub(r"&lt;(https?://[^\s&]+)&gt;", r'<a href="\1">\1</a>', text)
    text = re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"<strong>\2</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])", r"<em>\1</em>", text)
    text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<del>\1</del>", text)
    text = re.sub(r"==(?=\S)(.+?)(?<=\S)==", r"<mark>\1</mark>", text)
    text = re.sub(r" {2,}\n|\\\n", "<br>\n", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)

def _safe_url(url: str) -> str:
    url = _html.unescape(url)
    if re.match(r"^\s*(javascript|vbscript|data:text)", url, re.I):
        return "#"
    return _html.escape(url, quote=True)

def markdown_to_html(src: str) -> str:
    lines = str(src or "").replace("\r\n", "\n").replace("\t", "    ").split("\n")
    out: List[str] = []
    i = 0

    def is_table_sep(line: str) -> bool:
        return bool(re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", line))

    def cells(line: str) -> List[str]:
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        return [c.strip() for c in re.split(r"(?<!\\)\|", line)]

    def parse_list(start: int) -> int:
        nonlocal out
        stack: List[Tuple[int, str]] = []
        j = start
        while j < len(lines):
            m = re.match(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$", lines[j])
            if not m:
                if lines[j].strip() and stack and re.match(r"^\s{2,}\S", lines[j]):
                    out[-1] = out[-1][:-5] + " " + _md_inline(lines[j].strip()) + "</li>"
                    j += 1
                    continue
                break
            indent = len(m.group(1))
            tag = "ol" if m.group(2)[0].isdigit() else "ul"
            while stack and indent < stack[-1][0]:
                out.append(f"</{stack.pop()[1]}>")
            if not stack or indent > stack[-1][0]:
                attr = f' start="{int(m.group(2)[:-1])}"' if tag == "ol" and int(m.group(2)[:-1]) != 1 else ""
                if stack and out and out[-1].endswith("</li>"):
                    out[-1] = out[-1][:-5]
                    stack.append((indent, tag))
                    out.append(f"<{tag}{attr}>")
                else:
                    stack.append((indent, tag))
                    out.append(f"<{tag}{attr}>")
            item = m.group(3)
            task = re.match(r"^\[( |x|X)\]\s+(.*)$", item)
            if task:
                checked = " checked" if task.group(1).lower() == "x" else ""
                out.append(f'<li class="task"><input type="checkbox" disabled{checked}> {_md_inline(task.group(2))}</li>')
            else:
                out.append(f"<li>{_md_inline(item)}</li>")
            j += 1
        while stack:
            tag = stack.pop()[1]
            out.append(f"</{tag}>")
            if stack:
                out.append("</li>")
        return j

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        fence = re.match(r"^\s*(```+|~~~+)\s*([\w+#.-]*)", line)
        if fence:
            mark, lang = fence.group(1), fence.group(2)
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(mark[:3]):
                body.append(lines[i])
                i += 1
            i += 1
            cls = f' class="language-{_html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{_html.escape(chr(10).join(body))}</code></pre>")
            continue
        h = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", stripped)
        if h:
            n = len(h.group(1))
            out.append(f"<h{n}>{_md_inline(h.group(2))}</h{n}>")
            i += 1
            continue
        if re.match(r"^(\*\s*){3,}$|^(-\s*){3,}$|^(_\s*){3,}$", stripped):
            out.append("<hr>")
            i += 1
            continue
        if stripped.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(f"<blockquote>{markdown_to_html(chr(10).join(quote))}</blockquote>")
            continue
        if "|" in stripped and i + 1 < len(lines) and is_table_sep(lines[i + 1]):
            head = cells(stripped)
            aligns = []
            for c in cells(lines[i + 1]):
                aligns.append("center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "left" if c.startswith(":") else "")
            rows = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(cells(lines[i]))
                i += 1

            def td(tag, value, k):
                al = aligns[k] if k < len(aligns) and aligns[k] else ""
                return f'<{tag}{f" style={chr(34)}text-align:{al}{chr(34)}" if al else ""}>{_md_inline(value)}</{tag}>'
            body = "".join("<tr>" + "".join(td("td", r[k] if k < len(r) else "", k) for k in range(len(head))) + "</tr>" for r in rows)
            out.append("<table><thead><tr>" + "".join(td("th", c, k) for k, c in enumerate(head)) + f"</tr></thead><tbody>{body}</tbody></table>")
            continue
        if re.match(r"^\s*([-*+]|\d{1,9}[.)])\s+", line):
            i = parse_list(i)
            continue
        if i + 1 < len(lines) and re.match(r"^\s*(=+|-+)\s*$", lines[i + 1]) and stripped:
            n = 1 if "=" in lines[i + 1] else 2
            out.append(f"<h{n}>{_md_inline(stripped)}</h{n}>")
            i += 2
            continue
        para = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^\s*(#{1,6}\s|>|```|~~~|([-*+]|\d{1,9}[.)])\s+)", lines[i]) and not (
                "|" in lines[i] and i + 1 < len(lines) and is_table_sep(lines[i + 1])):
            para.append(lines[i].rstrip() if lines[i].endswith("  ") else lines[i].strip())
            i += 1
        out.append(f"<p>{_md_inline(chr(10).join(para))}</p>")
    return "\n".join(out)

DOC_CSS = """body{font-family:'Segoe UI',Arial,sans-serif;font-size:15px;line-height:1.6;color:#1f2328;max-width:820px;margin:32px auto;padding:0 24px}
h1,h2,h3{line-height:1.25;margin:1.4em 0 .5em}h1{font-size:1.9em}h2{font-size:1.45em;border-bottom:1px solid #e5e7eb;padding-bottom:.2em}
code{font-family:Consolas,'Courier New',monospace;background:#f3f4f6;padding:.1em .35em;border-radius:4px;font-size:.92em}
pre{background:#f6f8fa;border:1px solid #e5e7eb;border-radius:8px;padding:12px 14px;overflow:auto}pre code{background:none;padding:0}
blockquote{margin:1em 0;padding:.2em 1em;border-left:4px solid #d0d7de;color:#57606a}table{border-collapse:collapse;margin:1em 0}
th,td{border:1px solid #d0d7de;padding:6px 12px}th{background:#f6f8fa}img{max-width:100%}hr{border:0;border-top:1px solid #e5e7eb;margin:2em 0}
li.task{list-style:none}mark{background:#fff3a3}@media print{body{margin:0;max-width:none}}"""

def html_document(body: str, title: str = "", css: str = DOC_CSS) -> str:
    return (f'<!doctype html>\n<html><head><meta charset="utf-8"><title>{_html.escape(title or "Document")}</title>'
            f"<style>{css}</style></head><body>\n{body}\n</body></html>\n")

# ── universal document reading/writing ───────────────────────────────────────────────────────────────────────────────
def read_blocks(f) -> Tuple[List[Any], str, str]:
    """``(blocks, title, html_or_empty)`` of a document file."""
    from ..memunote.formats import richhtml
    ext = f.ext
    title = f.stem
    if ext == "docx":
        from ..memunote.formats import docx
        blocks, meta = docx.read_docx(f.data)
        return blocks, (meta or {}).get("title") or title, ""
    if ext == "odt":
        from ..memunote.formats import odt
        blocks, meta = odt.read_odt(f.data)
        return blocks, (meta or {}).get("title") or title, ""
    if ext == "rtf":
        from ..memunote.formats import rtf
        blocks, meta = rtf.read_rtf(f.data)
        return blocks, title, ""
    if ext in ("html", "htm", "xhtml"):
        src = f.text()
        m = re.search(r"<title[^>]*>(.*?)</title>", src, re.I | re.S)
        return richhtml.parse_html(src), (_html.unescape(m.group(1)).strip() if m else title), src
    if ext in ("md", "markdown"):
        body = markdown_to_html(f.text())
        return richhtml.parse_html(body), title, body
    if ext in ("epub", "fb2"):
        from ..memunote.formats import books
        book = books.read_book(str(f.path()), f.data)
        body = book.get("html") or ""
        return richhtml.parse_html(body), book.get("title") or title, body
    if ext == "pdf":
        from ..memunote.formats import pdftools
        md = pdftools.extract(str(f.path()), "markdown")
        body = markdown_to_html(md)
        return richhtml.parse_html(body), title, body
    if ext in ("txt", "text", "log", "csv", "") or f.data[:1000].isascii():
        text = f.text()
        return richhtml.text_to_blocks(text), title, ""
    if ext == "doc":
        raise UtilError("Старый формат .doc не поддерживается — пересохраните файл в Word как .docx",
                        "The legacy .doc format is not supported — resave it in Word as .docx")
    raise UtilError(f"Формат .{ext} не поддерживается", f"The .{ext} format is not supported")

def write_document(ctx, blocks, title: str, target: str, name: str, html_body: str = "") -> str:
    from ..memunote.formats import richhtml
    if target == "docx":
        from ..memunote.formats import docx
        ctx.out_bytes(f"{name}.docx", docx.write_docx(blocks, title))
    elif target == "odt":
        from ..memunote.formats import odt
        ctx.out_bytes(f"{name}.odt", odt.write_odt(blocks, title))
    elif target == "rtf":
        from ..memunote.formats import rtf
        ctx.out_bytes(f"{name}.rtf", rtf.write_rtf(blocks))
    elif target == "html":
        ctx.out_text(f"{name}.html", html_document(html_body or richhtml.to_html(blocks), title))
    elif target == "md":
        ctx.out_text(f"{name}.md", richhtml.to_markdown(blocks))
    elif target == "txt":
        ctx.out_text(f"{name}.txt", richhtml.to_text(blocks))
    elif target == "pdf":
        ctx.pdf_job(html_document(html_body or richhtml.to_html(blocks), title), f"{name}.pdf", text=richhtml.to_text(blocks))
    else:
        raise UtilError("Неизвестный формат", "Unknown format")
    return target

TARGETS = [("docx", "Word (DOCX)", "Word (DOCX)"), ("pdf", "PDF", "PDF"), ("odt", "OpenDocument (ODT)", "OpenDocument (ODT)"), ("rtf", "RTF", "RTF"),
           ("html", "HTML", "HTML"), ("md", "Markdown", "Markdown"), ("txt", "Текст (TXT)", "Plain text (TXT)")]

@utility("doc.convert", "docs", "Конвертер документов", "Document converter",
         "DOCX, ODT, RTF, HTML, Markdown, TXT, EPUB, FB2 и PDF (текст) → DOCX, PDF, ODT, RTF, HTML, Markdown или TXT.",
         "DOCX, ODT, RTF, HTML, Markdown, TXT, EPUB, FB2 and PDF (text) → DOCX, PDF, ODT, RTF, HTML, Markdown or TXT.",
         input="files", accept=DOC_ACCEPT, ai_default=True,
         opts=[O("to", "choice", "Во что", "To", "docx", choices=TARGETS)],
         keys="конвертировать документ word docx pdf odt rtf html markdown txt epub fb2 convert document")
def doc_convert(ctx):
    if not ctx.files:
        raise UtilError("Добавьте документ", "Add a document")
    done = []
    for f in ctx.files:
        if f.ext == ctx.o["to"]:
            raise UtilError(f"«{f.name}» уже в формате .{f.ext}", f"“{f.name}” is already .{f.ext}")
        blocks, title, body = read_blocks(f)
        if f.ext in ("docx", "odt", "rtf"):
            body = ""
        write_document(ctx, blocks, title, ctx.o["to"], f.stem, body if ctx.o["to"] in ("html", "pdf") else "")
        done.append(f.name)
    note = ""
    if any(f.ext == "pdf" for f in ctx.files):
        note = ctx.tr("Из PDF переносится только текст и структура (заголовки, списки), без вёрстки", "Only the text and structure (headings, lists) come over from a PDF, not the layout")
    return {"text": ctx.tr(f"Готово: {len(done)} → .{ctx.o['to']}", f"Done: {len(done)} → .{ctx.o['to']}"), "note": note}

@utility("doc.text", "docs", "Текст из любого документа", "Text from any document",
         "Достаёт чистый текст из DOCX, ODT, RTF, PDF, EPUB, FB2, HTML, EML, PPTX, XLSX/ODS, CSV и других — для копирования или поиска.",
         "Pulls plain text out of DOCX, ODT, RTF, PDF, EPUB, FB2, HTML, EML, PPTX, XLSX/ODS, CSV and more — to copy or search.",
         input="file", accept=DOC_ACCEPT + ",.eml,.pptx,.odp,.xlsx,.ods,.csv,.json,.xml", ai_default=True,
         opts=[O("file", "bool", "Сохранить файлом", "Save as a file", False)],
         keys="извлечь текст документ word pptx презентация extract text document")
def doc_text(ctx):
    f = ctx.file()
    ext = f.ext
    text = ""
    if ext in ("pptx", "odp"):
        text = _slides_text(f.data, ext)
    elif ext in ("xlsx", "ods", "xlsm"):
        from ..memunote.formats import sheets
        book = sheets.read_sheet(str(f.path()), f.data)
        text = "\n\n".join(f"## {s.get('name')}\n" + sheets.to_csv(s.get("rows") or [], "\t") for s in book.get("sheets") or [])
    elif ext == "eml":
        from ..memunote.formats import email_msg
        msg = email_msg.parse(f.data)
        text = f"{msg['subject']}\n\n{msg['text']}"
    elif ext == "docx":
        from ..memunote.formats import docx
        text = docx.docx_text(f.data)
    elif ext in ("txt", "csv", "json", "xml", "log", "ini", "yaml", "yml", "md", "markdown"):
        text = f.text()
    elif ext == "pdf":
        from ..memunote.formats import pdftools
        reader = open_pdf(f)
        text = pdftools.extract(str(f.path()), "text") if not reader.is_encrypted else ""
    else:
        from ..memunote.formats import richhtml
        blocks, _title, _body = read_blocks(f)
        text = richhtml.to_text(blocks)
    text = text.strip()
    if not text:
        raise UtilError("Текста в документе не нашлось", "No text was found in the document")
    if ctx.o["file"] or len(text) > 200_000:
        ctx.out_text(f"{f.stem}.txt", text)
    words = len(re.findall(r"\w+", text))
    return {"text": text[:200_000], "note": ctx.tr(f"Символов: {len(text):,} · слов: {words:,}".replace(",", " "), f"Characters: {len(text):,} · words: {words:,}")}

def _slides_text(data: bytes, ext: str) -> str:
    out = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UtilError("Файл повреждён или это не презентация", "The file is damaged or not a presentation")
    with archive:
        if ext == "pptx":
            names = sorted((n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
            for k, name in enumerate(names, 1):
                xml = archive.read(name).decode("utf-8", "replace")
                paras = []
                for p in re.findall(r"<a:p>(.*?)</a:p>", xml, re.S):
                    line = "".join(_html.unescape(t) for t in re.findall(r"<a:t>(.*?)</a:t>", p, re.S))
                    if line.strip():
                        paras.append(line)
                notes_name = f"ppt/notesSlides/notesSlide{re.findall(chr(92) + 'd+', name)[-1]}.xml"
                notes = ""
                if notes_name in archive.namelist():
                    notes = " ".join(_html.unescape(t) for t in re.findall(r"<a:t>(.*?)</a:t>", archive.read(notes_name).decode("utf-8", "replace")))
                out.append(f"--- {k} ---\n" + "\n".join(paras) + (f"\n[notes] {notes.strip()}" if notes.strip() else ""))
        else:
            xml = archive.read("content.xml").decode("utf-8", "replace")
            for k, page in enumerate(re.findall(r"<draw:page\b.*?</draw:page>", xml, re.S), 1):
                paras = [re.sub(r"<[^>]+>", "", p) for p in re.findall(r"<text:p[^>]*>(.*?)</text:p>", page, re.S)]
                out.append(f"--- {k} ---\n" + "\n".join(_html.unescape(p) for p in paras if p.strip()))
    return "\n\n".join(out)

ENCODINGS = [("utf-8", "UTF-8", "UTF-8"), ("utf-8-sig", "UTF-8 с BOM", "UTF-8 with BOM"), ("cp1251", "Windows-1251", "Windows-1251"),
             ("koi8-r", "KOI8-R", "KOI8-R"), ("cp866", "CP866 (DOS)", "CP866 (DOS)"), ("utf-16", "UTF-16 LE", "UTF-16 LE"),
             ("iso-8859-1", "Latin-1", "Latin-1"), ("cp1252", "Windows-1252", "Windows-1252")]

@utility("doc.encoding", "docs", "Кодировка текста: определить и перекодировать", "Text encoding: detect and convert",
         "Определяет кодировку (UTF-8, Windows-1251, KOI8-R, UTF-16…) и переносы строк, перекодирует файл и меняет CRLF/LF.",
         "Detects the encoding (UTF-8, Windows-1251, KOI8-R, UTF-16…) and line endings, re-encodes the file and switches CRLF/LF.",
         input="files", accept=".txt,.csv,.srt,.ini,.log,.md,.xml,.json,.html,.htm,.sub,.nfo,.bat,.cfg",
         opts=[O("to", "choice", "Перекодировать в", "Convert to", "utf-8", choices=[("", "Только определить", "Detect only")] + ENCODINGS),
               O("eol", "choice", "Переносы строк", "Line endings", "keep", choices=[("keep", "Как были", "Keep"), ("lf", "LF (Linux, macOS)", "LF (Linux, macOS)"), ("crlf", "CRLF (Windows)", "CRLF (Windows)")]),
               O("from", "choice", "Исходная кодировка", "Source encoding", "", choices=[("", "Определить автоматически", "Detect automatically")] + ENCODINGS[2:] + [("utf-8", "UTF-8", "UTF-8"), ("utf-16", "UTF-16", "UTF-16")])],
         keys="кодировка кракозябры cp1251 utf-8 koi8 перекодировать encoding charset line endings crlf")
def doc_encoding(ctx):
    from ..memunote.formats import textio
    if not ctx.files:
        raise UtilError("Добавьте файл", "Add a file")
    rows = []
    for f in ctx.files:
        info = textio.detect_bytes(f.data[:1_000_000], f.size <= 1_000_000)
        if info.binary and not ctx.o["from"]:
            rows.append([f.name, ctx.tr("двоичный файл, не текст", "binary, not text"), "", "", ""])
            continue
        source = ctx.o["from"] or info.encoding
        try:
            text = textio.decode(f.data, source)
        except Exception:  # noqa: BLE001
            text = f.data.decode(source, "replace")
        eol = {"\r\n": "CRLF", "\n": "LF", "\r": "CR"}.get(info.eol, str(info.eol or "").upper() or "—")
        status = ""
        if ctx.o["to"]:
            target = ctx.o["to"]
            body = text if ctx.o["eol"] == "keep" else textio.convert_eol(text.replace("\r\n", "\n").replace("\r", "\n"), ctx.o["eol"])
            data = body.encode(target, "replace")
            if target == "utf-16":
                data = body.encode("utf-16")
            lost = body.count("\ufffd")
            ctx.out_bytes(f.name, data)
            status = ctx.tr(f"→ {target}", f"→ {target}") + (ctx.tr(f", не поместилось символов: {lost}", f", characters lost: {lost}") if lost else "")
        rows.append([f.name, info.encoding + (" + BOM" if info.bom else ""), eol, f"{round(info.confidence * 100)}%", status])
    return {"table": table([ctx.tr("Файл", "File"), ctx.tr("Кодировка", "Encoding"), ctx.tr("Переносы", "EOL"), ctx.tr("Уверенность", "Confidence"), ctx.tr("Результат", "Result")], rows)}

@utility("md.convert", "docs", "Markdown → HTML, Word, PDF", "Markdown → HTML, Word, PDF",
         "Превращает Markdown (заголовки, списки, таблицы, код, задачи) в HTML-код или файл Word, PDF, ODT, RTF.",
         "Turns Markdown (headings, lists, tables, code, task lists) into HTML code or a Word, PDF, ODT, RTF file.",
         input="text", ai_default=True,
         opts=[O("to", "choice", "Во что", "To", "html", choices=[("html", "HTML-код", "HTML code"), ("page", "HTML-страница (файл)", "HTML page (file)")] + [t for t in TARGETS if t[0] in ("docx", "pdf", "odt", "rtf")]),
               O("title", "text", "Название документа", "Document title", "")],
         live=False, placeholder="# Заголовок\n\nТекст с **жирным** и [ссылкой](https://example.com).", placeholder_en="# Title\n\nText with **bold** and a [link](https://example.com).",
         keys="markdown md html word docx pdf конвертировать разметка")
def md_convert(ctx):
    from ..memunote.formats import richhtml
    src = ctx.need_text()
    body = markdown_to_html(src)
    title = ctx.o["title"] or (re.search(r"^#\s+(.+)$", src, re.M).group(1).strip() if re.search(r"^#\s+(.+)$", src, re.M) else "Document")
    target = ctx.o["to"]
    if target == "html":
        return {"text": body, "lang": "html"}
    if target == "page":
        ctx.out_text(f"{_slug(title)}.html", html_document(body, title))
        return {"text": html_document(body, title), "lang": "html"}
    blocks = richhtml.parse_html(body)
    write_document(ctx, blocks, title, target, _slug(title), body)
    return ctx.tr(f"Готово: {title}.{target}", f"Done: {title}.{target}")

def _slug(text: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]+", "", str(text or "document")).strip()[:80] or "document"

@utility("html.md", "docs", "HTML → Markdown или текст", "HTML → Markdown or text",
         "Переводит HTML-код (из письма, сайта, редактора) в чистый Markdown или обычный текст.",
         "Turns HTML code (from an email, a site, an editor) into clean Markdown or plain text.",
         input="text", ai_default=True, live=True,
         opts=[O("to", "choice", "Во что", "To", "md", choices=[("md", "Markdown", "Markdown"), ("text", "Текст", "Text")])],
         placeholder="<h1>Заголовок</h1><p>Текст с <b>жирным</b></p>", placeholder_en="<h1>Title</h1><p>Text with <b>bold</b></p>",
         keys="html markdown текст разметка теги убрать strip tags")
def html_md(ctx):
    from ..memunote.formats import richhtml
    src = ctx.need_text("Вставьте HTML", "Paste some HTML")
    blocks = richhtml.parse_html(src)
    if ctx.o["to"] == "text":
        return richhtml.to_text(blocks)
    return {"text": richhtml.to_markdown(blocks), "lang": "markdown"}

@utility("eml.read", "docs", "Открыть письмо (.eml)", "Open an email (.eml)",
         "Показывает отправителя, получателей, тему, дату и текст письма .eml и сохраняет вложения отдельными файлами.",
         "Shows the sender, recipients, subject, date and body of an .eml message and saves its attachments as files.",
         input="file", accept=".eml,.mht,.mhtml", ai_default=True,
         opts=[O("attachments", "bool", "Сохранить вложения", "Save attachments", True)],
         keys="письмо почта eml email вложения attachments mail message")
def eml_read(ctx):
    from email import policy
    from email.parser import BytesParser
    from ..memunote.formats import email_msg
    f = ctx.file()
    msg = email_msg.parse(f.data)

    def people(items):
        return ", ".join((f"{a.get('name')} <{a.get('email')}>" if a.get("name") else a.get("email") or "") for a in items or [])
    info = [[ctx.tr("Тема", "Subject"), msg["subject"]], [ctx.tr("От", "From"), people(msg["from"])], [ctx.tr("Кому", "To"), people(msg["to"])]]
    if msg["cc"]:
        info.append([ctx.tr("Копия", "Cc"), people(msg["cc"])])
    info.append([ctx.tr("Дата", "Date"), msg["date"]])
    if msg["attachments"]:
        info.append([ctx.tr("Вложения", "Attachments"), ", ".join(f"{a['name']} ({human_size(a['size'], ctx.en)})" for a in msg["attachments"][:30])])
        if ctx.o["attachments"]:
            parsed = BytesParser(policy=policy.default).parsebytes(f.data)
            _bodies, parts = email_msg._parts(parsed)  # noqa: SLF001 - the same part order as parse()
            for part in parts[:100]:
                payload = part.get_payload(decode=True) or b""
                if payload:
                    ctx.out_bytes(str(part.get_filename() or "attachment.bin"), payload)
    return {"info": info, "text": msg["text"]}
