# -*- coding: utf-8 -*-
"""Round 37: MemuMusic backend — a local music library with a smart downloader.

* :mod:`app.music.library`    — SQLite library in ``MEMUAI_DATA_DIR/music`` (tracks, playlists,
  likes, play history, stats, artists/albums, cover accent colour), local folder import.
* :mod:`app.music.sources`    — strict source detection by link and metadata of the services
  yt-dlp cannot download (Spotify, Apple Music, Deezer, Yandex Music, Tidal, Amazon Music).
* :mod:`app.music.tools`      — yt-dlp (python module or a binary fetched on demand) and ffmpeg.
* :mod:`app.music.downloader` — the job queue: download / import, progress, cancel, retry, dedup.
* :mod:`app.music.lyrics`     — LRCLIB synced and plain lyrics with a cache.
* :mod:`app.music.player`     — the host player state and remote commands (Mini App remote).
* :mod:`app.music.api`        — ``call(op, args)`` for the module bridge / Mini App and the REST routes.
"""
from __future__ import annotations
