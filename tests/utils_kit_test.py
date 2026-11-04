"""MemuUtils: every utility runs on generated sample files (images, audio, video, PDF, DOCX, XLSX, EML, EPUB…) and
its outputs open again (Pillow / PyAV / pypdf / zipfile / json). Dangerous system utilities run only in safe modes on
temporary folders. Network utilities run only with MEMU_TEST_NET=1. Also: the catalog, the AI skill text, settings,
chunked uploads, output references, the chat source gate (AI access list, confirmation)."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import os
import struct
import subprocess
import sys
import time
import wave
import zipfile
from email.message import EmailMessage
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NET = os.environ.get("MEMU_TEST_NET") == "1"
SAMPLES: dict = {}

# ── sample files ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _png(path: Path, shift: int = 0) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (320, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for x in range(20, 300):
        d.line([(x, 20), (x, 180)], fill=(40 + x // 2, 90, 200 - x // 3, 255))
    d.rectangle([60 + shift, 60, 140 + shift, 140], fill=(250, 220, 40, 255))
    d.ellipse([180, 50, 260, 130], fill=(220, 40, 90, 255))
    d.text((30, 150), "MemuUtils 123", fill=(255, 255, 255, 255))
    img.save(path)

def _jpg(path: Path) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (640, 400), (200, 210, 230))
    d = ImageDraw.Draw(img)
    for i in range(0, 640, 40):
        d.rectangle([i, 0, i + 20, 400], fill=(30 + i // 4, 120, 160))
    d.text((40, 40), "Photo", fill=(0, 0, 0))
    exif = img.getexif()
    exif[0x010F] = "MemuCam"
    exif[0x0110] = "Model X"
    exif[0x0132] = "2024:05:01 12:00:00"
    exif[0x0112] = 6
    img.save(path, "JPEG", quality=90, exif=exif)

def _gif(path: Path) -> None:
    from PIL import Image, ImageDraw
    frames = []
    for i in range(3):
        f = Image.new("RGB", (120, 80), (255, 255, 255))
        ImageDraw.Draw(f).rectangle([10 + i * 30, 20, 40 + i * 30, 60], fill=(200, 30 * i, 60))
        frames.append(f)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=200, loop=0)

def _wav(path: Path, seconds: float = 3.0, freq: float = 440.0, edge: float = 0.5) -> None:
    rate = 44100
    n = int(rate * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            t = i / rate
            v = 0 if t < edge or t > seconds - edge else int(12000 * math.sin(2 * math.pi * freq * t))
            frames += struct.pack("<hh", v, v)
        w.writeframes(bytes(frames))

def _encode_audio(src: Path, dst: Path, codec: str) -> None:
    import av
    with av.open(str(src)) as inp, av.open(str(dst), "w") as out:
        stream = out.add_stream(codec, rate=44100)
        stream.layout = "stereo"
        for frame in inp.decode(audio=0):
            frame.pts = None
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)

def _mp4(path: Path, seconds: float = 3.0) -> None:
    import av
    import numpy as np
    fps = 25
    with av.open(str(path), "w") as out:
        v = out.add_stream("libx264", rate=fps)
        v.width, v.height, v.pix_fmt = 320, 240, "yuv420p"
        a = out.add_stream("aac", rate=44100)
        a.layout = "stereo"
        total = int(seconds * fps)
        for i in range(total):
            img = np.zeros((240, 320, 3), dtype=np.uint8)
            img[:, :, 0] = (i * 8) % 255
            img[60:180, (i * 8) % 260:(i * 8) % 260 + 60, 1] = 255
            frame = av.VideoFrame.from_ndarray(img, format="rgb24")
            for packet in v.encode(frame):
                out.mux(packet)
        samples = int(seconds * 44100)
        t = np.arange(samples) / 44100.0
        tone = (0.3 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
        chunk = 1024
        for start in range(0, samples, chunk):
            part = tone[start:start + chunk]
            if len(part) < chunk:
                part = np.pad(part, (0, chunk - len(part)))
            frame = av.AudioFrame.from_ndarray(np.vstack([part, part]), format="fltp", layout="stereo")
            frame.sample_rate = 44100
            for packet in a.encode(frame):
                out.mux(packet)
        for packet in v.encode(None):
            out.mux(packet)
        for packet in a.encode(None):
            out.mux(packet)

def _zip(path: Path, files: dict) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            if name == "mimetype":
                z.writestr(zipfile.ZipInfo(name), data, compress_type=zipfile.ZIP_STORED)
            else:
                z.writestr(name, data)

def _docx(path: Path) -> None:
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = (f'<w:document {w}><w:body>'
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Отчёт MemuUtils</w:t></w:r></w:p>'
            '<w:p><w:r><w:t xml:space="preserve">Первый абзац с </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>жирным</w:t></w:r><w:r><w:t> словом.</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Second paragraph in English.</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Имя</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Город</w:t></w:r></w:p></w:tc></w:tr>'
            '<w:tr><w:tc><w:p><w:r><w:t>Анна</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Москва</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '</w:body></w:document>')
    _zip(path, {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                               '<Default Extension="xml" ContentType="application/xml"/>'
                               '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + body,
    })

def _pptx(path: Path) -> None:
    _zip(path, {"[Content_Types].xml": "<Types/>",
                "ppt/slides/slide1.xml": '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                                         'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree>'
                                         '<p:sp><p:txBody><a:p><a:r><a:t>Слайд один</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
                "ppt/slides/slide2.xml": '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                                         'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree>'
                                         '<p:sp><p:txBody><a:p><a:r><a:t>Slide two</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'})

def _epub(path: Path) -> None:
    _zip(path, {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                                  '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        "OEBPS/content.opf": '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
                             '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Книга</dc:title><dc:identifier id="id">x</dc:identifier></metadata>'
                             '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/></manifest>'
                             '<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>',
        "OEBPS/c1.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Глава 1</h1><p>Жили-были утилиты.</p></body></html>',
        "OEBPS/c2.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter 2</h1><p>The end.</p></body></html>',
    })

def _fb2(path: Path) -> None:
    path.write_text('<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
                    '<description><title-info><book-title>Сказка</book-title></title-info></description>'
                    '<body><section><title><p>Начало</p></title><p>Текст сказки про MemuUtils.</p></section></body></FictionBook>', "utf-8")

def _eml(path: Path) -> None:
    msg = EmailMessage()
    msg["Subject"] = "Отчёт за май"
    msg["From"] = "Анна <anna@example.com>"
    msg["To"] = "boss@example.com"
    msg["Date"] = "Wed, 01 May 2024 12:00:00 +0300"
    msg.set_content("Привет!\nОтчёт во вложении.\n")
    msg.add_alternative("<p>Привет!<br><b>Отчёт</b> во вложении.</p>", subtype="html")
    msg.add_attachment(b"id,sum\n1,10\n", maintype="text", subtype="csv", filename="report.csv")
    path.write_bytes(bytes(msg))

def _pdf(path: Path, png: Path, jpg: Path = None) -> None:
    try:
        import fitz  # PyMuPDF: only to make a realistic sample (text + an image) when it is installed
        doc = fitz.open()
        for i in range(3):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 90), f"Page {i + 1}: MemuUtils PDF sample", fontsize=16)
            page.insert_text((72, 120), "Second line of text for extraction.", fontsize=11)
            if i == 1:
                page.insert_image(fitz.Rect(72, 160, 392, 360), filename=str(png))
            if i == 2 and jpg is not None:
                from PIL import Image
                big = path.with_name("big.jpg")
                Image.open(jpg).resize((1600, 1000)).save(big, "JPEG", quality=95)
                page.insert_image(fitz.Rect(72, 160, 520, 440), filename=str(big))
        doc.save(str(path))
        doc.close()
    except ImportError:
        from app.utils_kit.t_gen import simple_pdf
        path.write_bytes(simple_pdf(["Page 1: MemuUtils PDF sample", "Second line of text for extraction."] * 3))

CSV = "id,name,city,amount,date\n1,Анна,Москва,10.5,2024-05-01\n2,Борис,Казань,7,2024-05-02\n3,Анна,Москва,3.25,2024-05-03\n3,Анна,Москва,3.25,2024-05-03\n4, Вера ,Пермь,,2024-05-04\n"
CSV2 = "id,phone\n1,+79120000001\n2,+79120000002\n5,+79120000005\n"
JSON_DATA = [{"id": 1, "name": "Анна", "tags": ["a", "b"], "address": {"city": "Москва", "zip": "101000"}},
             {"id": 2, "name": "Boris", "tags": [], "address": {"city": "Kazan", "zip": "420000"}}]

def make_samples(folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    s = {k: folder / v for k, v in {"png": "pic.png", "png2": "pic2.png", "jpg": "photo.jpg", "gif": "anim.gif", "wav": "tone.wav",
                                     "wav2": "tone2.wav", "mp3": "song.mp3", "mp4": "clip.mp4", "pdf": "doc.pdf", "docx": "report.docx",
                                     "pptx": "slides.pptx", "epub": "book.epub", "fb2": "tale.fb2", "eml": "mail.eml", "csv": "table.csv",
                                     "csv2": "phones.csv", "json": "data.json", "xlsx": "book.xlsx", "txt1251": "old.txt", "md": "note.md",
                                     "html": "page.html", "logo": "logo.png"}.items()}
    _png(s["png"])
    _png(s["png2"], shift=40)
    _jpg(s["jpg"])
    _gif(s["gif"])
    _wav(s["wav"])
    _wav(s["wav2"], seconds=2.0, freq=660.0, edge=0.2)
    _encode_audio(s["wav2"], s["mp3"], "libmp3lame")
    _mp4(s["mp4"])
    _jpg(s["jpg"])
    _pdf(s["pdf"], s["png"], s["jpg"])
    _docx(s["docx"])
    _pptx(s["pptx"])
    _epub(s["epub"])
    _fb2(s["fb2"])
    _eml(s["eml"])
    s["csv"].write_text(CSV, "utf-8")
    s["csv2"].write_text(CSV2, "utf-8")
    s["json"].write_text(json.dumps(JSON_DATA, ensure_ascii=False), "utf-8")
    from app.utils_kit.t_data import write_xlsx
    s["xlsx"].write_bytes(write_xlsx({"Продажи": [["id", "item", "qty", "price"], [1, "Чай", 3, 120.5], [2, "Кофе", 1, 340], [3, "Чай", 2, 120.5]],
                                      "Second": [["k", "v"], ["a", 1]]}))
    s["txt1251"].write_bytes("Старый файл в кодировке Windows-1251.\r\nВторая строка — тире.\r\n".encode("cp1251"))
    s["md"].write_text("# Заголовок\n\nТекст с **жирным** и `кодом`.\n\n- один\n- два\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\nprint('hi')\n```\n", "utf-8")
    s["html"].write_text("<html><head><title>Стр</title></head><body><h1>Привет</h1><p>Абзац <a href='https://example.com'>ссылка</a>.</p>"
                         "<ul><li>раз</li><li>два</li></ul><table><tr><th>x</th><th>y</th></tr><tr><td>1</td><td>2</td></tr></table></body></html>", "utf-8")
    from PIL import Image
    Image.new("RGBA", (64, 64), (255, 255, 255, 200)).save(s["logo"])
    return s

def f(*keys: str) -> list:
    """Window-style file entries (name + data URL) for the sample keys."""
    out = []
    for key in keys:
        path = SAMPLES[key]
        out.append({"name": path.name, "data": "data:application/octet-stream;base64," + base64.b64encode(path.read_bytes()).decode()})
    return out

# ── output checks ────────────────────────────────────────────────────────────────────────────────────────────────────
def check_output(path: Path) -> None:
    ext = path.suffix.lower()
    assert path.stat().st_size > 0, f"{path.name} is empty"
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".ico", ".avif"):
        from PIL import Image
        try:
            import pillow_heif  # noqa: F401
            pillow_heif.register_heif_opener()
        except Exception:  # noqa: BLE001
            pass
        with Image.open(path) as img:
            img.load()
            assert img.size[0] > 0 and img.size[1] > 0
    elif ext in (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".ac3", ".wma", ".mp4", ".webm", ".mkv", ".mov", ".avi"):
        import av
        with av.open(str(path)) as c:
            assert c.streams, f"{path.name}: no streams"
            stream = (c.streams.video or c.streams.audio)[0]
            got = False
            for frame in c.decode(stream):
                got = True
                break
            assert got, f"{path.name}: nothing decodes"
    elif ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            return
        assert len(reader.pages) > 0
        reader.pages[0].extract_text()
    elif ext in (".zip", ".docx", ".xlsx", ".odt", ".epub", ".pptx"):
        with zipfile.ZipFile(path) as z:
            assert z.testzip() is None
            names = z.namelist()
            assert names
            if ext == ".docx":
                assert "word/document.xml" in names
            if ext == ".xlsx":
                assert "xl/workbook.xml" in names
            if ext == ".odt":
                assert "content.xml" in names and z.read("mimetype") == b"application/vnd.oasis.opendocument.text"
    elif ext == ".json":
        json.loads(path.read_text("utf-8"))
    elif ext == ".svg":
        import xml.dom.minidom
        xml.dom.minidom.parseString(path.read_bytes())
    elif ext == ".parquet":
        assert path.read_bytes()[:4] == b"PAR1"
    elif ext in (".html", ".htm", ".xml"):
        assert "<" in path.read_text("utf-8", "replace")

# ── cases: (id, util, kwargs, expect) ─────────────────────────────────────────────────────────────────────────────────
LOREM = "Привет, мир! Это тестовый текст. Почта: test@example.com, сайт https://example.com, телефон +7 912 345-67-89."
JWT = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ."
       "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")

def T(text, **opts):
    return {"text": text, "opts": opts}

def T2(text, text2, **opts):
    return {"text": text, "text2": text2, "opts": opts}

def F(*keys, **opts):
    return {"files": lambda: f(*keys), "opts": opts}

def N(**opts):
    return {"opts": opts}

CASES = [
    # text
    ("text.case-snake", "text.case", T("Hello World Привет", mode="snake"), "hello_world_привет"),
    ("text.case-title", "text.case", T("привет мир", mode="title"), "Привет Мир"),
    ("text.lines-sort", "text.lines", T("b\na\nc\na", op="sort"), "a\na\nb\nc"),
    ("text.lines-unique", "text.lines", T("b\na\nb", op="unique"), None),
    ("text.lines-grep", "text.lines", T("one\ntwo\nthree", op="grep", value="t"), "two"),
    ("text.replace", "text.replace", T("cat cat dog", find="cat", repl="fox"), "fox fox dog"),
    ("text.replace-regex", "text.replace", T("a1 b22", find=r"\d+", repl="#", regex=True), "a# b#"),
    ("text.stats", "text.stats", T(LOREM * 3), None),
    ("text.translit", "text.translit", T("Щука и ёж"), None),
    ("text.translit-back", "text.translit", T("Privet", dir="to_cyr"), "Привет"),
    ("text.layout", "text.layout", T("ghbdtn"), "привет"),
    ("text.clean", "text.clean", T("  a   b  \n\n\n c\u200b "), None),
    ("text.typograph", "text.typograph", T('"Привет" - сказал он... 1/2'), None),
    ("text.extract-email", "text.extract", T(LOREM, what="email"), "test@example.com"),
    ("text.extract-url", "text.extract", T(LOREM, what="url"), "https://example.com"),
    ("text.extract-phone", "text.extract", T(LOREM, what="phone"), None),
    ("text.diff", "text.diff", T2("a\nb\nc", "a\nB\nc\nd"), None),
    ("text.diff-words", "text.diff", T2("one two three", "one 2 three", mode="words"), None),
    ("text.wrap", "text.wrap", T(LOREM * 4, width=40), None),
    ("text.reverse", "text.reverse", T("abc", mode="chars"), "cba"),
    ("text.reverse-flip", "text.reverse", T("hello", mode="flip"), None),
    ("text.slug", "text.slug", T("Привет, Мир 2024!"), "privet-mir-2024"),
    ("text.numwords", "text.numwords", T("1234.56", money="rub"), None),
    ("text.numwords-en", "text.numwords", T("21", lang="en"), None),
    ("text.morse", "text.morse", T("SOS"), "... --- ..."),
    ("text.morse-decode", "text.morse", T("... --- ..."), "SOS"),
    ("text.fancy", "text.fancy", T("Memu", style="all"), None),
    ("text.tokens", "text.tokens", T(LOREM * 20), None),
    ("text.tokens-chunk", "text.tokens", T(LOREM * 60, mode="chunk", size=200), None),
    ("text.mojibake", "text.mojibake", T("Ïðèâåò, ìèð"), "Привет"),
    ("text.template", "text.template", T2("Hello, {name}! You owe {sum}.", "name,sum\nAnna,10\nBoris,20"), "Hello, Anna!"),
    # format
    ("fmt.json", "fmt.json", T('{"b":1,"a":[1,2,{"c":null}]}', sort=True), '"a"'),
    ("fmt.json-minify", "fmt.json", T('{ "a" : 1 ,\n "b": [1, 2] }', mode="minify"), '{"a":1,"b":[1,2]}'),
    ("fmt.json-lenient", "fmt.json", T("{a: 1, 'b': 'x', // c\n}"), None),
    ("fmt.xml", "fmt.xml", T("<a><b x='1'>t</b><c/></a>"), None),
    ("fmt.html", "fmt.html", T("<div><p>Hi<b>there</b></p><ul><li>1</li></ul></div>"), None),
    ("fmt.html-minify", "fmt.html", T("<div>\n  <p> Hi </p>\n</div>", mode="minify"), None),
    ("fmt.css", "fmt.css", T("a{color:red;margin:0}b{x:y}"), None),
    ("fmt.css-minify", "fmt.css", T("a {\n color: red; /* c */\n}\n", mode="minify"), None),
    ("fmt.sql", "fmt.sql", T("select a,b from t where x=1 and y in (select z from q) order by a"), "SELECT"),
    ("fmt.table-md", "fmt.table", T("a,b\n1,2\n3,4"), "|"),
    ("fmt.table-ascii", "fmt.table", T("a\tb\n1\t2", to="ascii"), None),
    ("fmt.table-json", "fmt.table", T("a;b\n1;2", to="json"), None),
    ("fmt.number", "fmt.number", T("1234567.891"), None),
    ("fmt.number-bytes", "fmt.number", T("1536000", style="bytes"), None),
    # dev
    ("dev.cron", "dev.cron", T("*/15 9-18 * * 1-5"), None),
    ("dev.chmod", "dev.chmod", T("755"), "rwxr-xr-x"),
    ("dev.chmod-sym", "dev.chmod", T("rw-r--r--"), "644"),
    ("dev.http", "dev.http", T("404"), None),
    ("dev.mime-text", "dev.mime", T("pdf"), "application/pdf"),
    ("dev.mime-file", "dev.mime", F("png"), "image/png"),
    ("dev.useragent", "dev.useragent", T("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"), "Chrome"),
    ("dev.calc", "dev.calc", T("2+2*sqrt(16)"), "10"),
    ("dev.calc-multi", "dev.calc", T("x = 5\ny = x * 2\ny + 1"), "11"),
    ("dev.json_code-ts", "dev.json_code", T('{"id":1,"name":"a","tags":["x"],"geo":{"lat":1.5}}'), "interface"),
    ("dev.json_code-py", "dev.json_code", T('{"id":1,"name":"a"}', lang="pyd"), None),
    ("dev.json_code-schema", "dev.json_code", T('[{"id":1},{"id":2,"x":"y"}]', lang="schema"), None),
    ("dev.escape", "dev.escape", T('Say "hi"\n\tok'), None),
    ("dev.escape-decode", "dev.escape", T('Say \\"hi\\"\\n', dir="decode"), None),
    ("dev.regex", "dev.regex", T("a1 b22 c333", pattern=r"(\w)(\d+)"), None),
    ("dev.url_parse", "dev.url_parse", T("https://user@example.com:8080/a/b?x=1&utm_source=y#frag"), "example.com"),
    ("dev.curl", "dev.curl", T("curl -X POST https://api.example.com/v1 -H 'Content-Type: application/json' -d '{\"a\":1}'"), "requests"),
    ("dev.curl-fetch", "dev.curl", T("curl https://example.com -H 'X-A: 1'", lang="fetch"), "fetch"),
    ("dev.gitignore", "dev.gitignore", N(python=True, node=True), "__pycache__"),
    # code / encoders
    ("enc.base64", "enc.base64", T("Привет"), "0J/RgNC40LLQtdGC"),
    ("enc.base64-decode", "enc.base64", T("0J/RgNC40LLQtdGC", dir="decode"), "Привет"),
    ("enc.file_base64", "enc.file_base64", F("png"), "data:image/png;base64,"),
    ("enc.base64_file", "enc.base64_file", lambda: T(base64.b64encode(SAMPLES["png"].read_bytes()).decode()), "#files"),
    ("enc.url", "enc.url", T("a b&c=д"), None),
    ("enc.url-decode", "enc.url", T("a%20b%26c%3D%D0%B4", dir="decode"), "a b&c=д"),
    ("enc.html", "enc.html", T("<a href='x'>&</a>"), "&lt;"),
    ("enc.unicode", "enc.unicode", T("Привет 👋"), None),
    ("enc.unicode-inspect", "enc.unicode", T("A€👋", mode="inspect"), None),
    ("enc.hex", "enc.hex", T("Hi"), "48 69"),
    ("enc.hex-decode", "enc.hex", T("48 69", dir="decode"), "Hi"),
    ("enc.basen-b58", "enc.basen", T("hello", alg="b58"), "Cn8eVZg"),
    ("enc.basen-b32-decode", "enc.basen", T("NBSWY3DP", dir="decode", alg="b32"), "hello"),
    ("enc.punycode", "enc.punycode", T("пример.рф"), "xn--"),
    ("enc.jwt", "enc.jwt", T(JWT, secret="your-256-bit-secret"), "John Doe"),
    ("enc.hash", "enc.hash", T("abc", alg="sha256"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    ("enc.hash-file", "enc.hash", F("png"), None),
    ("enc.hmac", "enc.hmac", T("message", key="secret"), "8b5f48702995c1598c573db1e21866a9b825d4a794d169d7060a03605796360b"),
    ("enc.cipher-caesar", "enc.cipher", T("Hello", alg="caesar", shift=3), "Khoor"),
    ("enc.cipher-vigenere", "enc.cipher", T("ATTACKATDAWN", alg="vigenere", key="LEMON"), "LXFOPVEFRNHR"),
    ("enc.mime", "enc.mime", T("=?utf-8?B?0J/RgNC40LLQtdGC?="), None),
    ("enc.mime-qp", "enc.mime", T("Привет", dir="encode"), None),
    ("enc.compress", "enc.compress", T("hello hello hello hello"), None),
    # check
    ("val.json-ok", "val.json", T('{"a": 1}'), None),
    ("val.json-bad", "val.json", T('{"a": 1,,}'), None),
    ("val.email", "val.email", T("test@example.com\nbad@\nuser@gmail.con"), None),
    ("val.card", "val.card", T("4111 1111 1111 1111"), None),
    ("val.iban", "val.iban", T("DE89370400440532013000"), None),
    ("val.ru_ids", "val.ru_ids", T("7707083893"), None),
    ("val.ru_ids-gen", "val.ru_ids", T("", mode="generate", kind="snils", count=3), None),
    ("val.isbn", "val.isbn", T("978-3-16-148410-0"), None),
    ("val.password", "val.password", T("P@ssw0rd123"), None),
    ("val.phone", "val.phone", T("8 (912) 345-67-89"), "+7"),
    ("val.semver", "val.semver", T("1.2.3-beta.1\n1.10.0\n1.2.3"), None),
    ("val.uuid", "val.uuid", T("123e4567-e89b-12d3-a456-426614174000"), None),
    # convert
    ("conv.units", "conv.units", T("10 km"), None),
    ("conv.units-temp", "conv.units", T("100 C", group="temperature"), None),
    ("conv.base", "conv.base", T("255"), "ff"),
    ("conv.time", "conv.time", T("1700000000"), "2023"),
    ("conv.tz", "conv.tz", T("2024-05-01 12:00", **{"from": "UTC"}), None),
    ("conv.color", "conv.color", T("#6366f1"), None),
    ("conv.color-name", "conv.color", T("rgb(255, 0, 0)"), None),
    ("conv.roman", "conv.roman", T("2024"), "MMXXIV"),
    ("conv.roman-back", "conv.roman", T("MMXXIV"), "2024"),
    ("conv.date", "conv.date", T2("2024-01-01", "2024-12-31"), None),
    ("conv.date-add", "conv.date", T2("2024-01-31", "", add=30), None),
    ("conv.css", "conv.css", T("24px"), None),
    ("conv.aspect", "conv.aspect", T("1920x1080"), "16:9"),
    ("conv.percent", "conv.percent", N(mode="of", x=20, y=1000), "200"),
    ("conv.percent-change", "conv.percent", N(mode="change", x=80, y=100), None),
    # generators
    ("gen.uuid", "gen.uuid", N(count=3), None),
    ("gen.uuid-v7", "gen.uuid", N(kind="v7"), None),
    ("gen.uuid-ulid", "gen.uuid", N(kind="ulid"), None),
    ("gen.token", "gen.token", N(), None),
    ("gen.password", "gen.password", N(length=24, count=3), None),
    ("gen.passphrase", "gen.passphrase", N(dict="ru"), None),
    ("gen.lorem", "gen.lorem", N(), None),
    ("gen.lorem-ru", "gen.lorem", N(lang="ru", unit="sent", count=4), None),
    ("gen.fake", "gen.fake", N(count=5), None),
    ("gen.fake-json", "gen.fake", N(lang="en", format="json", count=3), None),
    ("gen.placeholder", "gen.placeholder", N(text="Hello"), "#files"),
    ("gen.placeholder-svg", "gen.placeholder", N(format="svg"), "#files"),
    ("gen.identicon", "gen.identicon", T("memu"), "#files"),
    ("gen.identicon-svg", "gen.identicon", T("memu", format="svg"), "#files"),
    ("gen.qr", "gen.qr", T("https://example.com"), "#files"),
    ("gen.qr-wifi-svg", "gen.qr", T("", kind="wifi", ssid="Home", wifi_pass="secret123", format="svg"), "#files"),
    ("gen.barcode", "gen.barcode", T("MEMU-123"), "#files"),
    ("gen.barcode-ean13", "gen.barcode", T("4006381333931", type="ean13"), "#files"),
    ("gen.barcode-ean8-svg", "gen.barcode", T("9638507", type="ean8", format="svg"), "#files"),
    ("gen.barcode-code39", "gen.barcode", T("MEMU 39", type="code39"), "#files"),
    ("gen.random", "gen.random", T("", count=5), None),
    ("gen.random-pick", "gen.random", T("Анна\nБорис\nВера", mode="pick"), None),
    ("gen.random-teams", "gen.random", T("a\nb\nc\nd\ne\nf", mode="teams", count=2), None),
    ("gen.palette", "gen.palette", T("#6366f1"), None),
    ("gen.gradient", "gen.gradient", T("#6366f1, #ec4899"), "#files"),
    ("gen.gradient-conic", "gen.gradient", T("red, blue, lime", kind="conic"), "#files"),
    ("gen.dummy-png", "gen.dummy", N(type="png", size=0.2), "#files"),
    ("gen.dummy-pdf", "gen.dummy", N(type="pdf", size=0.1), "#files"),
    ("gen.dummy-docx", "gen.dummy", N(type="docx", size=0.1), "#files"),
    ("gen.dummy-xlsx", "gen.dummy", N(type="xlsx", size=0.1), "#files"),
    ("gen.dummy-zip", "gen.dummy", N(type="zip", size=0.1), "#files"),
    ("gen.dummy-wav", "gen.dummy", N(type="wav", size=0.2), "#files"),
    ("gen.dummy-json", "gen.dummy", N(type="json", size=0.05), "#files"),
    ("gen.meta", "gen.meta", N(title="MemuAI", desc="Утилиты", image="https://example.com/og.png"), "og:title"),
    # images
    ("image.convert-webp", "image.convert", F("png", "jpg"), "#files"),
    ("image.convert-ico", "image.convert", F("png", format="ico"), "#files"),
    ("image.convert-avif", "image.convert", F("jpg", format="avif"), "#files"),
    ("image.convert-jpg-alpha", "image.convert", F("png", format="jpg"), "#files"),
    ("image.convert-tiff", "image.convert", F("jpg", format="tiff"), "#files"),
    ("image.resize", "image.resize", F("jpg", width=200, height=200), "#files"),
    ("image.resize-percent", "image.resize", F("png", mode="percent", percent=50, format="webp"), "#files"),
    ("image.resize-cover", "image.resize", F("png", mode="cover", width=100, height=100), "#files"),
    ("image.crop", "image.crop", F("jpg"), "#files"),
    ("image.crop-box", "image.crop", F("png", mode="box", x=10, y=10, w=100, h=50), "#files"),
    ("image.rotate", "image.rotate", F("png"), "#files"),
    ("image.rotate-angle", "image.rotate", F("jpg", op="angle", angle=15), "#files"),
    ("image.rotate-exif", "image.rotate", F("jpg", op="exif"), "#files"),
    ("image.compress", "image.compress", F("jpg", "png"), "#files"),
    ("image.compress-target", "image.compress", F("jpg", target=20), "#files"),
    ("image.strip", "image.strip", F("jpg"), "#files"),
    ("image.info", "image.info", F("jpg"), "MemuCam"),
    ("image.filters-gray", "image.filters", F("png"), "#files"),
    ("image.filters-pixelate", "image.filters", F("jpg", filter="pixelate", amount=8), "#files"),
    ("image.filters-sepia", "image.filters", F("jpg", filter="sepia"), "#files"),
    ("image.watermark", "image.watermark", F("jpg"), "#files"),
    ("image.watermark-tile", "image.watermark", F("png", pos="tile"), "#files"),
    ("image.watermark-logo", "image.watermark", F("jpg", "logo", logo=True), "#files"),
    ("image.frame", "image.frame", F("jpg", border=6, shadow=True, pad=10), "#files"),
    ("image.trim", "image.trim", F("png"), "#files"),
    ("image.favicon", "image.favicon", F("png"), "#files"),
    ("image.colors", "image.colors", F("jpg"), None),
    ("image.collage", "image.collage", F("png", "jpg", "png2"), "#files"),
    ("image.gif", "image.gif", F("png", "png2", "jpg"), "#files"),
    ("image.gif-webp", "image.gif", F("png", "png2", format="webp", bounce=True), "#files"),
    ("image.frames", "image.frames", F("gif"), "#files"),
    ("image.ascii", "image.ascii", F("jpg", width=60), None),
    ("image.transparent", "image.transparent", F("jpg"), "#files"),
    ("image.grid", "image.grid", F("jpg", cols=3, rows=2), "#files"),
    ("image.avatar", "image.avatar", F("jpg", ring=8), "#files"),
    ("image.avatar-squircle", "image.avatar", F("png", shape="squircle"), "#files"),
    ("image.compare", "image.compare", F("png", "png2"), "#files"),
    ("image.meme", "image.meme", F("jpg", top="Когда тесты", bottom="прошли"), "#files"),
    ("image.to_pdf", "image.to_pdf", F("jpg", "png"), "#files"),
    ("image.to_pdf-fit", "image.to_pdf", F("png", page="fit"), "#files"),
    # audio
    ("audio.convert-mp3", "audio.convert", F("wav"), "#files"),
    ("audio.convert-flac", "audio.convert", F("mp3", format="flac"), "#files"),
    ("audio.convert-m4a", "audio.convert", F("wav", format="m4a", channels="1", rate="22050"), "#files"),
    ("audio.convert-opus", "audio.convert", F("wav", format="opus"), "#files"),
    ("audio.convert-ogg", "audio.convert", F("wav", format="ogg"), "#files"),
    ("audio.convert-wav", "audio.convert", F("mp3", format="wav"), "#files"),
    ("audio.convert-alac", "audio.convert", F("wav", format="alac"), "#files"),
    ("audio.convert-ac3", "audio.convert", F("wav", format="ac3"), "#files"),
    ("audio.convert-wma", "audio.convert", F("wav", format="wma"), "#files"),
    ("audio.extract", "audio.extract", F("mp4"), "#files"),
    ("audio.trim", "audio.trim", F("wav", start="0:00.5", end="0:02", fadein=0.2, fadeout=0.3), "#files"),
    ("audio.volume", "audio.volume", F("wav"), "#files"),
    ("audio.volume-gain", "audio.volume", F("mp3", mode="gain", db=-6, format="wav"), "#files"),
    ("audio.speed", "audio.speed", F("wav"), "#files"),
    ("audio.speed-pitch", "audio.speed", F("wav", speed=0.8, pitch=True), "#files"),
    ("audio.reverse", "audio.reverse", F("mp3"), "#files"),
    ("audio.silence", "audio.silence", F("wav"), "#files"),
    ("audio.merge", "audio.merge", F("wav", "mp3"), "#files"),
    ("audio.merge-cross", "audio.merge", F("wav", "wav2", cross=0.3, format="wav"), "#files"),
    ("audio.split", "audio.split", F("wav", minutes=0.02), "#files"),
    ("audio.info", "audio.info", F("mp3"), None),
    ("audio.tags", "audio.tags", F("mp3", "png", title="Песня", artist="Memu"), "#files"),
    ("audio.waveform", "audio.waveform", F("wav"), "#files"),
    ("audio.waveform-wave", "audio.waveform", F("mp3", style="wave"), "#files"),
    ("audio.tone", "audio.tone", N(seconds=1), "#files"),
    ("audio.tone-dtmf", "audio.tone", N(wave="dtmf", format="mp3"), "#files"),
    ("audio.tone-sweep", "audio.tone", N(wave="sweep", seconds=1, format="flac"), "#files"),
    ("audio.tone-pink", "audio.tone", N(wave="pink", seconds=1, format="ogg"), "#files"),
    # video
    ("video.convert-webm", "video.convert", F("mp4", format="webm"), "#files"),
    ("video.convert-mkv", "video.convert", F("mp4", format="mkv", height="0"), "#files"),
    ("video.convert-gif", "video.convert", F("gif"), "#files"),
    ("video.trim", "video.trim", F("mp4", start="0:01", end="0:02"), "#files"),
    ("video.trim-exact", "video.trim", F("mp4", start="0:00.5", end="0:02", fast=False), "#files"),
    ("video.mute", "video.mute", F("mp4"), "#files"),
    ("video.gif", "video.gif", F("mp4", seconds=1.5, fps=8, width=200), "#files"),
    ("video.gif-webp", "video.gif", F("mp4", seconds=1, format="webp"), "#files"),
    ("video.frames", "video.frames", F("mp4"), "#files"),
    ("video.frames-every", "video.frames", F("mp4", mode="every", step=1), "#files"),
    ("video.frames-sheet", "video.frames", F("mp4", mode="sheet"), "#files"),
    ("video.info", "video.info", F("mp4"), None),
    ("video.compress", "video.compress", F("mp4", mb=0.2, height="0"), "#files"),
    ("video.rotate", "video.rotate", F("mp4"), "#files"),
    ("video.rotate-hflip", "video.rotate", F("mp4", op="hflip"), "#files"),
    ("video.slideshow", "video.slideshow", F("png", "jpg", "wav", seconds=1, fade=0.3), "#files"),
    ("video.audio", "video.audio", F("mp4", "wav2"), "#files"),
    # pdf
    ("pdf.merge", "pdf.merge", F("pdf", "png", "pdf"), "#files"),
    ("pdf.split", "pdf.split", F("pdf"), "#files"),
    ("pdf.split-each-zip", "pdf.split", F("pdf", mode="each", zip=True), "#files"),
    ("pdf.pages-delete", "pdf.pages", F("pdf", action="delete", pages="2"), "#files"),
    ("pdf.pages-order", "pdf.pages", F("pdf", action="order", pages="3,1,2"), "#files"),
    ("pdf.pages-reverse", "pdf.pages", F("pdf", action="reverse"), "#files"),
    ("pdf.rotate", "pdf.rotate", F("pdf", pages="1"), "#files"),
    ("pdf.text", "pdf.text", F("pdf"), "MemuUtils"),
    ("pdf.text-md-file", "pdf.text", F("pdf", fmt="markdown", file=True), "#files"),
    ("pdf.images", "pdf.images", F("pdf", min=16), None),
    ("pdf.protect", "pdf.protect", F("pdf", password="1234", print=False), "#files"),
    ("pdf.info", "pdf.info", F("pdf"), None),
    ("pdf.meta", "pdf.meta", F("pdf", title="Отчёт", author="Memu"), "#files"),
    ("pdf.meta-clear", "pdf.meta", F("pdf", mode="clear"), "#files"),
    ("pdf.watermark", "pdf.watermark", F("pdf"), "#files"),
    ("pdf.watermark-tile", "pdf.watermark", F("pdf", layout="tile", text="DRAFT", pages="2-3"), "#files"),
    ("pdf.numbers", "pdf.numbers", F("pdf"), "#files"),
    ("pdf.numbers-outer", "pdf.numbers", F("pdf", position="outer", skip=1, format="Стр. {n}"), "#files"),
    ("pdf.compress", "pdf.compress", F("pdf"), "Пережато картинок: 1"),
    ("pdf.compress-strong", "pdf.compress", F("pdf", level="strong", strip=True), "#files"),
    ("pdf.crop", "pdf.crop", F("pdf", top=20, bottom=20), "#files"),
    # documents
    ("doc.convert-docx-md", "doc.convert", F("docx", to="md"), "#files"),
    ("doc.convert-docx-pdf", "doc.convert", F("docx", to="pdf"), "#files"),
    ("doc.convert-docx-odt", "doc.convert", F("docx", to="odt"), "#files"),
    ("doc.convert-docx-rtf", "doc.convert", F("docx", to="rtf"), "#files"),
    ("doc.convert-md-docx", "doc.convert", F("md", to="docx"), "#files"),
    ("doc.convert-html-docx", "doc.convert", F("html", to="docx"), "#files"),
    ("doc.convert-epub-txt", "doc.convert", F("epub", to="txt"), "#files"),
    ("doc.convert-fb2-html", "doc.convert", F("fb2", to="html"), "#files"),
    ("doc.convert-pdf-docx", "doc.convert", F("pdf", to="docx"), "#files"),
    ("doc.text-docx", "doc.text", F("docx"), "жирным"),
    ("doc.text-pptx", "doc.text", F("pptx"), "Slide two"),
    ("doc.text-eml", "doc.text", F("eml"), "Привет"),
    ("doc.text-xlsx", "doc.text", F("xlsx"), "Кофе"),
    ("doc.text-epub", "doc.text", F("epub"), "утилиты"),
    ("doc.text-pdf", "doc.text", F("pdf"), "MemuUtils"),
    ("doc.encoding", "doc.encoding", F("txt1251"), "#files"),
    ("md.convert", "md.convert", T("# Hi\n\n**bold** and a [link](https://example.com)\n\n| a | b |\n|---|---|\n| 1 | 2 |"), "<h1"),
    ("md.convert-docx", "md.convert", T("# Заголовок\n\n- пункт\n- ещё", to="docx"), "#files"),
    ("md.convert-pdf", "md.convert", T("# PDF\n\nText **bold**", to="pdf"), "#files"),
    ("md.convert-page", "md.convert", T("# Page", to="page"), "#files"),
    ("md.convert-odt", "md.convert", T("# ODT\n\ntext", to="odt"), "#files"),
    ("md.convert-rtf", "md.convert", T("# RTF\n\nтекст", to="rtf"), "#files"),
    ("html.md", "html.md", T("<h1>Title</h1><p>Text <b>bold</b> <a href='https://x.y'>link</a></p><ul><li>a</li></ul>"), "# Title"),
    ("html.md-text", "html.md", T("<p>Hello<br>world</p>", to="text"), "Hello"),
    ("eml.read", "eml.read", F("eml"), "Отчёт за май"),
    # data
    ("data.convert-xlsx", "data.convert", F("csv"), "#files"),
    ("data.convert-json", "data.convert", F("csv", to="json"), "#files"),
    ("data.convert-yaml", "data.convert", F("csv", to="yaml"), "#files"),
    ("data.convert-xml", "data.convert", F("csv", to="xml"), "#files"),
    ("data.convert-sql", "data.convert", F("csv", to="sql"), "#files"),
    ("data.convert-parquet", "data.convert", F("csv", to="parquet"), "#files"),
    ("data.convert-xlsx-csv", "data.convert", F("xlsx", to="csv"), "#files"),
    ("data.convert-json-csv", "data.convert", F("json", to="csv"), "#files"),
    ("data.convert-text-md", "data.convert", T("a;b\n1;2\n3;4", to="md"), None),
    ("data.convert-html-table", "data.convert", F("html", to="csv"), "#files"),
    ("data.sql", "data.sql", F("csv", query="SELECT name, SUM(amount) AS total FROM t1 GROUP BY name ORDER BY total DESC"), None),
    ("data.sql-xlsx", "data.sql", F("csv", "xlsx", query="SELECT COUNT(*) AS n FROM t2", to="xlsx"), "#files"),
    ("data.sql-noext", "data.sql", F("csv", query="SELECT * FROM read_csv_auto('/etc/passwd')"), "!error"),
    ("data.query", "data.query", F("json", path="$..city"), "Москва"),
    ("data.query-text", "data.query", T('{"a":{"b":[{"c":1},{"c":2}]}}', path="$.a.b[*].c"), None),
    ("data.clean", "data.clean", F("csv"), "#files"),
    ("data.merge-stack", "data.merge", F("csv", "csv"), "#files"),
    ("data.merge-join", "data.merge", F("csv", "csv2", mode="join", key="id"), "#files"),
    ("data.split-rows", "data.split", F("csv", n=2), "#files"),
    ("data.split-column", "data.split", F("csv", mode="column", column="city", zip=True), "#files"),
    ("data.stats", "data.stats", F("csv"), None),
    ("data.pivot", "data.pivot", F("csv", rows="name", cols="city", value="amount"), None),
    ("data.filter", "data.filter", F("csv", where="city", value="Москва"), None),
    ("data.filter-gt", "data.filter", F("xlsx", where="price", op=">", value="200", to="csv"), "#files"),
    ("data.flatten", "data.flatten", F("json"), None),
    ("data.flatten-nest", "data.flatten", T('{"a.b": 1, "a.c": 2, "d": 3}', mode="nest"), None),
    ("data.json_diff", "data.json_diff", T2('{"a":1,"b":[1,2],"c":{"d":1}}', '{"a":2,"b":[1,2,3],"c":{"e":1}}'), None),
    # network (only with MEMU_TEST_NET=1) — subnet is offline
    ("net.subnet", "net.subnet", T("192.168.1.0/24", split=26), "192.168.1.64"),
    ("net.dns", "net.dns", T("example.com", type="A"), "#net"),
    ("net.dns-all", "net.dns", T("gmail.com"), "#net"),
    ("net.ip", "net.ip", T("8.8.8.8"), "#net"),
    ("net.http", "net.http", T("http://example.com"), "#net"),
    ("net.ssl", "net.ssl", T("example.com"), "#net"),
    ("net.whois", "net.whois", T("example.com"), "#net"),
    ("net.ping", "net.ping", T("1.1.1.1", count=2), "#net"),
    ("net.ports", "net.ports", T("127.0.0.1", ports="22,80,1"), None),
    ("net.currency", "net.currency", T("100 USD RUB"), "#net"),
    ("net.wol", "net.wol", T("00:11:22:33:44:55", broadcast="127.0.0.1"), None),
    # system (safe modes)
    ("sys.info", "sys.info", N(), None),
    ("sys.disks", "sys.disks", N(), None),
    ("sys.processes", "sys.processes", N(), None),
    ("sys.listening", "sys.listening", N(), None),
    ("sys.hosts", "sys.hosts", N(), None),
    ("sys.env", "sys.env", T("PATH"), None),
    ("sys.flushdns", "sys.flushdns", N(), "#any"),
    ("sys.battery", "sys.battery", N(), "#any"),
    ("sys.wifi", "sys.wifi", N(), "#any"),
    ("sys.tempclean", "sys.tempclean", N(), None),
    ("sys.startup", "sys.startup", N(), "#win"),
    ("sys.installed", "sys.installed", T(""), "#win"),
    ("sys.tweaks", "sys.tweaks", N(), "#win"),
    ("sys.explorer", "sys.explorer", N(), "#win"),
]

AI_UTILS = ["ai.ocr", "ai.regex", "ai.sql", "ai.explain", "ai.rewrite", "ai.extract"]

# ── fixtures ─────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module", autouse=True)
def samples(tmp_path_factory):
    data = tmp_path_factory.mktemp("memu-utils-data")
    old = os.environ.get("MEMUAI_DATA_DIR")
    os.environ["MEMUAI_DATA_DIR"] = str(data)
    SAMPLES.update(make_samples(tmp_path_factory.mktemp("samples")))
    yield SAMPLES
    if old is None:
        os.environ.pop("MEMUAI_DATA_DIR", None)
    else:
        os.environ["MEMUAI_DATA_DIR"] = old

def run(util_id, **kw):
    import app.utils_kit as uk
    return asyncio.run(uk.run_async(util_id, **kw))

def _result_text(res) -> str:
    r = res.get("result") or {}
    parts = [r.get("text") or "", r.get("note") or ""]
    for a, b in r.get("info") or []:
        parts.append(f"{a}: {b}")
    table = r.get("table") or {}
    for row in [table.get("columns") or []] + (table.get("rows") or [])[:50]:
        parts.append(" | ".join(str(x) for x in row))
    parts += [o["name"] for o in res.get("outputs") or []]
    return "\n".join(parts)

def do_case(util_id, kwargs, expect):
    import app.utils_kit as uk
    if callable(kwargs):
        kwargs = kwargs()
    kw = dict(kwargs)
    if callable(kw.get("files")):
        kw["files"] = kw["files"]()
    if expect == "#net" and not NET:
        pytest.skip("network utilities run with MEMU_TEST_NET=1")
    res = run(util_id, **kw)
    if expect == "#win" and sys.platform != "win32":
        assert not res["ok"] and "Windows" in res["error"], res
        return res
    if expect == "#any":
        assert res.get("ok") or res.get("error"), res
        return res
    if expect == "!error":
        assert not res["ok"], res
        return res
    assert res.get("ok"), f"{util_id}: {res.get('error')}"
    text = _result_text(res)
    assert text.strip() or res["outputs"], f"{util_id}: empty result"
    if expect == "#files":
        assert res["outputs"], f"{util_id}: no output files"
    elif isinstance(expect, str) and not expect.startswith("#"):
        assert expect.lower() in text.lower(), f"{util_id}: {expect!r} not in {text[:600]!r}"
    for out in res["outputs"]:
        path = uk.resolve_ref(out["ref"])
        check_output(path)
    return res

@pytest.mark.parametrize("case_id,util_id,kwargs,expect", CASES, ids=[c[0] for c in CASES])
def test_utility(case_id, util_id, kwargs, expect):
    do_case(util_id, kwargs, expect)

# ── chains and special cases ─────────────────────────────────────────────────────────────────────────────────────────
def test_every_utility_has_a_case():
    import app.utils_kit as uk
    covered = {c[1] for c in CASES} | set(AI_UTILS) | {"pdf.unlock", "sys.bigfiles", "sys.foldersize", "sys.duplicates", "sys.empty_folders",
                                                       "sys.rename", "sys.kill", "sys.power", "sys.clicker", "sys.autotype", "sys.keepawake",
                                                       "enc.compress", "net.lan", "net.speed", "video.download"}
    missing = sorted(set(uk.REGISTRY) - covered)
    assert not missing, missing
    assert len(uk.REGISTRY) >= 150

def test_pdf_protect_unlock_chain():
    import app.utils_kit as uk
    res = do_case("pdf.protect", F("pdf", password="1234"), "#files")
    ref = res["outputs"][0]["ref"]
    from pypdf import PdfReader
    assert PdfReader(str(uk.resolve_ref(ref))).is_encrypted
    bad = run("pdf.unlock", files=[{"ref": ref}], opts={"password": "0000"})
    assert not bad["ok"]
    good = run("pdf.unlock", files=[{"ref": ref}], opts={"password": "1234"})
    assert good["ok"], good
    reader = PdfReader(str(uk.resolve_ref(good["outputs"][0]["ref"])))
    assert not reader.is_encrypted and len(reader.pages) == 3

def test_compress_roundtrip():
    enc = run("enc.compress", text="hello " * 50, opts={"alg": "xz"})
    assert enc["ok"], enc
    dec = run("enc.compress", text=enc["result"]["text"].strip(), opts={"alg": "xz", "dir": "decode"})
    assert dec["ok"] and dec["result"]["text"].strip() == ("hello " * 50).strip(), dec

def test_folder_tools(tmp_path):
    root = tmp_path / "folder"
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "empty1").mkdir()
    (root / "empty2" / "inner").mkdir(parents=True)
    big = os.urandom(300_000)
    (root / "a.bin").write_bytes(big)
    (root / "sub" / "copy of a.bin").write_bytes(big)
    (root / "sub" / "deep" / "Фото 1.JPG").write_bytes(b"x" * 1000)
    (root / "sub" / "deep" / "photo 2.jpg").write_bytes(b"y" * 1000)
    res = run("sys.bigfiles", text=str(root), opts={"min": 0})
    assert res["ok"] and "a.bin" in _result_text(res), res
    res = run("sys.foldersize", text=str(root))
    assert res["ok"] and "sub" in _result_text(res), res
    res = run("sys.duplicates", text=str(root), opts={"min": 1})
    assert res["ok"] and "copy of a.bin" in _result_text(res), res
    res = run("sys.empty_folders", text=str(root))
    assert res["ok"] and (root / "empty1").exists(), res
    assert "empty1" in _result_text(res)
    res = run("sys.empty_folders", text=str(root), opts={"delete": True})
    assert res["ok"] and not (root / "empty1").exists() and not (root / "empty2").exists() and (root / "sub").exists(), res
    target = root / "sub" / "deep"
    res = run("sys.rename", text=str(target), opts={"pattern": "img_{n}", "case": "lower"})
    assert res["ok"] and (target / "photo 2.jpg").exists(), res
    res = run("sys.rename", text=str(target), opts={"pattern": "img_{n}", "case": "lower", "apply": True})
    assert res["ok"], res
    names = sorted(p.name for p in target.iterdir())
    assert all(n.startswith("img_") for n in names), names

def test_kill_and_power_jobs():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        res = run("sys.kill", text=str(proc.pid))
        assert res["ok"], res
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    import app.utils_kit as uk
    res = run("sys.power", opts={"action": "sleep", "minutes": 600})
    assert res["ok"], res
    assert any(j["kind"] == "power" for j in uk.jobs.listing(False))
    res = run("sys.power", opts={"action": "cancel"})
    assert res["ok"], res
    time.sleep(0.3)
    assert not any(j["kind"] == "power" and j.get("running", True) for j in uk.jobs.listing(False))

def test_ai_utils_are_window_only():
    import app.utils_kit as uk
    for util_id in AI_UTILS:
        util = uk.REGISTRY[util_id]
        assert util.cat == "ai" and not util.ai and util.prompt and util.prompt_en, util_id
        res = run(util_id, text="x", files=f("png") if util.input == "files" else None)
        assert not res["ok"]

def test_catalog_skill_and_settings():
    import app.utils_kit as uk
    cat = uk.catalog("ru")
    assert cat["total"] == len(uk.REGISTRY) and cat["categories"]
    ids = {u["id"] for u in cat["utils"]}
    assert "image.convert" in ids and "pdf.merge" in ids
    row = next(u for u in cat["utils"] if u["id"] == "image.convert")
    assert row["title"] and row["opts"] and row["tokens"] > 0
    en = uk.catalog("en")
    assert next(u for u in en["utils"] if u["id"] == "image.convert")["title"] != row["title"]
    assert uk.ai_tools() == [] and "no utilities are allowed" in uk.skill_text("en")  # nothing out of the box
    assert not any(u["ai_on"] for u in cat["utils"]) and cat["skill"]["tools"] == 0
    assert "image.convert" in cat["recommended"] and "pdf.info" in cat["recommended"]
    uk.save_settings({"ai_tools": uk.recommended_tools()})
    skill = uk.skill_text("en")
    assert "image.convert" in skill and "ai.ocr" not in skill and "sys.clicker" not in skill
    full = uk.estimate_tokens(skill)
    uk.save_settings({"ai_tools": ["image.convert", "pdf.merge", "sys.clicker"]})
    assert uk.ai_tools() == ["image.convert", "pdf.merge"]
    small = uk.estimate_tokens(uk.skill_text("en"))
    assert small < full / 5
    res = run("pdf.info", files=f("pdf"), source="chat")
    assert not res["ok"] and "MemuUtils" in res["error"]
    res = run("image.convert", files=f("png"), source="chat")
    assert res["ok"], res
    uk.save_settings({"ai_tools": None})
    assert uk.ai_tools() == []
    assert "pdf.info" in uk.recommended_tools()
    help_text = uk.tool_help("image.resize", "ru")
    assert "width" in help_text

def test_chat_dangerous_needs_confirmation(tmp_path):
    import app.utils_kit as uk
    (tmp_path / "e1").mkdir()
    res = run("sys.empty_folders", text=str(tmp_path), source="chat")
    assert not res["ok"] and "MemuUtils" in res["error"], res  # not in the default AI list
    uk.save_settings({"ai_tools": ["sys.empty_folders", "sys.clicker"]})
    res = run("sys.empty_folders", text=str(tmp_path), opts={"delete": True}, source="chat", confirmed=False)
    assert res.get("needs_confirm") and (tmp_path / "e1").exists(), res
    res = run("sys.empty_folders", text=str(tmp_path), opts={"delete": False}, source="chat", confirmed=False)
    assert res["ok"], res
    res = run("sys.clicker", source="chat")
    assert not res["ok"]
    uk.save_settings({"ai_enabled": False})
    res = run("sys.empty_folders", text=str(tmp_path), source="chat")
    assert not res["ok"], res
    uk.save_settings({"ai_enabled": True, "ai_tools": None})

def test_chunked_upload_and_refs():
    import app.utils_kit as uk
    data = SAMPLES["jpg"].read_bytes()
    up = uk.upload_start("photo.jpg", len(data))["upload"]
    half = len(data) // 2
    uk.upload_chunk(up, 0, base64.b64encode(data[:half]).decode())
    with pytest.raises(uk.UtilError):
        uk.upload_chunk(up, 0, base64.b64encode(data[half:]).decode())
    uk.upload_chunk(up, half, base64.b64encode(data[half:]).decode())
    done = uk.upload_done(up)
    assert done["size"] == len(data)
    res = run("image.info", files=[{"ref": done["ref"]}])
    assert res["ok"] and "640" in _result_text(res), res
    res = run("image.resize", files=[{"ref": done["ref"]}], opts={"width": 100, "height": 100})
    out = res["outputs"][0]
    again = run("image.info", files=[{"ref": out["ref"]}])
    assert again["ok"], again
    for bad in ("out:../../etc/passwd", "out:20240101-000000-abcdef/../../x", "up:../../etc", "/etc/passwd", "file:///etc/passwd"):
        with pytest.raises(uk.UtilError):
            uk.resolve_ref(bad)
    data_url = uk.output_data(res["run"], out["name"])
    assert data_url["data"].startswith("data:image/")
    saved = uk.save_output(res["run"], out["name"], str(SAMPLES["png"].parent / "saved"))
    assert Path(saved["path"]).is_file()

if __name__ == "__main__":
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="memu-utils-"))
    os.environ.setdefault("MEMUAI_DATA_DIR", str(tmp / "data"))
    SAMPLES.update(make_samples(tmp / "samples"))
    only = sys.argv[1:]
    bad = 0
    for case_id, util_id, kwargs, expect in CASES:
        if only and not any(case_id.startswith(o) for o in only):
            continue
        t0 = time.perf_counter()
        try:
            res = do_case(util_id, kwargs, expect)
            print(f"ok   {case_id:28s} {int((time.perf_counter() - t0) * 1000):6d} ms  {(_result_text(res) or '')[:110]!r}")
        except BaseException as exc:  # noqa: BLE001
            if type(exc).__name__ == "Skipped":
                print(f"skip {case_id}")
                continue
            bad += 1
            print(f"FAIL {case_id:28s} {type(exc).__name__}: {str(exc)[:400]}")
    print("failed:", bad)
