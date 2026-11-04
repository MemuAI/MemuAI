import asyncio
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import av

from app import utils_kit
from app.music import tools
from app.utils_kit import api, video_download


def test_video_download_saves_a_playable_artifact(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mp4") as output:
        stream = output.add_stream("mpeg4", rate=10)
        stream.width = stream.height = 32
        frame = av.VideoFrame(32, 32, "yuv420p")
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        for packet in stream.encode(frame) + stream.encode():
            output.mux(packet)
    video = buffer.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/clip.mp4")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(video)))
            self.end_headers()
            self.wfile.write(video)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{server.server_port}/redirect"
        monkeypatch.setattr(utils_kit, "runs_dir", lambda: tmp_path / "runs")
        monkeypatch.setattr(utils_kit, "uploads_dir", lambda: tmp_path / "uploads")
        monkeypatch.setattr(tools, "ffmpeg_path", lambda: None)
        monkeypatch.setattr(video_download, "_runtimes", dict)
        original = video_download._web_url
        monkeypatch.setattr(video_download, "_web_url",
                            lambda value: original(value) or urlsplit(value).netloc == urlsplit(url).netloc)
        try:
            denied = asyncio.run(utils_kit.run_async("video.download", text=url, lang="en", remember=False))
            assert "not public" in denied["error"]
            monkeypatch.setattr(video_download, "_public_address", lambda host: host == "127.0.0.1")
            result = asyncio.run(api.call_async("run", {"util": "video.download", "text": url, "live": True}, lang="en"))
            assert result["ok"], result
            artifact, = result["outputs"]
            assert artifact["kind"] == "video"
            assert utils_kit.output_path(result["run"], artifact["name"]).read_bytes() == video
            assert not (tmp_path / "runs" / result["run"] / "video").exists()
        finally:
            server.shutdown()
            worker.join()


def test_video_download_only_runs_in_memuutils():
    import pytest
    from app.utils_kit.core import UtilError

    denied = asyncio.run(utils_kit.run_async("video.download", source="chat", lang="en"))
    assert "only in the MemuUtils window" in denied["error"]
    with pytest.raises(UtilError, match="only in the MemuUtils window"):
        asyncio.run(api.call_async("run", {"util": "video.download"}, lang="en", trusted=False, owner="other-module"))
