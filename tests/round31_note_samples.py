"""Sample documents for tests/round31_note_core_test.py (and the note-core screenshots): tiny but real
XLSX / ODS / EPUB / FB2 / IPYNB files built with the stdlib only."""
from __future__ import annotations

import base64
import json
import zipfile
from pathlib import Path

PNG_1PX = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def xlsx(path: Path) -> Path:
    shared = ["Имя", "Город", "Сумма", "Дата", "Анна", "Рига", "Борис", "Вильнюс"]
    sst = "".join(f"<si><t>{s}</t></si>" for s in shared)
    sheet1 = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c></row>
<row r="2"><c r="A2" t="s"><v>4</v></c><c r="B2" t="s"><v>5</v></c><c r="C2"><v>1250.5</v></c><c r="D2" s="1"><v>45941</v></c></row>
<row r="3"><c r="A3" t="s"><v>6</v></c><c r="B3" t="s"><v>7</v></c><c r="C3"><v>300</v></c><c r="D3" s="1"><v>45942.5</v></c><c r="F3" t="b"><v>1</v></c></row>
<row r="5"><c r="A5" t="inlineStr"><is><t>Итого</t></is></c><c r="C5" t="str"><f>SUM(C2:C3)</f><v>1550.5</v></c></row>
</sheetData></worksheet>"""
    sheet2 = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>42</v></c></row></sheetData></worksheet>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Продажи" sheetId="1" r:id="rId1"/><sheet name="Second" sheetId="2" r:id="rId2"/></sheets></workbook>""")
        z.writestr("xl/_rels/workbook.xml.rels", """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="worksheet" Target="/xl/worksheets/sheet2.xml"/></Relationships>""")
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{sst}</sst>')
        z.writestr("xl/styles.xml", """<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>""")
        z.writestr("xl/worksheets/sheet1.xml", sheet1)
        z.writestr("xl/worksheets/sheet2.xml", sheet2)
    return path


def ods(path: Path) -> Path:
    content = """<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet>
<table:table table:name="Лист1"><table:table-row><table:table-cell office:value-type="string"><text:p>a</text:p></table:table-cell><table:table-cell table:number-columns-repeated="2" office:value-type="float" office:value="7"><text:p>7</text:p></table:table-cell></table:table-row>
<table:table-row table:number-rows-repeated="3"><table:table-cell/></table:table-row>
<table:table-row><table:table-cell office:value-type="date" office:date-value="2026-10-11"><text:p>11.10.2026</text:p></table:table-cell><table:table-cell office:value-type="boolean" office:boolean-value="true"/></table:table-row>
<table:table-row table:number-rows-repeated="1048000"><table:table-cell table:number-columns-repeated="1024"/></table:table-row>
</table:table></office:spreadsheet></office:body></office:document-content>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("content.xml", content)
    return path


def epub(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", """<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>""")
        z.writestr("OEBPS/content.opf", """<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Тестовая книга</dc:title><dc:creator>Автор Тестов</dc:creator></metadata>
<manifest><item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/><item id="img" href="img/dot.png" media-type="image/png"/></manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>""")
        z.writestr("OEBPS/text/ch1.xhtml", """<html xmlns="http://www.w3.org/1999/xhtml"><head><title>1</title><style>p{color:red}</style><script>alert(1)</script></head><body><h1>Глава первая</h1><p>Жили-были <b>дед</b> и <i>баба</i>.</p><p><img src="../img/dot.png" alt=""/></p><p onclick="evil()"><a href="javascript:alert(1)">bad link</a> <a href="https://example.com">good link</a></p></body></html>""")
        z.writestr("OEBPS/text/ch2.xhtml", """<html xmlns="http://www.w3.org/1999/xhtml"><body><h2>Chapter two</h2><p>The end.</p></body></html>""")
        z.writestr("OEBPS/img/dot.png", PNG_1PX)
    return path


def fb2(path: Path) -> Path:
    path.write_text("""<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">
<description><title-info><author><first-name>Лев</first-name><last-name>Толстой</last-name></author><book-title>Короткая повесть</book-title></title-info></description>
<body><section><title><p>Часть 1</p></title><p>Все счастливые семьи <emphasis>похожи</emphasis> друг на друга.</p><image l:href="#pic"/>
<section><title><p>Глава 1.1</p></title><p><strong>Текст</strong> главы.</p><empty-line/><poem><stanza><v>Строка стиха</v></stanza></poem></section></section>
<section><title><p>Часть 2</p></title><p>Конец.</p></section></body>
<binary id="pic" content-type="image/png">""" + base64.b64encode(PNG_1PX).decode() + """</binary></FictionBook>""", encoding="utf-8")
    return path


def ipynb(path: Path) -> Path:
    nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
          "cells": [{"cell_type": "markdown", "metadata": {}, "source": ["# Анализ\n", "Простой **блокнот**."]},
                    {"cell_type": "code", "execution_count": 1, "metadata": {}, "source": ["x = [1, 2, 3]\n", "print(sum(x))"],
                     "outputs": [{"output_type": "stream", "name": "stdout", "text": ["6\n"]}]},
                    {"cell_type": "code", "execution_count": 2, "metadata": {}, "source": "1/0",
                     "outputs": [{"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero", "traceback": ["\u001b[31mZeroDivisionError\u001b[0m"]}]},
                    {"cell_type": "code", "execution_count": 3, "metadata": {}, "source": "img",
                     "outputs": [{"output_type": "display_data", "data": {"image/png": base64.b64encode(PNG_1PX).decode(), "text/plain": ["<Figure>"]}, "metadata": {}}]}]}
    path.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def all_samples(folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "people.csv").write_text('name,city,note\nАнна,Рига,"привет, мир"\nБорис,Вильнюс,"скажи ""да"""\n', encoding="utf-8")
    (folder / "data.json").write_text(json.dumps({"name": "MemuNote", "version": 3.5, "tags": ["fast", "ai"], "nested": {"a": None, "b": True}}, ensure_ascii=False), encoding="utf-8")
    (folder / "config.xml").write_text('<?xml version="1.0"?><config><item key="a">1</item><item key="b"><sub/></item><!-- c --></config>', encoding="utf-8")
    return {"xlsx": xlsx(folder / "report.xlsx"), "ods": ods(folder / "sheet.ods"), "epub": epub(folder / "book.epub"),
            "fb2": fb2(folder / "povest.fb2"), "ipynb": ipynb(folder / "analysis.ipynb"), "csv": folder / "people.csv",
            "json": folder / "data.json", "xml": folder / "config.xml"}
