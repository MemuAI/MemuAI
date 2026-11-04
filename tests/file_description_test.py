"""Descriptive file search over actual images and local document contents."""
import os
import time
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import fsindex, fsindex_api
from app.file_description import parse_description
from app.memufiles.search import Query, walk_search


def test_both_search_paths_use_description(tmp_path, monkeypatch):
    root = tmp_path / "files"
    root.mkdir()
    Image.new("RGB", (120, 60), "black").save(root / "unnamed.png")
    Image.new("RGB", (90, 90), "white").save(root / "black-in-name.png")
    Image.new("RGBA", (90, 90), (0, 0, 0, 0)).save(root / "transparent.png")
    (root / "photo.png").write_bytes(b"broken image")
    (root / "note.txt").write_text("Итоговый\n\t  отчёт", encoding="utf-8")
    (root / "Project Video.txt").write_text("unrelated", encoding="utf-8")
    with zipfile.ZipFile(root / "notes.docx", "w") as archive:
        archive.writestr("word/document.xml", "<w:p><w:r><w:t>Pay</w:t></w:r><w:r><w:t>ment due</w:t></w:r></w:p>")
    video = root / "capture.mp4"
    with video.open("wb") as stream:
        stream.truncate(11 * 1024 * 1024)
    yesterday = time.time() - 86400
    os.utime(video, (yesterday, yesterday))

    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(fsindex, "_SERVICE", None)
    fsindex.save_settings({"roots": [str(root)], "live": False, "backend": "own", "mft": False})
    fsindex.rebuild(wait=True)
    server = FastAPI()
    server.include_router(fsindex_api.router)
    try:
        with TestClient(server) as client:
            for query, expected in [
                ("black image", {"unnamed.png"}),
                ("find me a black image", {"unnamed.png"}),
                ("black ext:png", {"unnamed.png"}),
                ("Project Video", {"Project Video.txt"}),
                ("чёрное горизонтальное изображение", {"unnamed.png"}),
                ("white square png", {"black-in-name.png"}),
                ("image 120x60", {"unnamed.png"}),
                ('content:"ИТОГОВЫЙ ОТЧЕТ"', {"note.txt"}),
                ("docx containing payment due", {"notes.docx"}),
                ("large videos from yesterday", {"capture.mp4"}),
                ("*.png", {"unnamed.png", "black-in-name.png", "transparent.png", "photo.png"}),
            ]:
                rows = walk_search(str(root), Query(query), lambda: False)
                assert {row["n"] for row in rows} == expected, query
                result = client.get("/api/fsindex/search", params={"q": query}).json()
                assert not result["error"], (query, result)
                assert {row["name"] for row in result["results"]} == expected, query
    finally:
        fsindex.shutdown()


def test_literal_and_combined_filters():
    assert parse_description('"black image" *.png').base == '"black image" *.png'
    assert not parse_description('"black image"').inspect
    description = parse_description("black png over 2 mb")
    assert description.color == "black"
    assert description.base == "ext:png size:>2mb"
