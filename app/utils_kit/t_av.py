# -*- coding: utf-8 -*-
"""Audio and video utilities on PyAV (bundled FFmpeg): convert, trim, volume, speed, waveform, tags, GIF, frames…"""
from __future__ import annotations

import io
import math
import zipfile
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .core import O, UtilError, as_float, human_size, parse_time, table, utility

AUDIO_ACCEPT = "audio/*,.mp3,.wav,.ogg,.opus,.flac,.m4a,.aac,.wma,.amr,.aiff,.mka,video/*,.mp4,.mkv,.webm,.mov,.avi"
VIDEO_ACCEPT = "video/*,.mp4,.mkv,.webm,.mov,.avi,.m4v,.wmv,.flv,.3gp,.ts,.mts,.gif"

# name → (container, codec, extension, default bitrate, forced sample rate)
AUDIO_FORMATS = {"mp3": ("mp3", "libmp3lame", "mp3", 192000, None), "wav": ("wav", "pcm_s16le", "wav", 0, None),
                 "flac": ("flac", "flac", "flac", 0, None), "m4a": ("ipod", "aac", "m4a", 192000, None),
                 "ogg": ("ogg", "libopus", "ogg", 128000, 48000), "opus": ("ogg", "libopus", "opus", 128000, 48000),
                 "alac": ("ipod", "alac", "m4a", 0, None), "ac3": ("ac3", "ac3", "ac3", 192000, None), "wma": ("asf", "wmav2", "wma", 128000, None)}
AUDIO_CHOICES = [("mp3", "MP3", "MP3"), ("wav", "WAV", "WAV"), ("flac", "FLAC (без потерь)", "FLAC (lossless)"), ("m4a", "M4A (AAC)", "M4A (AAC)"),
                 ("ogg", "OGG (Opus)", "OGG (Opus)"), ("opus", "Opus", "Opus"), ("alac", "ALAC (Apple, без потерь)", "ALAC (Apple lossless)"),
                 ("ac3", "AC3", "AC3"), ("wma", "WMA", "WMA")]

def _av():
    try:
        import av
        return av
    except ImportError:
        raise UtilError("Компонент работы с аудио и видео (PyAV) недоступен", "The audio/video component (PyAV) is unavailable")

def _open(path) -> Any:
    av = _av()
    try:
        return av.open(str(path))
    except Exception as exc:  # noqa: BLE001
        raise UtilError(f"Файл не открывается как аудио/видео: {exc}", f"The file cannot be opened as audio/video: {exc}")

def _layout_name(n: int) -> str:
    return "mono" if n == 1 else "stereo"

def _channels(stream) -> int:
    try:
        return int(stream.codec_context.layout.nb_channels)
    except Exception:  # noqa: BLE001
        return int(getattr(stream.codec_context, "channels", 2) or 2)

def _duration(container, stream=None) -> float:
    if stream is not None and stream.duration and stream.time_base:
        return float(stream.duration * stream.time_base)
    return float(container.duration or 0) / 1_000_000

class AudioWriter:
    """Encodes float32 planar numpy blocks (channels × samples) to any of :data:`AUDIO_FORMATS`."""

    def __init__(self, path: Path, fmt: str, rate: int, channels: int, bitrate: int = 0, metadata: Dict[str, str] = None):
        av = _av()
        container, codec, _, default_br, forced = AUDIO_FORMATS[fmt]
        self.rate = forced or rate
        if codec == "ac3" and self.rate not in (32000, 44100, 48000):
            self.rate = 48000
        self.channels = channels
        self.layout = _layout_name(channels)
        self.out = av.open(str(path), "w", format=container)
        for k, v in (metadata or {}).items():
            self.out.metadata[k] = v
        self.stream = self.out.add_stream(codec, rate=self.rate, layout=self.layout)
        if bitrate or default_br:
            self.stream.bit_rate = bitrate or default_br
        self.samples = 0
        self.src_rate = rate
        self.resampler = av.AudioResampler(format="fltp", layout=self.layout, rate=self.rate) if self.rate != rate else None

    def write(self, block) -> None:
        import numpy as np
        av = _av()
        if block is None or block.shape[1] == 0:
            return
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(block, dtype=np.float32), format="fltp", layout=self.layout)
        frame.sample_rate = self.src_rate
        frames = self.resampler.resample(frame) if self.resampler else [frame]
        for fr in frames:
            fr.pts = self.samples
            fr.time_base = Fraction(1, self.rate)
            self.samples += fr.samples
            for packet in self.stream.encode(fr):
                self.out.mux(packet)

    def close(self) -> None:
        if self.resampler:
            for fr in self.resampler.resample(None):
                fr.pts = self.samples
                fr.time_base = Fraction(1, self.rate)
                self.samples += fr.samples
                for packet in self.stream.encode(fr):
                    self.out.mux(packet)
        for packet in self.stream.encode(None):
            self.out.mux(packet)
        self.out.close()

def read_audio(path, rate: Optional[int] = None, channels: Optional[int] = None, start: float = 0.0, end: Optional[float] = None,
               block_seconds: float = 2.0):
    """Yield ``(numpy fltp block, rate, channels)`` from the first audio stream, resampled, between ``start`` and ``end``."""
    av = _av()
    import numpy as np
    container = _open(path)
    try:
        if not container.streams.audio:
            raise UtilError("В файле нет звуковой дорожки", "The file has no audio track")
        ast = container.streams.audio[0]
        src_rate = ast.codec_context.sample_rate or 44100
        rate = rate or src_rate
        channels = channels or min(2, _channels(ast)) or 2
        resampler = av.AudioResampler(format="fltp", layout=_layout_name(channels), rate=rate)
        if start > 1:
            try:
                container.seek(int(max(0, start - 1) * 1_000_000), any_frame=False, backward=True)
            except Exception:  # noqa: BLE001
                pass
        pos = None
        pending: List[Any] = []
        pending_n = 0
        for frame in container.decode(ast):
            if pos is None:
                pos = float(frame.time) if frame.time is not None else 0.0
            for fr in resampler.resample(frame):
                arr = fr.to_ndarray().reshape(channels, -1)
                t0 = pos
                pos += arr.shape[1] / rate
                if pos <= start:
                    continue
                if t0 < start:
                    arr = arr[:, int((start - t0) * rate):]
                if end is not None and pos > end:
                    arr = arr[:, :max(0, int((end - max(t0, start)) * rate))]
                pending.append(arr)
                pending_n += arr.shape[1]
                if pending_n >= block_seconds * rate:
                    yield np.concatenate(pending, axis=1), rate, channels
                    pending, pending_n = [], 0
            if end is not None and pos is not None and pos >= end:
                break
        else:
            for fr in resampler.resample(None):
                pending.append(fr.to_ndarray().reshape(channels, -1))
        if pending:
            yield np.concatenate(pending, axis=1), rate, channels
    finally:
        container.close()

def audio_meta(path) -> Tuple[int, int, float]:
    c = _open(path)
    try:
        if not c.streams.audio:
            raise UtilError("В файле нет звуковой дорожки", "The file has no audio track")
        a = c.streams.audio[0]
        return a.codec_context.sample_rate or 44100, min(2, _channels(a)) or 2, _duration(c, a)
    finally:
        c.close()

def _fmt_opts(default: str = "mp3"):
    return [O("format", "choice", "Формат", "Format", default, choices=AUDIO_CHOICES),
            O("bitrate", "int", "Битрейт, кбит/с (0 — по умолчанию)", "Bitrate, kbps (0 = default)", 0, min=0, max=512, show="format=mp3|m4a|ogg|opus|ac3|wma")]

def _out_audio(ctx, f, suffix: str = "", fmt: Optional[str] = None) -> Tuple[Path, str]:
    fmt = fmt or ctx.o.get("format") or "mp3"
    ext = AUDIO_FORMATS[fmt][2]
    return ctx.out_path(f"{f.stem}{suffix}.{ext}"), fmt

def _pipe(ctx, f, suffix: str, transform: Optional[Callable] = None, start: float = 0.0, end: Optional[float] = None,
          rate: Optional[int] = None, channels: Optional[int] = None, fmt: Optional[str] = None) -> Path:
    src_rate, src_ch, _ = audio_meta(f.path())
    rate, channels = rate or src_rate, channels or src_ch
    path, fmt = _out_audio(ctx, f, suffix, fmt)
    writer = AudioWriter(path, fmt, rate, channels, int(ctx.o.get("bitrate") or 0) * 1000)
    t = 0.0
    try:
        for block, r, ch in read_audio(f.path(), rate, channels, start, end):
            if transform:
                block = transform(block, t, r)
            t += block.shape[1] / r
            writer.write(block)
    finally:
        writer.close()
    return path

@utility("audio.convert", "audio", "Конвертер аудио", "Audio converter",
         "MP3, WAV, FLAC, M4A/AAC, OGG/Opus, ALAC, AC3, WMA — пачкой; частота, моно/стерео и битрейт. Звук из видео тоже.",
         "MP3, WAV, FLAC, M4A/AAC, OGG/Opus, ALAC, AC3, WMA — in batches; sample rate, mono/stereo and bitrate. Audio from video too.",
         input="files", accept=AUDIO_ACCEPT,
         opts=_fmt_opts() + [O("rate", "choice", "Частота", "Sample rate", "keep", choices=[("keep", "Как в исходнике", "Keep"), ("48000", "48 кГц", "48 kHz"), ("44100", "44,1 кГц", "44.1 kHz"),
                                                                                       ("32000", "32 кГц", "32 kHz"), ("22050", "22 кГц", "22 kHz"), ("16000", "16 кГц (речь)", "16 kHz (speech)")]),
                             O("channels", "choice", "Каналы", "Channels", "keep", choices=[("keep", "Как в исходнике", "Keep"), ("1", "Моно", "Mono"), ("2", "Стерео", "Stereo")])],
         ai_default=True, keys="конвертировать аудио mp3 wav flac m4a ogg opus звук музыка audio convert")
def audio_convert(ctx):
    rows = []
    for f in ctx.files or [ctx.file()]:
        rate = None if ctx.o["rate"] == "keep" else int(ctx.o["rate"])
        ch = None if ctx.o["channels"] == "keep" else int(ctx.o["channels"])
        path = _pipe(ctx, f, "", rate=rate, channels=ch)
        rows.append([f.name, path.name, human_size(f.size, ctx.en), human_size(path.stat().st_size, ctx.en)])
    return {"table": table([ctx.tr("Файл", "File"), ctx.tr("Результат", "Result"), ctx.tr("Было", "Before"), ctx.tr("Стало", "After")], rows)}

@utility("audio.extract", "audio", "Звук из видео", "Extract audio from video",
         "Вытаскивает звуковую дорожку из MP4, MKV, MOV, WebM… в MP3, M4A, WAV или FLAC.", "Pulls the soundtrack out of MP4, MKV, MOV, WebM… as MP3, M4A, WAV or FLAC.",
         input="files", accept=VIDEO_ACCEPT, opts=_fmt_opts(), ai_default=True, keys="звук из видео извлечь аудио mp3 из mp4 extract audio")
def audio_extract(ctx):
    for f in ctx.files or [ctx.file()]:
        _pipe(ctx, f, "")
    return ""

def _fade(block, t, rate, start_fade, end_fade, total):
    import numpy as np
    n = block.shape[1]
    times = t + np.arange(n) / rate
    gain = np.ones(n, dtype=np.float32)
    if start_fade > 0:
        gain = np.minimum(gain, np.clip(times / start_fade, 0, 1))
    if end_fade > 0 and total:
        gain = np.minimum(gain, np.clip((total - times) / end_fade, 0, 1))
    return block * gain

@utility("audio.trim", "audio", "Обрезать аудио", "Trim audio",
         "Вырезает фрагмент по времени (1:30–2:45), с плавным появлением и затуханием — для рингтонов и нарезок.",
         "Cuts a segment by time (1:30–2:45), with fade in/out — for ringtones and clips.",
         input="file", accept=AUDIO_ACCEPT,
         opts=[O("start", "text", "Начало", "Start", "0:00"), O("end", "text", "Конец (пусто — до конца)", "End (empty = to the end)", ""),
               O("fadein", "number", "Появление, с", "Fade in, s", 0, min=0, max=60), O("fadeout", "number", "Затухание, с", "Fade out, s", 0, min=0, max=60)] + _fmt_opts(),
         ai_default=True, keys="обрезать аудио вырезать фрагмент рингтон trim cut audio fade")
def audio_trim(ctx):
    f = ctx.file()
    start = parse_time(ctx.o["start"])
    _, _, dur = audio_meta(f.path())
    end = parse_time(ctx.o["end"]) if str(ctx.o["end"]).strip() else dur
    if end <= start:
        raise UtilError("Конец должен быть позже начала", "The end must be after the start")
    total = end - start
    fi, fo = float(ctx.o["fadein"]), float(ctx.o["fadeout"])
    path = _pipe(ctx, f, "-cut", (lambda b, t, r: _fade(b, t, r, fi, fo, total)) if (fi or fo) else None, start, end)
    return {"note": ctx.tr(f"Фрагмент {total:.1f} с → {path.name}", f"Segment {total:.1f} s → {path.name}")}

@utility("audio.volume", "audio", "Громкость и нормализация", "Volume and normalize",
         "Сделать громче/тише на N дБ или выровнять до пика −1 дБ — тихие записи становятся нормальными.",
         "Louder/quieter by N dB or normalize to a −1 dB peak — quiet recordings become normal.",
         input="files", accept=AUDIO_ACCEPT,
         opts=[O("mode", "choice", "Режим", "Mode", "normalize", choices=[("normalize", "Нормализовать по пику", "Normalize to peak"), ("rms", "Выровнять громкость (RMS)", "Even out loudness (RMS)"),
                                                                       ("gain", "Изменить на N дБ", "Change by N dB")]),
               O("db", "number", "дБ (пик или изменение)", "dB (peak or change)", -1, min=-60, max=60)] + _fmt_opts(),
         ai_default=True, keys="громкость громче тише нормализация volume normalize louder gain db")
def audio_volume(ctx):
    import numpy as np
    for f in ctx.files or [ctx.file()]:
        mode, db = ctx.o["mode"], float(ctx.o["db"])
        if mode == "gain":
            gain = 10 ** (db / 20)
        else:
            peak, sq, n = 0.0, 0.0, 0
            for block, _, _ in read_audio(f.path()):
                peak = max(peak, float(np.max(np.abs(block))) if block.size else 0.0)
                sq += float(np.sum(block.astype(np.float64) ** 2))
                n += block.size
            if peak <= 1e-6:
                raise UtilError("В записи тишина — нечего усиливать", "The recording is silent — nothing to amplify")
            if mode == "normalize":
                gain = (10 ** (min(0.0, db) / 20)) / peak
            else:
                rms = math.sqrt(sq / max(1, n))
                target = 10 ** (min(-6.0, db if db < 0 else -16.0) / 20)
                gain = min(target / max(rms, 1e-6), (10 ** (-1 / 20)) / peak)
        _pipe(ctx, f, "-volume", lambda b, t, r, g=gain: np.clip(b * g, -1.0, 1.0))
    return {"note": ctx.tr(f"Усиление ×{gain:.2f} ({20 * math.log10(max(gain, 1e-9)):+.1f} дБ)", f"Gain ×{gain:.2f} ({20 * math.log10(max(gain, 1e-9)):+.1f} dB)")}

def _atempo_chain(speed: float) -> List[str]:
    chain = []
    while speed > 2.0:
        chain.append("2.0")
        speed /= 2.0
    while speed < 0.5:
        chain.append("0.5")
        speed /= 0.5
    chain.append(f"{speed:.5f}")
    return chain

@utility("audio.speed", "audio", "Скорость аудио", "Audio speed",
         "Ускорить или замедлить запись (лекции, подкасты) без «мультяшного» голоса, или вместе с тоном.",
         "Speed up or slow down a recording (lectures, podcasts) without chipmunk voices, or together with the pitch.",
         input="file", accept=AUDIO_ACCEPT,
         opts=[O("speed", "number", "Скорость, ×", "Speed, ×", 1.25, min=0.25, max=4, step=0.05), O("pitch", "bool", "Менять и тон (как пластинка)", "Change pitch too (like a record)", False)] + _fmt_opts(),
         ai_default=True, keys="скорость ускорить замедлить подкаст лекция speed tempo faster slower")
def audio_speed(ctx):
    av = _av()
    import numpy as np
    f = ctx.file()
    speed = float(ctx.o["speed"])
    rate, ch, dur = audio_meta(f.path())
    path, fmt = _out_audio(ctx, f, f"-x{speed:g}")
    writer = AudioWriter(path, fmt, rate, ch, int(ctx.o.get("bitrate") or 0) * 1000)
    try:
        if ctx.o["pitch"]:
            # resample trick: treat the samples as if recorded at rate*speed, then convert back to rate
            writer.src_rate = int(rate * speed)
            writer.resampler = av.AudioResampler(format="fltp", layout=writer.layout, rate=writer.rate)
            for block, _, _ in read_audio(f.path(), rate, ch):
                writer.write(block)
        else:
            graph = av.filter.Graph()
            src = graph.add_abuffer(format="fltp", sample_rate=rate, layout=_layout_name(ch), time_base=Fraction(1, rate))
            last = src
            for value in _atempo_chain(speed):
                node = graph.add("atempo", value)
                last.link_to(node)
                last = node
            sink = graph.add("abuffersink")
            last.link_to(sink)
            graph.configure()
            pts = 0

            def drain():
                while True:
                    try:
                        out = sink.pull()
                    except (av.error.BlockingIOError, av.error.EOFError):
                        return
                    writer.write(out.to_ndarray().reshape(ch, -1))
            for block, _, _ in read_audio(f.path(), rate, ch):
                frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(block, dtype=np.float32), format="fltp", layout=_layout_name(ch))
                frame.sample_rate = rate
                frame.pts = pts
                frame.time_base = Fraction(1, rate)
                pts += block.shape[1]
                src.push(frame)
                drain()
            src.push(None)
            drain()
    finally:
        writer.close()
    return {"note": ctx.tr(f"Было {dur:.0f} с → стало ~{dur / speed:.0f} с", f"{dur:.0f} s → ~{dur / speed:.0f} s")}

@utility("audio.reverse", "audio", "Аудио задом наперёд", "Reverse audio",
         "Проигрывает запись наоборот (до 20 минут).", "Plays a recording backwards (up to 20 minutes).",
         input="file", accept=AUDIO_ACCEPT, opts=_fmt_opts(), keys="задом наперёд реверс reverse backwards")
def audio_reverse(ctx):
    import numpy as np
    f = ctx.file()
    rate, ch, dur = audio_meta(f.path())
    if dur > 1200:
        raise UtilError("Для разворота нужна запись до 20 минут", "Reversing works for recordings up to 20 minutes")
    blocks = [b for b, _, _ in read_audio(f.path(), rate, ch)]
    whole = np.concatenate(blocks, axis=1)[:, ::-1] if blocks else np.zeros((ch, 0), np.float32)
    path, fmt = _out_audio(ctx, f, "-reversed")
    w = AudioWriter(path, fmt, rate, ch, int(ctx.o.get("bitrate") or 0) * 1000)
    try:
        step = rate * 5
        for i in range(0, whole.shape[1], step):
            w.write(whole[:, i:i + step])
    finally:
        w.close()
    return ""

@utility("audio.silence", "audio", "Убрать тишину", "Remove silence",
         "Срезает тишину в начале и конце или вырезает все паузы длиннее N секунд (диктофон, лекции).",
         "Trims silence at the start/end or cuts every pause longer than N seconds (voice memos, lectures).",
         input="file", accept=AUDIO_ACCEPT,
         opts=[O("mode", "choice", "Режим", "Mode", "edges", choices=[("edges", "Только в начале и конце", "Only start and end"), ("all", "Все длинные паузы", "Every long pause")]),
               O("threshold", "number", "Порог тишины, дБ", "Silence threshold, dB", -45, min=-90, max=-10),
               O("pause", "number", "Пауза длиннее, с", "Pauses longer than, s", 1.0, min=0.1, max=30, show="mode=all"),
               O("keep", "number", "Оставлять от паузы, с", "Keep of each pause, s", 0.3, min=0, max=5, show="mode=all")] + _fmt_opts(),
         ai_default=True, keys="тишина паузы убрать silence remove pauses trim")
def audio_silence(ctx):
    import numpy as np
    f = ctx.file()
    rate, ch, dur = audio_meta(f.path())
    thr = 10 ** (float(ctx.o["threshold"]) / 20)
    win = max(1, rate // 50)
    loud: List[bool] = []
    for block, _, _ in read_audio(f.path(), rate, ch):
        mono = np.max(np.abs(block), axis=0)
        n = mono.shape[0] // win
        if n:
            loud += list(mono[: n * win].reshape(n, win).max(axis=1) > thr)
    if not any(loud):
        raise UtilError("Вся запись тише порога", "The whole recording is below the threshold")
    step = win / rate
    first = loud.index(True) * step
    last = (len(loud) - loud[::-1].index(True)) * step
    if ctx.o["mode"] == "edges":
        path = _pipe(ctx, f, "-trimmed", None, max(0, first - 0.05), min(dur, last + 0.1))
        return {"note": ctx.tr(f"Убрано {first:.1f} с в начале и {max(0, dur - last):.1f} с в конце", f"Removed {first:.1f} s at the start and {max(0, dur - last):.1f} s at the end")}
    pause, keep = float(ctx.o["pause"]), float(ctx.o["keep"])
    cut: List[Tuple[float, float]] = []
    i = 0
    while i < len(loud):
        if not loud[i]:
            j = i
            while j < len(loud) and not loud[j]:
                j += 1
            a, b = i * step, j * step
            if b - a >= pause:
                cut.append((a + keep / 2 if a > 0 else 0, b - keep / 2 if j < len(loud) else b))
            i = j
        else:
            i += 1
    removed = sum(b - a for a, b in cut)

    def transform(block, t, r):
        n = block.shape[1]
        times = t + np.arange(n) / r
        mask = np.ones(n, dtype=bool)
        for a, b in cut:
            if b < t or a > t + n / r:
                continue
            mask &= ~((times >= a) & (times < b))
        return block[:, mask]
    _pipe(ctx, f, "-nopauses", transform)
    return {"note": ctx.tr(f"Вырезано пауз: {len(cut)}, всего {removed:.1f} с", f"Pauses cut: {len(cut)}, {removed:.1f} s in total")}

@utility("audio.merge", "audio", "Склеить аудиофайлы", "Join audio files",
         "Соединяет несколько записей в одну по порядку, с паузой или плавным переходом между ними.",
         "Joins several recordings into one in order, with a gap or a crossfade between them.",
         input="files", accept=AUDIO_ACCEPT,
         opts=[O("gap", "number", "Пауза между, с", "Gap between, s", 0, min=0, max=30), O("cross", "number", "Плавный переход, с", "Crossfade, s", 0, min=0, max=10)] + _fmt_opts(),
         ai_default=True, keys="склеить соединить объединить аудио merge join concatenate audio")
def audio_merge(ctx):
    import numpy as np
    files = ctx.files
    if len(files) < 2:
        raise UtilError("Добавьте хотя бы два файла", "Add at least two files")
    rate, ch, _ = audio_meta(files[0].path())
    ch = max(ch, *(audio_meta(f.path())[1] for f in files[1:]))
    path, fmt = _out_audio(ctx, files[0], "-merged")
    w = AudioWriter(path, fmt, rate, ch, int(ctx.o.get("bitrate") or 0) * 1000)
    cross = int(float(ctx.o["cross"]) * rate)
    gap = int(float(ctx.o["gap"]) * rate)
    tail = None
    total = 0.0
    try:
        for idx, f in enumerate(files):
            blocks = [b for b, _, _ in read_audio(f.path(), rate, ch, block_seconds=5)]
            data = np.concatenate(blocks, axis=1) if blocks else np.zeros((ch, 0), np.float32)
            total += data.shape[1] / rate
            if tail is not None and cross and data.shape[1] > cross and tail.shape[1] >= cross:
                ramp = np.linspace(0, 1, cross, dtype=np.float32)
                mixed = tail[:, -cross:] * (1 - ramp) + data[:, :cross] * ramp
                w.write(tail[:, :-cross])
                w.write(mixed)
                data = data[:, cross:]
            elif tail is not None:
                w.write(tail)
                if gap:
                    w.write(np.zeros((ch, gap), np.float32))
            tail = data
        if tail is not None:
            w.write(tail)
    finally:
        w.close()
    return {"note": ctx.tr(f"Склеено файлов: {len(files)}, ~{total:.0f} с", f"Joined {len(files)} files, ~{total:.0f} s")}

@utility("audio.split", "audio", "Разрезать на части", "Split audio",
         "Режет длинную запись на куски по N минут (например, для расшифровки или отправки в мессенджер).",
         "Cuts a long recording into N-minute pieces (e.g. for transcription or messengers).",
         input="file", accept=AUDIO_ACCEPT, opts=[O("minutes", "number", "Длина части, мин", "Piece length, min", 10, min=0.1, max=600)] + _fmt_opts(),
         keys="разрезать разделить аудио части split audio chunks")
def audio_split(ctx):
    f = ctx.file()
    _, _, dur = audio_meta(f.path())
    size = float(ctx.o["minutes"]) * 60
    parts = math.ceil(dur / size) if dur else 1
    if parts > 200:
        raise UtilError("Получится больше 200 частей — увеличьте длину", "More than 200 pieces — increase the length")
    for i in range(parts):
        _pipe(ctx, f, f"-part{i + 1:02d}", None, i * size, min(dur, (i + 1) * size))
    return {"note": ctx.tr(f"Частей: {parts}", f"Pieces: {parts}")}

@utility("audio.info", "audio", "Информация об аудио", "Audio info",
         "Длительность, кодек, битрейт, частота, каналы и теги (исполнитель, альбом, год, обложка).",
         "Duration, codec, bitrate, sample rate, channels and tags (artist, album, year, cover).",
         input="file", accept=AUDIO_ACCEPT, ai_default=True, keys="информация аудио битрейт длительность теги audio info tags")
def audio_info(ctx):
    f = ctx.file()
    c = _open(f.path())
    try:
        if not c.streams.audio:
            raise UtilError("В файле нет звуковой дорожки", "The file has no audio track")
        a = c.streams.audio[0]
        dur = _duration(c, a) or _duration(c)
        br = a.codec_context.bit_rate or (c.bit_rate or 0)
        info = [[ctx.tr("Файл", "File"), f"{f.name} ({human_size(f.size, ctx.en)})"], [ctx.tr("Формат", "Container"), c.format.long_name or c.format.name],
                [ctx.tr("Кодек", "Codec"), a.codec_context.codec.long_name or a.codec_context.name], [ctx.tr("Длительность", "Duration"), _hms(dur)],
                [ctx.tr("Битрейт", "Bitrate"), f"{br // 1000} kbps" if br else "—"], [ctx.tr("Частота", "Sample rate"), f"{a.codec_context.sample_rate} Hz"],
                [ctx.tr("Каналы", "Channels"), _channels(a)]]
        tags = dict(c.metadata or {})
        tags.update(a.metadata or {})
    finally:
        c.close()
    try:
        import mutagen
        mf = mutagen.File(str(f.path()), easy=True)
        if mf and mf.tags:
            for k, v in mf.tags.items():
                tags[k] = ", ".join(v) if isinstance(v, list) else str(v)
        full = mutagen.File(str(f.path()))
        if full is not None and any(str(k).startswith(("APIC", "covr", "METADATA_BLOCK_PICTURE")) for k in (full.tags or {}).keys()) or getattr(full, "pictures", None):
            info.append([ctx.tr("Обложка", "Cover art"), ctx.tr("есть", "yes")])
    except Exception:  # noqa: BLE001
        pass
    for k, v in list(tags.items())[:30]:
        if k.lower() not in ("encoder", "major_brand", "minor_version", "compatible_brands"):
            info.append([k, str(v)[:200]])
    return {"info": info}

def _hms(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    frac = seconds - int(seconds)
    return (f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}") + (f".{int(frac * 10)}" if frac >= 0.05 else "")

@utility("audio.tags", "audio", "Теги и обложка", "Tags and cover art",
         "Меняет название, исполнителя, альбом, год, жанр и номер трека; второй файл-картинка становится обложкой (MP3, FLAC, M4A, OGG).",
         "Edits title, artist, album, year, genre and track number; a second image file becomes the cover (MP3, FLAC, M4A, OGG).",
         input="files", accept=AUDIO_ACCEPT + ",image/*",
         opts=[O("title", "text", "Название", "Title", ""), O("artist", "text", "Исполнитель", "Artist", ""), O("album", "text", "Альбом", "Album", ""),
               O("date", "text", "Год", "Year", ""), O("genre", "text", "Жанр", "Genre", ""), O("tracknumber", "text", "Номер трека", "Track number", ""),
               O("clear", "bool", "Сначала удалить все старые теги", "Remove all old tags first", False)],
         keys="теги mp3 id3 обложка исполнитель альбом tags cover artist album")
def audio_tags(ctx):
    try:
        import mutagen
    except ImportError:
        raise UtilError("Библиотека mutagen недоступна", "mutagen is not available")
    from .core import kind_of
    audio = [f for f in ctx.files if kind_of(f.name) != "image"]
    images = [f for f in ctx.files if kind_of(f.name) == "image"]
    if not audio:
        raise UtilError("Добавьте аудиофайл", "Add an audio file")
    for f in audio:
        out = ctx.out_bytes(f.name, f.data)
        mf = mutagen.File(str(out), easy=True)
        if mf is None:
            raise UtilError(f"«{f.name}»: формат тегов не поддерживается", f"“{f.name}”: tag format not supported")
        if ctx.o["clear"] and mf.tags is not None:
            mf.delete()
            mf = mutagen.File(str(out), easy=True)
        if mf.tags is None:
            mf.add_tags()
        for key in ("title", "artist", "album", "date", "genre", "tracknumber"):
            if str(ctx.o[key]).strip():
                mf[key] = str(ctx.o[key]).strip()
        mf.save()
        if images:
            _set_cover(out, images[0])
    return {"note": ctx.tr("Теги записаны", "Tags written")}

def _set_cover(path: Path, image) -> None:
    import mutagen
    mime = "image/png" if image.data[:4] == b"\x89PNG" else "image/jpeg"
    ext = path.suffix.lower()
    if ext == ".mp3":
        from mutagen.id3 import APIC, ID3
        tags = ID3(str(path))
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image.data))
        tags.save(str(path))
    elif ext == ".flac":
        from mutagen.flac import FLAC, Picture
        mf = FLAC(str(path))
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, mime, image.data
        mf.clear_pictures()
        mf.add_picture(pic)
        mf.save()
    elif ext in (".m4a", ".mp4", ".aac"):
        from mutagen.mp4 import MP4, MP4Cover
        mf = MP4(str(path))
        mf["covr"] = [MP4Cover(image.data, imageformat=MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG)]
        mf.save()
    elif ext in (".ogg", ".opus"):
        import base64
        from mutagen.flac import Picture
        mf = mutagen.File(str(path))
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, mime, image.data
        mf["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
        mf.save()

@utility("audio.waveform", "audio", "Волна звука картинкой", "Waveform image",
         "Рисует форму волны записи в PNG — для обложек подкастов, соцсетей и визуальной проверки.",
         "Draws the waveform as a PNG — for podcast covers, social posts and quick visual checks.",
         input="file", accept=AUDIO_ACCEPT,
         opts=[O("width", "int", "Ширина", "Width", 1600, min=100, max=8000), O("height", "int", "Высота", "Height", 300, min=40, max=3000),
               O("color", "color", "Цвет волны", "Wave color", "#6366f1"), O("bg", "color", "Фон (пусто — прозрачный)", "Background (empty = transparent)", ""),
               O("style", "choice", "Стиль", "Style", "bars", choices=[("bars", "Столбики", "Bars"), ("wave", "Сплошная волна", "Solid wave")])],
         keys="волна waveform визуализация звука картинка")
def audio_waveform(ctx):
    import numpy as np
    from PIL import Image, ImageDraw
    from .core import parse_color
    from .t_gen import save_image
    f = ctx.file()
    W, H = int(ctx.o["width"]), int(ctx.o["height"])
    peaks: List[float] = []
    for block, r, ch in read_audio(f.path(), 8000, 1):
        peaks.append(block[0])
    data = np.abs(np.concatenate(peaks)) if peaks else np.zeros(1, np.float32)
    cols = W // 4 if ctx.o["style"] == "bars" else W
    n = max(1, len(data) // cols)
    env = data[: n * cols].reshape(cols, n).max(axis=1) if len(data) >= cols else np.pad(data, (0, cols - len(data)))
    env = env / (env.max() or 1)
    bg = parse_color(ctx.o["bg"], None) if str(ctx.o["bg"]).strip() else None
    img = Image.new("RGBA", (W, H), (tuple(bg[:3]) + (255,)) if bg else (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = tuple(parse_color(ctx.o["color"], (99, 102, 241))[:3]) + (255,)
    mid = H / 2
    if ctx.o["style"] == "bars":
        for i, v in enumerate(env):
            h = max(2, v * (H * 0.9) / 2)
            d.rounded_rectangle([i * 4, mid - h, i * 4 + 2, mid + h], radius=1, fill=color)
    else:
        pts_top = [(i, mid - v * H * 0.45) for i, v in enumerate(env)]
        pts_bot = [(i, mid + v * H * 0.45) for i, v in reversed(list(enumerate(env)))]
        d.polygon(pts_top + pts_bot, fill=color)
    ctx.out_bytes(f"{f.stem}-waveform.png", save_image(img, "png"))
    return ""

@utility("audio.tone", "audio", "Генератор тона и шума", "Tone and noise generator",
         "Синус, меандр, пила, треугольник, белый/розовый шум, тональные сигналы DTMF — для проверки колонок и тестов.",
         "Sine, square, saw, triangle, white/pink noise, DTMF tones — for testing speakers and pipelines.",
         input="none", opts=[O("wave", "choice", "Сигнал", "Signal", "sine", choices=[("sine", "Синус", "Sine"), ("square", "Меандр", "Square"), ("saw", "Пила", "Saw"),
                                                                       ("triangle", "Треугольник", "Triangle"), ("white", "Белый шум", "White noise"), ("pink", "Розовый шум", "Pink noise"),
                                                                       ("sweep", "Свип 20 Гц → 20 кГц", "Sweep 20 Hz → 20 kHz"), ("dtmf", "DTMF по цифрам", "DTMF digits")]),
                             O("freq", "number", "Частота, Гц", "Frequency, Hz", 440, min=1, max=22000, show="wave=sine|square|saw|triangle"),
                             O("digits", "text", "Цифры DTMF", "DTMF digits", "123#", show="wave=dtmf"),
                             O("seconds", "number", "Длительность, с", "Duration, s", 3, min=0.1, max=600), O("volume", "int", "Громкость, %", "Volume, %", 50, min=1, max=100),
                             O("format", "choice", "Формат", "Format", "wav", choices=AUDIO_CHOICES[:5])],
         keys="тон генератор звука синус шум dtmf tone generator sine noise test speakers")
def audio_tone(ctx):
    import numpy as np
    rate = 48000 if ctx.o["format"] in ("ogg", "opus") else 44100
    wave, secs, vol = ctx.o["wave"], float(ctx.o["seconds"]), int(ctx.o["volume"]) / 100
    if wave == "dtmf":
        table_ = {"1": (697, 1209), "2": (697, 1336), "3": (697, 1477), "A": (697, 1633), "4": (770, 1209), "5": (770, 1336), "6": (770, 1477), "B": (770, 1633),
                  "7": (852, 1209), "8": (852, 1336), "9": (852, 1477), "C": (852, 1633), "*": (941, 1209), "0": (941, 1336), "#": (941, 1477), "D": (941, 1633)}
        parts = []
        for ch in str(ctx.o["digits"]).upper():
            if ch in table_:
                t = np.arange(int(rate * 0.2)) / rate
                lo, hi = table_[ch]
                parts += [0.5 * (np.sin(2 * np.pi * lo * t) + np.sin(2 * np.pi * hi * t)), np.zeros(int(rate * 0.08))]
        if not parts:
            raise UtilError("Введите цифры 0–9, *, #, A–D", "Enter digits 0–9, *, #, A–D")
        sig = np.concatenate(parts)
    else:
        n = int(rate * secs)
        t = np.arange(n) / rate
        f0 = float(ctx.o["freq"])
        if wave == "sine":
            sig = np.sin(2 * np.pi * f0 * t)
        elif wave == "square":
            sig = np.sign(np.sin(2 * np.pi * f0 * t))
        elif wave == "saw":
            sig = 2 * (t * f0 - np.floor(0.5 + t * f0))
        elif wave == "triangle":
            sig = 2 * np.abs(2 * (t * f0 - np.floor(0.5 + t * f0))) - 1
        elif wave == "white":
            sig = np.random.default_rng().uniform(-1, 1, n)
        elif wave == "pink":
            white = np.random.default_rng().normal(size=n)
            spectrum = np.fft.rfft(white)
            spectrum /= np.sqrt(np.maximum(np.arange(len(spectrum)), 1))
            sig = np.fft.irfft(spectrum, n)
            sig /= np.max(np.abs(sig)) or 1
        else:
            phase = 2 * np.pi * 20 * secs / math.log(1000) * (np.exp(t / secs * math.log(1000)) - 1)
            sig = np.sin(phase)
    sig = (sig * vol).astype(np.float32)
    fade = min(len(sig) // 10, int(rate * 0.01))
    if fade:
        sig[:fade] *= np.linspace(0, 1, fade)
        sig[-fade:] *= np.linspace(1, 0, fade)
    fmt = ctx.o["format"]
    path = ctx.out_path(f"tone-{wave}.{AUDIO_FORMATS[fmt][2]}")
    w = AudioWriter(path, fmt, rate, 1)
    try:
        for i in range(0, len(sig), rate):
            w.write(sig[None, i:i + rate])
    finally:
        w.close()
    return ""

# ── video ───────────────────────────────────────────────────────────────────────────────────────────────────────────
VIDEO_FORMATS = {"mp4": ("mp4", "libx264", "aac", "mp4"), "webm": ("webm", "libvpx-vp9", "libopus", "webm"), "mkv": ("matroska", "libx264", "aac", "mkv"),
                 "mov": ("mov", "libx264", "aac", "mov"), "avi": ("avi", "mpeg4", "libmp3lame", "avi"), "mp4-hevc": ("mp4", "libx265", "aac", "mp4")}
VIDEO_CHOICES = [("mp4", "MP4 (H.264) — везде открывается", "MP4 (H.264) — plays everywhere"), ("webm", "WebM (VP9)", "WebM (VP9)"), ("mkv", "MKV", "MKV"),
                 ("mov", "MOV", "MOV"), ("avi", "AVI (MPEG-4)", "AVI (MPEG-4)"), ("mp4-hevc", "MP4 (H.265/HEVC) — меньше вес", "MP4 (H.265/HEVC) — smaller")]

def _pick_encoder(av, name: str) -> str:
    fallbacks = {"libx264": ["libx264", "h264", "mpeg4"], "libx265": ["libx265", "hevc", "libx264", "h264"], "libvpx-vp9": ["libvpx-vp9", "libvpx"],
                 "aac": ["aac"], "libopus": ["libopus", "opus"], "libmp3lame": ["libmp3lame"], "mpeg4": ["mpeg4"]}
    for cand in fallbacks.get(name, [name]):
        try:
            av.codec.Codec(cand, "w")
            return cand
        except Exception:  # noqa: BLE001
            continue
    raise UtilError(f"Кодек {name} недоступен в этой сборке", f"The {name} encoder is not available in this build")

def _even(v: int) -> int:
    return max(2, int(v) // 2 * 2)

def transcode_video(src: Path, dst: Path, fmt: str, *, height: int = 0, crf: int = 23, start: float = 0.0, end: Optional[float] = None,
                    mute: bool = False, vbitrate: int = 0, abitrate: int = 128000, vf: str = "") -> Dict[str, Any]:
    """Re-encode the first video (and audio) stream of ``src`` into ``dst``; optional cut, downscale and filter chain."""
    av = _av()
    container_name, vcodec, acodec, _ = VIDEO_FORMATS[fmt]
    inp = _open(src)
    try:
        if not inp.streams.video:
            raise UtilError("В файле нет видеодорожки", "The file has no video track")
        ist = inp.streams.video[0]
        ist.thread_type = "AUTO"
        ias = inp.streams.audio[0] if inp.streams.audio and not mute else None
        dur = _duration(inp, ist) or _duration(inp)
        if end and dur:
            end = min(end, dur + float(inp.start_time or 0) / 1_000_000)
        out = av.open(str(dst), "w", format=container_name)
        rate = Fraction(ist.average_rate or ist.guessed_rate or 30).limit_denominator(1001)
        ost = out.add_stream(_pick_encoder(av, vcodec), rate=rate)
        w, h = ist.codec_context.width, ist.codec_context.height
        graph = sink = None
        if vf:
            graph = av.filter.Graph()
            last = graph.add_buffer(template=ist)
            for part in vf.split(","):
                name, _, args = part.partition("=")
                node = graph.add(name, args or None)
                last.link_to(node)
                last = node
            sink = graph.add("buffersink")
            last.link_to(sink)
            graph.configure()
            if vf.count("transpose") % 2 == 1:
                w, h = h, w
        if height and h > height:
            w, h = round(w * height / h), height
        ost.width, ost.height = _even(w), _even(h)
        ost.pix_fmt = "yuv420p"
        tb = Fraction(1, 90000)
        ost.codec_context.time_base = tb
        name = ost.codec_context.name
        opts: Dict[str, str] = {}
        if name in ("libx264", "libx265"):
            opts = {"preset": "veryfast"}
            if not vbitrate:
                opts["crf"] = str(crf)
            if name == "libx265":
                opts["x265-params"] = "log-level=error"
        elif name.startswith("libvpx"):
            opts = {"deadline": "realtime", "cpu-used": "8", "row-mt": "1"}
            if not vbitrate:
                opts.update({"crf": str(min(63, crf + 8)), "b": "0"})
        elif name == "mpeg4" and not vbitrate:
            opts = {"qscale": "4"}
        if vbitrate:
            ost.bit_rate = vbitrate
        ost.codec_context.options = opts
        oas = None
        if ias is not None:
            ac = _pick_encoder(av, acodec)
            arate = 48000 if ac in ("libopus", "opus") else (ias.codec_context.sample_rate or 44100)
            oas = out.add_stream(ac, rate=arate, layout=_layout_name(min(2, _channels(ias)) or 2))
            oas.bit_rate = abitrate
        origin = max(start, float(inp.start_time or 0) / 1_000_000)
        if start > 0:
            inp.seek(int(start * 1_000_000), backward=True, any_frame=False)
        last_vpts = -1
        last_apts = -1
        frames_done = 0
        streams = [ist] + ([ias] if ias is not None else [])

        def put_video(fr) -> None:
            nonlocal last_vpts, frames_done
            ft = float(fr.time) if fr.time is not None else 0.0
            if ft < origin - 1e-3 or (end is not None and ft > end):
                return
            out_frame = fr.reformat(width=ost.width, height=ost.height, format="yuv420p")
            pts = max(int(round((ft - origin) * 90000)), last_vpts + 1)
            last_vpts = pts
            out_frame.pts = pts
            out_frame.time_base = tb
            for p in ost.encode(out_frame):
                out.mux(p)
            frames_done += 1

        for packet in inp.demux(streams):
            if packet.stream.type == "video":
                for frame in packet.decode():
                    if graph is None:
                        put_video(frame)
                        continue
                    graph.vpush(frame)
                    while True:
                        try:
                            put_video(graph.vpull())
                        except (av.error.BlockingIOError, av.error.EOFError):
                            break
            elif oas is not None:
                for frame in packet.decode():
                    t = float(frame.time) if frame.time is not None else 0.0
                    if t < origin - 0.02 or (end is not None and t > end):
                        continue
                    ftb = Fraction(1, frame.sample_rate)
                    pts = int(round((t - origin) * frame.sample_rate))
                    if pts <= last_apts:
                        pts = last_apts + 1
                    frame.pts = pts
                    frame.time_base = ftb
                    last_apts = pts + frame.samples - 1
                    for p in oas.encode(frame):
                        out.mux(p)
        for p in ost.encode(None):
            out.mux(p)
        if oas is not None:
            for p in oas.encode(None):
                out.mux(p)
        out.close()
        length = ((end if end is not None else origin + dur) - origin) if dur else 0.0
        return {"duration": max(0.0, length), "width": ost.width, "height": ost.height, "frames": frames_done}
    finally:
        inp.close()

def _vopts(default_fmt: str = "mp4"):
    return [O("format", "choice", "Формат", "Format", default_fmt, choices=VIDEO_CHOICES),
            O("height", "choice", "Высота кадра", "Frame height", "0", choices=[("0", "Как в исходнике", "Keep"), ("2160", "2160p (4K)", "2160p (4K)"), ("1440", "1440p", "1440p"),
                                                                             ("1080", "1080p", "1080p"), ("720", "720p", "720p"), ("480", "480p", "480p"), ("360", "360p", "360p")]),
            O("crf", "int", "Качество (CRF: 18 — отлично, 28 — меньше вес)", "Quality (CRF: 18 = great, 28 = smaller)", 23, min=10, max=40)]

@utility("video.download", "video", "Скачать видео по ссылке", "Download video from a link",
         "YouTube, Vimeo, TikTok, Twitch и другие сайты. Одно общедоступное видео до 300 МБ.",
         "YouTube, Vimeo, TikTok, Twitch and other sites. One public video up to 300 MB.",
         ai=False, danger=True, placeholder="Вставьте ссылку на видео", placeholder_en="Paste a video link",
         keys="youtube ютуб youtu.be vimeo tiktok тикток twitch твич vk rutube рутуб reddit video download",
         opts=[O("height", "choice", "Качество", "Quality", "best", choices=[
             ("best", "Лучшее доступное", "Best available"), ("1080", "До 1080p", "Up to 1080p"),
             ("720", "До 720p", "Up to 720p"), ("480", "До 480p", "Up to 480p")])])
def video_download(ctx):
    from .video_download import download
    return download(ctx)


@utility("video.convert", "video", "Конвертер видео", "Video converter",
         "MP4 (H.264/H.265), WebM, MKV, MOV, AVI; уменьшение разрешения и качества. Работает без интернета.",
         "MP4 (H.264/H.265), WebM, MKV, MOV, AVI; downscale and quality control. Works offline.",
         input="file", accept=VIDEO_ACCEPT, opts=_vopts(), ai_default=True, keys="конвертировать видео mp4 webm mkv mov avi формат video convert")
def video_convert(ctx):
    f = ctx.file()
    fmt = ctx.o["format"]
    dst = ctx.out_path(f"{f.stem}.{VIDEO_FORMATS[fmt][3]}" if VIDEO_FORMATS[fmt][3] != f.ext else f"{f.stem}-converted.{VIDEO_FORMATS[fmt][3]}")
    info = transcode_video(f.path(), dst, fmt, height=int(ctx.o["height"]), crf=int(ctx.o["crf"]))
    return {"note": f"{info['width']}×{info['height']}, {_hms(info['duration'])} · {human_size(f.size, ctx.en)} → {human_size(dst.stat().st_size, ctx.en)}"}

@utility("video.trim", "video", "Обрезать видео", "Trim video",
         "Вырезает фрагмент по времени; «быстрый» режим без перекодирования режет по ключевым кадрам мгновенно.",
         "Cuts a segment by time; the fast mode without re-encoding cuts at keyframes instantly.",
         input="file", accept=VIDEO_ACCEPT,
         opts=[O("start", "text", "Начало", "Start", "0:00"), O("end", "text", "Конец (пусто — до конца)", "End (empty = to the end)", ""),
               O("fast", "bool", "Быстро, без перекодирования (точность ±1–2 с)", "Fast, no re-encoding (±1–2 s precision)", True)] + _vopts()[1:],
         ai_default=True, keys="обрезать видео вырезать фрагмент trim cut video")
def video_trim(ctx):
    f = ctx.file()
    start = parse_time(ctx.o["start"])
    end = parse_time(ctx.o["end"]) if str(ctx.o["end"]).strip() else None
    if end is not None and end <= start:
        raise UtilError("Конец должен быть позже начала", "The end must be after the start")
    if ctx.o["fast"]:
        dst = ctx.out_path(f"{f.stem}-cut.{f.ext or 'mp4'}")
        _remux(f.path(), dst, start=start, end=end)
        return {"note": ctx.tr("Готово без перекодирования", "Done without re-encoding")}
    dst = ctx.out_path(f"{f.stem}-cut.mp4")
    info = transcode_video(f.path(), dst, "mp4", height=int(ctx.o["height"]), crf=int(ctx.o["crf"]), start=start, end=end)
    return {"note": _hms(info["duration"])}

def _remux(src: Path, dst: Path, start: float = 0.0, end: Optional[float] = None, drop_audio: bool = False, drop_video: bool = False) -> None:
    av = _av()
    inp = _open(src)
    try:
        fmt = None
        if dst.suffix.lower() in (".mp4", ".m4v", ".mov"):
            fmt = "mp4" if dst.suffix.lower() != ".mov" else "mov"
        out = av.open(str(dst), "w", format=fmt) if fmt else av.open(str(dst), "w")
        mapping = {}
        for s in inp.streams:
            if s.type == "video" and not drop_video or s.type == "audio" and not drop_audio:
                try:
                    mapping[s.index] = out.add_stream_from_template(s)
                except Exception:  # noqa: BLE001 - codec not storable in this container
                    continue
        if not mapping:
            raise UtilError("Нечего сохранять: нет подходящих дорожек", "Nothing to keep: no suitable tracks")
        if start > 0:
            vs = inp.streams.video[0] if inp.streams.video else list(inp.streams)[0]
            inp.seek(int(start / vs.time_base), stream=vs, backward=True, any_frame=False)
        offsets: Dict[int, Optional[int]] = {}
        for packet in inp.demux([s for s in inp.streams if s.index in mapping]):
            if packet.dts is None:
                continue
            s = packet.stream
            t = float(packet.pts * s.time_base) if packet.pts is not None else float(packet.dts * s.time_base)
            if end is not None and t > end:
                continue
            if start and t < start - 2 and s.type == "audio":
                continue
            if s.index not in offsets:
                offsets[s.index] = packet.dts
            off = offsets[s.index] or 0
            packet.dts -= off
            if packet.pts is not None:
                packet.pts -= off
            packet.stream = mapping[s.index]
            out.mux(packet)
        out.close()
    finally:
        inp.close()

@utility("video.mute", "video", "Убрать звук из видео", "Remove audio from video",
         "Удаляет звуковую дорожку мгновенно, без потери качества видео.", "Removes the soundtrack instantly, without touching video quality.",
         input="file", accept=VIDEO_ACCEPT, ai_default=True, keys="убрать звук без звука mute видео remove audio")
def video_mute(ctx):
    f = ctx.file()
    _remux(f.path(), ctx.out_path(f"{f.stem}-mute.{f.ext or 'mp4'}"), drop_audio=True)
    return ""

@utility("video.gif", "video", "Видео → GIF / WebP", "Video → GIF / WebP",
         "Превращает отрывок видео в анимированный GIF или WebP: частота кадров, ширина, начало и длительность.",
         "Turns a video clip into an animated GIF or WebP: frame rate, width, start and duration.",
         input="file", accept=VIDEO_ACCEPT,
         opts=[O("start", "text", "Начало", "Start", "0:00"), O("seconds", "number", "Длительность, с", "Duration, s", 5, min=0.2, max=60),
               O("fps", "int", "Кадров в секунду", "Frames per second", 12, min=1, max=30), O("width", "int", "Ширина, px", "Width, px", 480, min=40, max=1920),
               O("format", "choice", "Формат", "Format", "gif", choices=[("gif", "GIF", "GIF"), ("webp", "WebP (меньше вес)", "WebP (smaller)")])],
         ai_default=True, keys="gif из видео гифка видео в гиф video to gif webp")
def video_gif(ctx):
    from PIL import Image
    f = ctx.file()
    start, secs, fps, width = parse_time(ctx.o["start"]), float(ctx.o["seconds"]), int(ctx.o["fps"]), int(ctx.o["width"])
    frames = []
    for t, img in _iter_frames(f.path(), start, start + secs, 1 / fps):
        img.thumbnail((width, width * 4), Image.LANCZOS)
        frames.append(img.convert("RGB"))
        if len(frames) >= 900:
            break
    if not frames:
        raise UtilError("Не удалось получить кадры — проверьте время начала", "No frames — check the start time")
    buf = io.BytesIO()
    if ctx.o["format"] == "gif":
        pal = [fr.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG) for fr in frames]
        pal[0].save(buf, "GIF", save_all=True, append_images=pal[1:], duration=int(1000 / fps), loop=0, optimize=True)
    else:
        frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], duration=int(1000 / fps), loop=0, quality=75, method=4)
    ctx.out_bytes(f"{f.stem}.{ctx.o['format']}", buf.getvalue())
    return {"note": ctx.tr(f"Кадров: {len(frames)}, {frames[0].width}×{frames[0].height}", f"Frames: {len(frames)}, {frames[0].width}×{frames[0].height}")}

def _iter_frames(path: Path, start: float, end: Optional[float], step: float):
    inp = _open(path)
    try:
        if not inp.streams.video:
            raise UtilError("В файле нет видеодорожки", "The file has no video track")
        ist = inp.streams.video[0]
        ist.thread_type = "AUTO"
        if start > 0.5:
            inp.seek(int(start / ist.time_base), stream=ist, backward=True, any_frame=False)
        nxt = start
        for frame in inp.decode(ist):
            t = float(frame.time) if frame.time is not None else 0.0
            if t + 1e-6 < nxt:
                continue
            if end is not None and t > end:
                break
            yield t, frame.to_image()
            nxt = max(nxt + step, t + step * 0.5) if step > 0 else t + 1e9
    finally:
        inp.close()

def _frames_at(path: Path, times: List[float]):
    """Frames nearest after each moment in *times* (sorted). Seeks for big gaps, so a contact sheet of a two-hour
    film does not decode the whole film."""
    inp = _open(path)
    try:
        if not inp.streams.video:
            raise UtilError("В файле нет видеодорожки", "The file has no video track")
        ist = inp.streams.video[0]
        ist.thread_type = "AUTO"
        decoder, last_t = None, -1e9
        for target in times:
            if decoder is None or target - last_t > 8:
                try:
                    inp.seek(max(0, int(target / ist.time_base)), stream=ist, backward=True, any_frame=False)
                except Exception:  # noqa: BLE001 - not seekable: decode on
                    if decoder is None:
                        inp.seek(0)
                decoder = inp.decode(ist)
            got = None
            for frame in decoder:
                t = float(frame.time) if frame.time is not None else 0.0
                last_t = t
                if t + 0.02 >= target:
                    got = (t, frame.to_image())
                    break
            if got is None:
                break
            yield got
    finally:
        inp.close()

@utility("video.frames", "video", "Кадры и стоп-кадр из видео", "Frames and snapshots from video",
         "Сохраняет кадр в нужный момент или кадры каждые N секунд (ZIP) — для превью, раскадровки и анализа.",
         "Saves a frame at a moment or a frame every N seconds (ZIP) — for thumbnails, storyboards and analysis.",
         input="file", accept=VIDEO_ACCEPT,
         opts=[O("mode", "choice", "Режим", "Mode", "one", choices=[("one", "Один кадр в момент", "One frame at a time"), ("every", "Каждые N секунд", "Every N seconds"),
                                                               ("sheet", "Лист раскадровки (сетка)", "Contact sheet (grid)")]),
               O("at", "text", "Момент", "Moment", "0:01", show="mode=one"), O("step", "number", "Шаг, с", "Step, s", 5, min=0.1, max=3600, show="mode=every"),
               O("count", "int", "Кадров на листе", "Frames on the sheet", 16, min=2, max=60, show="mode=sheet"),
               O("format", "choice", "Формат", "Format", "jpg", choices=[("jpg", "JPG", "JPG"), ("png", "PNG", "PNG"), ("webp", "WebP", "WebP")])],
         ai_default=True, keys="кадр скриншот из видео стоп-кадр превью frames snapshot thumbnail раскадровка")
def video_frames(ctx):
    from PIL import Image
    from .t_gen import save_image
    f = ctx.file()
    fmt = ctx.o["format"]
    if ctx.o["mode"] == "one":
        at = parse_time(ctx.o["at"])
        for t, img in _iter_frames(f.path(), at, None, 0):
            ctx.out_bytes(f"{f.stem}-{_hms(t).replace(':', '-')}.{fmt}", save_image(img, fmt, 92))
            return {"note": _hms(t)}
        raise UtilError("Кадр не найден — момент за пределами видео?", "No frame — is the moment past the end?")
    shots = []
    if ctx.o["mode"] == "sheet":
        with _open(f.path()) as probe:
            vs = probe.streams.video[0] if probe.streams.video else None
            dur = _duration(probe, vs) or _duration(probe)
        count = int(ctx.o["count"])
        times = [(i + 0.5) * dur / count for i in range(count)] if dur > 0 else [0.0]
        shots = list(_frames_at(f.path(), times))
    else:
        step = float(ctx.o["step"])
        for t, img in _iter_frames(f.path(), 0, None, step):
            shots.append((t, img))
            if len(shots) >= 1000:
                break
    if not shots:
        raise UtilError("Кадры не найдены", "No frames found")
    if ctx.o["mode"] == "sheet":
        from .t_gen import font
        from PIL import ImageDraw
        cols = min(max(2, math.ceil(math.sqrt(len(shots)))), len(shots))
        rows = math.ceil(len(shots) / cols)
        cw = 320
        ch = round(cw * shots[0][1].height / shots[0][1].width)
        sheet = Image.new("RGB", (cols * (cw + 8) + 8, rows * (ch + 8) + 8), (20, 20, 24))
        d = ImageDraw.Draw(sheet)
        fnt = font(16, True)
        for i, (t, img) in enumerate(shots):
            r, c = divmod(i, cols)
            x, y = 8 + c * (cw + 8), 8 + r * (ch + 8)
            sheet.paste(img.resize((cw, ch)), (x, y))
            d.text((x + 6, y + ch - 24), _hms(t), font=fnt, fill="white", stroke_width=2, stroke_fill="black")
        ctx.out_bytes(f"{f.stem}-contact-sheet.{fmt}", save_image(sheet, fmt, 90))
        return {"note": ctx.tr(f"Кадров на листе: {len(shots)}", f"Frames on the sheet: {len(shots)}")}
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_STORED) as z:
        for t, img in shots:
            z.writestr(f"{f.stem}-{_hms(t).replace(':', '-').replace('.', '_')}.{fmt}", save_image(img, fmt, 90))
    ctx.out_bytes(f"{f.stem}-frames.zip", zbuf.getvalue())
    return {"note": ctx.tr(f"Кадров: {len(shots)}", f"Frames: {len(shots)}")}

@utility("video.info", "video", "Информация о видео", "Video info",
         "Контейнер, длительность, разрешение, кадры в секунду, кодеки, битрейт, дорожки звука и субтитров, поворот.",
         "Container, duration, resolution, frame rate, codecs, bitrate, audio and subtitle tracks, rotation.",
         input="file", accept=VIDEO_ACCEPT, ai_default=True, keys="информация видео разрешение fps кодек битрейт video info")
def video_info(ctx):
    f = ctx.file()
    c = _open(f.path())
    try:
        info = [[ctx.tr("Файл", "File"), f"{f.name} ({human_size(f.size, ctx.en)})"], [ctx.tr("Контейнер", "Container"), c.format.long_name or c.format.name],
                [ctx.tr("Длительность", "Duration"), _hms(_duration(c))], [ctx.tr("Общий битрейт", "Overall bitrate"), f"{(c.bit_rate or 0) // 1000} kbps"]]
        for s in c.streams:
            cc = s.codec_context
            if s.type == "video":
                fps = float(s.average_rate or s.guessed_rate or 0)
                info.append([ctx.tr("Видео", "Video") + f" #{s.index}", f"{cc.name}, {cc.width}×{cc.height}, {fps:.3g} fps, {(cc.bit_rate or 0) // 1000} kbps, {cc.pix_fmt or ''}"])
            elif s.type == "audio":
                lang = (s.metadata or {}).get("language", "")
                info.append([ctx.tr("Звук", "Audio") + f" #{s.index}", f"{cc.name}, {cc.sample_rate} Hz, {_channels(s)} ch, {(cc.bit_rate or 0) // 1000} kbps {lang}".strip()])
            elif s.type == "subtitle":
                info.append([ctx.tr("Субтитры", "Subtitles") + f" #{s.index}", f"{cc.name} {(s.metadata or {}).get('language', '')}".strip()])
        for k, v in list((c.metadata or {}).items())[:12]:
            if k not in ("major_brand", "minor_version", "compatible_brands"):
                info.append([k, str(v)[:160]])
    finally:
        c.close()
    return {"info": info}

@utility("video.compress", "video", "Сжать видео до размера", "Compress video to a size",
         "Пересчитывает битрейт так, чтобы файл влез в лимит (например, 25 МБ для почты или мессенджера).",
         "Recomputes the bitrate so the file fits a limit (e.g. 25 MB for mail or messengers).",
         input="file", accept=VIDEO_ACCEPT,
         opts=[O("mb", "number", "Целевой размер, МБ", "Target size, MB", 25, min=1, max=20000),
               O("height", "choice", "Не выше", "Max height", "720", choices=[("0", "Как в исходнике", "Keep"), ("1080", "1080p", "1080p"), ("720", "720p", "720p"), ("480", "480p", "480p"), ("360", "360p", "360p")]),
               O("abitrate", "int", "Звук, кбит/с", "Audio, kbps", 96, min=32, max=320)],
         ai_default=True, keys="сжать видео уменьшить размер вес compress video 25 мб telegram")
def video_compress(ctx):
    f = ctx.file()
    c = _open(f.path())
    dur = _duration(c)
    c.close()
    if not dur:
        raise UtilError("Не удалось узнать длительность видео", "Cannot read the video duration")
    abr = int(ctx.o["abitrate"]) * 1000
    total = float(ctx.o["mb"]) * 8 * 1024 * 1024 * 0.95
    vbr = int(total / dur - abr)
    if vbr < 60_000:
        raise UtilError("Слишком маленький размер для такой длины — увеличьте лимит или укоротите видео", "The size is too small for this length — raise the limit or trim the video")
    dst = ctx.out_path(f"{f.stem}-{int(float(ctx.o['mb']))}MB.mp4")
    info = transcode_video(f.path(), dst, "mp4", height=int(ctx.o["height"]), vbitrate=vbr, abitrate=abr)
    return {"note": f"{human_size(f.size, ctx.en)} → {human_size(dst.stat().st_size, ctx.en)}, {info['width']}×{info['height']}, {vbr // 1000} kbps"}

@utility("video.rotate", "video", "Повернуть / отразить видео", "Rotate / flip video",
         "Поворот на 90°/180° и зеркальное отражение — для роликов, снятых «на боку».", "Rotate by 90°/180° and mirror — for clips filmed sideways.",
         input="file", accept=VIDEO_ACCEPT,
         opts=[O("op", "choice", "Действие", "Action", "cw", choices=[("cw", "90° по часовой", "90° clockwise"), ("ccw", "90° против часовой", "90° counter-clockwise"),
                                                                   ("180", "180°", "180°"), ("hflip", "Отразить по горизонтали", "Flip horizontally"), ("vflip", "Отразить по вертикали", "Flip vertically")])] + _vopts()[2:],
         keys="повернуть видео перевернуть отразить rotate flip video")
def video_rotate(ctx):
    f = ctx.file()
    vf = {"cw": "transpose=clock", "ccw": "transpose=cclock", "180": "hflip,vflip", "hflip": "hflip", "vflip": "vflip"}[ctx.o["op"]]
    dst = ctx.out_path(f"{f.stem}-rotated.mp4")
    transcode_video(f.path(), dst, "mp4", crf=int(ctx.o["crf"]), vf=vf)
    return ""

@utility("video.slideshow", "video", "Слайд-шоу из фото", "Slideshow from photos",
         "Делает MP4 из картинок: N секунд на кадр, плавная смена; аудиофайл в списке станет музыкой.",
         "Makes an MP4 from pictures: N seconds per slide, crossfades; an audio file in the list becomes the music.",
         input="files", accept="image/*," + AUDIO_ACCEPT,
         opts=[O("seconds", "number", "Секунд на слайд", "Seconds per slide", 3, min=0.2, max=60), O("fade", "number", "Переход, с", "Crossfade, s", 0.5, min=0, max=5),
               O("width", "int", "Ширина", "Width", 1280, min=160, max=3840), O("height", "int", "Высота", "Height", 720, min=120, max=2160)],
         keys="слайд-шоу видео из фото slideshow photos to video")
def video_slideshow(ctx):
    av = _av()
    from PIL import Image, ImageOps
    import numpy as np
    from .core import kind_of
    images = [f for f in ctx.files if kind_of(f.name) == "image"]
    audio = [f for f in ctx.files if kind_of(f.name) in ("audio", "video")]
    if not images:
        raise UtilError("Добавьте картинки", "Add pictures")
    W, H = _even(int(ctx.o["width"])), _even(int(ctx.o["height"]))
    fps = 25
    per, fade = float(ctx.o["seconds"]), min(float(ctx.o["fade"]), float(ctx.o["seconds"]) / 2)
    slides = []
    for f in images:
        img = Image.open(io.BytesIO(f.data))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((W, H), Image.LANCZOS)
        canvas = Image.new("RGB", (W, H), (0, 0, 0))
        canvas.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
        slides.append(np.asarray(canvas))
    dst = ctx.out_path("slideshow.mp4")
    out = av.open(str(dst), "w", format="mp4")
    vs = out.add_stream(_pick_encoder(av, "libx264"), rate=fps)
    vs.width, vs.height, vs.pix_fmt = W, H, "yuv420p"
    vs.codec_context.options = {"preset": "veryfast", "crf": "22", "tune": "stillimage"} if vs.codec_context.name == "libx264" else {}
    n_per, n_fade = int(per * fps), int(fade * fps)
    idx = 0
    for i, arr in enumerate(slides):
        nxt = slides[i + 1] if i + 1 < len(slides) else None
        for k in range(n_per):
            if nxt is not None and n_fade and k >= n_per - n_fade:
                a = (k - (n_per - n_fade) + 1) / (n_fade + 1)
                frame_arr = (arr * (1 - a) + nxt * a).astype(np.uint8)
            else:
                frame_arr = arr
            frame = av.VideoFrame.from_ndarray(frame_arr, format="rgb24").reformat(format="yuv420p")
            frame.pts = idx
            frame.time_base = Fraction(1, fps)
            idx += 1
            for p in vs.encode(frame):
                out.mux(p)
    for p in vs.encode(None):
        out.mux(p)
    total = idx / fps
    out.close()
    if audio:
        tmp = dst.with_name(dst.stem + "-music.mp4")
        _mux_audio(dst, audio[0].path(), tmp, total)
        dst.unlink()
        tmp.rename(dst)
    return {"note": ctx.tr(f"Слайдов: {len(slides)}, {total:.1f} с", f"Slides: {len(slides)}, {total:.1f} s")}

def _mux_audio(video: Path, audio: Path, dst: Path, limit: Optional[float] = None, mix_original: bool = False) -> None:
    av = _av()
    vin = _open(video)
    try:
        out = av.open(str(dst), "w", format="mp4")
        vst = vin.streams.video[0]
        ovs = out.add_stream_from_template(vst)
        dur = limit or _duration(vin, vst) or _duration(vin)
        rate, ch, _ = audio_meta(audio)
        oas = out.add_stream("aac", rate=rate, layout=_layout_name(ch))
        oas.bit_rate = 160000
        first = None
        for packet in vin.demux(vst):
            if packet.dts is None:
                continue
            if first is None:
                first = packet.dts
            packet.dts -= first
            if packet.pts is not None:
                packet.pts -= first
            packet.stream = ovs
            out.mux(packet)
        samples = 0
        for block, r, c in read_audio(audio, rate, ch, 0, dur):
            frame = av.AudioFrame.from_ndarray(block.astype("float32"), format="fltp", layout=_layout_name(ch))
            frame.sample_rate = rate
            frame.pts = samples
            frame.time_base = Fraction(1, rate)
            samples += block.shape[1]
            for p in oas.encode(frame):
                out.mux(p)
        for p in oas.encode(None):
            out.mux(p)
        out.close()
    finally:
        vin.close()

@utility("video.audio", "video", "Заменить звук в видео", "Replace video soundtrack",
         "Подкладывает музыку или озвучку вместо оригинального звука (видео не перекодируется). Первый файл — видео, второй — звук.",
         "Puts music or a voice-over in place of the original sound (no video re-encoding). First file = video, second = audio.",
         input="files", accept=VIDEO_ACCEPT + "," + AUDIO_ACCEPT, keys="заменить звук музыка на видео озвучка replace audio soundtrack music")
def video_audio(ctx):
    if len(ctx.files) < 2:
        raise UtilError("Добавьте видео и аудиофайл", "Add a video and an audio file")
    v, a = ctx.files[0], ctx.files[1]
    _mux_audio(v.path(), a.path(), ctx.out_path(f"{v.stem}-new-audio.mp4"))
    return ""
