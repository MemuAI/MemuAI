"""Round 37: a small offline MemuMusic library for the Mini App tests and screenshots (no network).

``seed(n)`` adds tracks with generated covers (Pillow) and short WAV files to the library of
``MEMUAI_DATA_DIR/music``, likes a few, makes a playlist and stores synced lyrics for the first track.
``fake_window()`` makes ``app.music.tgapp_ops`` treat this process as the MemuAI window (the player state and
commands then live in this process's ``player`` hub), so the remote / handoff paths run without a second process.
"""
from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path
from unittest import mock

TRACKS = [
    ("Северное сияние", "Лунный кот", "Полярная ночь", (255, 94, 135), (88, 60, 220)),
    ("Midnight Drive", "Neon Harbor", "Afterglow", (255, 160, 60), (220, 40, 90)),
    ("Ocean Eyes", "Blue Lagoon", "Tides", (40, 190, 220), (30, 60, 160)),
    ("Golden Hour", "Sunset Club", "Warm", (250, 200, 80), (230, 90, 40)),
    ("Forest Rain", "Moss & Fern", "Green", (90, 200, 120), (20, 90, 70)),
    ("Velvet Sky", "Aurora", "Pastel Dreams", (190, 140, 255), (255, 140, 200)),
    ("Ночной город", "Огни", "Метро", (120, 130, 255), (20, 20, 60)),
    ("Lo-fi Morning", "Kettle Beats", "Coffee", (210, 160, 120), (90, 60, 50)),
]
LYRICS = [[0.0, "Над городом тихо гаснут огни"], [3.0, "Северное сияние зовёт"], [6.0, "Мы снова идём по льду"],
          [9.0, "И небо поёт для нас"], [12.0, "♪"], [14.0, "Северное сияние"]]


def cover_png(c1, c2, seed: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter
    size = 480
    img = Image.new("RGB", (size, size), c1)
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / size
        draw.line([(0, y), (size, y)], fill=tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)))
    for k in range(5):
        r = 60 + (seed * 37 + k * 53) % 140
        x = (seed * 71 + k * 113) % size
        y = (seed * 29 + k * 97) % size
        draw.ellipse([x - r, y - r, x + r, y + r], fill=tuple(min(255, int(c2[i] * .5 + 255 * .5 * (k % 2))) for i in range(3)))
    img = img.filter(ImageFilter.GaussianBlur(28))
    draw = ImageDraw.Draw(img)
    draw.ellipse([size * .3, size * .3, size * .7, size * .7], outline=(255, 255, 255), width=6)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=86)
    return out.getvalue()


def wav_bytes(seconds: float = 16.0, freq: float = 330.0) -> bytes:
    rate = 8000
    frames = bytearray()
    for i in range(int(rate * seconds)):
        value = int(9000 * math.sin(2 * math.pi * freq * i / rate) * (0.6 + 0.4 * math.sin(i / rate * 3)))
        frames += struct.pack("<h", value)
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return out.getvalue()


def seed(count: int = len(TRACKS)):
    """Adds the tracks once; returns (library, [track ids], playlist id)."""
    from app.music.library import get_library
    lib = get_library()
    existing = lib.list("", "added", "", 500, 0, "")
    if existing.get("total"):
        # the same order as a fresh seed (TRACKS first — the tests rely on ids[0] having lyrics), then anything added later
        by_title = {t["title"]: t["id"] for t in existing["tracks"]}
        ids = [by_title[row[0]] for row in TRACKS[:count] if row[0] in by_title]
        ids += [t["id"] for t in existing["tracks"] if t["id"] not in ids]
        pls = [p for p in lib.playlists("", "ru") if not p.get("smart")]
        return lib, ids, (pls[0]["id"] if pls else "")
    lib.tracks_dir.mkdir(parents=True, exist_ok=True)
    ids = []
    for n, (title, artist, album, c1, c2) in enumerate(TRACKS[:count]):
        path = Path(lib.tracks_dir) / f"r37-{n}.wav"
        path.write_bytes(wav_bytes(16.0, 220 + n * 40))
        row = lib.add({"file": str(path), "title": title, "artist": artist, "album": album, "duration": 16 + n * 31,
                       "source": ["youtube", "spotify", "ytmusic", "soundcloud"][n % 4], "added_at": 1_760_000_000 + n * 600},
                      cover_png(c1, c2, n))
        ids.append(row["id"])
    for tid in ids[:4]:
        lib.like(tid, True)
    for tid in ids[2:6]:
        lib.played(tid, 200)
    pl = lib.create_playlist("Вечер пятницы", ids[1:7])
    for tid in ids[:2]:
        lib.store_lyrics(tid, LYRICS, None, "lrclib")
    return lib, ids, pl["id"]


def fake_window():
    """This process plays the role of the MemuAI window for app.music.tgapp_ops (patches; call .start())."""
    from app.music import api, tgapp_ops

    def window_call(url, op, args, lang):
        return api.call(op, args, lang=lang, sender="phone")
    return [mock.patch.object(tgapp_ops, "window_url", return_value="http://127.0.0.1:1"),
            mock.patch.object(tgapp_ops, "_window_call", side_effect=window_call)]


def pc_playing(track_id: str, queue, position: float = 42.0, playing: bool = True):
    from app.music.library import get_library
    from app.music.player import hub
    return hub(get_library().root).update({"track_id": track_id, "queue": list(queue), "index": list(queue).index(track_id),
                                           "position": position, "playing": playing, "duration": 180, "volume": .7,
                                           "device": "pc", "device_name": "MemuAI"})


ALBUM = [("Afterglow", 1, "Neon Lights"), ("Afterglow", 3, "City Pulse"), ("Afterglow", 4, "Last Exit")]


def seed_artist():
    """Three more tracks of «Neon Harbor» on the album «Afterglow» (track numbers) for the artist / album screens."""
    from app.music.library import get_library
    lib = get_library()
    have = {t["title"] for t in lib.list("", "added", "", 500, 0, "")["tracks"]}
    for n, (album, no, title) in enumerate(ALBUM):
        if title in have:
            continue
        path = Path(lib.tracks_dir) / f"r37-album-{n}.wav"
        path.write_bytes(wav_bytes(12.0, 300 + n * 50))
        row = lib.add({"file": str(path), "title": title, "artist": "Neon Harbor", "album": album, "track_no": no,
                       "year": 2024, "duration": 150 + n * 20, "source": "spotify", "added_at": 1_759_000_000 + n},
                      cover_png((255, 160, 60), (220, 40, 90), 1))
        lib.played(row["id"], 120 + n * 60)
    for row in lib.list("Midnight Drive", "added", "", 5, 0, "")["tracks"]:
        lib.update(row["id"], {"track_no": 2, "year": 2024})
