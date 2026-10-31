"""Duplicate finder speed-ups: parallel single-pass hashing, hash cache, hard links, skip dirs.

Run: python -m pytest -q tests/memufiles_dupes_test.py
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.memufiles.fsops import FsError, hash_file_both, hash_head  # noqa: E402
from app.memufiles.jobs import Job, Jobs  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path / "data"))
    yield tmp_path


def run_dupes(base, cache_path=None, **extra):
    args = {"path": str(base), "wait": 10}
    if cache_path is not None:
        args["cache_path"] = str(cache_path)
    if extra:
        args.update(extra)
    job = Jobs().start("dupes", args)
    assert job.status == "done", job.error
    return job


def test_groups_and_order(tmp_path):
    base = tmp_path / "tree"
    (base / "a").mkdir(parents=True)
    (base / "b").mkdir()
    big = os.urandom(200_000)
    (base / "a" / "one.bin").write_bytes(big)
    (base / "b" / "two.bin").write_bytes(big)
    (base / "b" / "three.bin").write_bytes(big)
    (base / "a" / "small.txt").write_text("hello", encoding="utf-8")
    (base / "b" / "small2.txt").write_text("hello", encoding="utf-8")
    (base / "a" / "unique.bin").write_bytes(os.urandom(200_000))
    job = run_dupes(base, tmp_path / "cache.sqlite3", workers=1)
    groups = job.result["groups"]
    assert [g["size"] for g in groups] == [200_000, 5]
    assert [len(g["files"]) for g in groups] == [3, 2]
    assert groups[0]["wasted"] == 200_000 * 2
    assert job.result["scanned"] == 6
    # Oldest first inside a group, and the same answer on a repeat run.
    assert groups[0]["files"][0]["path"].endswith("one.bin")
    job2 = run_dupes(base, tmp_path / "cache.sqlite3", workers=2)
    assert job2.result["groups"] == groups


def test_skip_dirs_are_not_searched(tmp_path):
    base = tmp_path / "tree"
    content = b"same" * 1000
    for skipped in ("node_modules", ".git", ".venv", "venv", "dist", "build",
                    "__pycache__", ".cache", ".idea", ".vscode", "target", ".hidden"):
        folder = base / skipped
        folder.mkdir(parents=True)
        (folder / "copy.bin").write_bytes(content)
    (base / "keep").mkdir(parents=True)
    (base / "keep" / "copy.bin").write_bytes(content)
    (base / "keep" / "other.bin").write_bytes(content)
    job = run_dupes(base, tmp_path / "cache.sqlite3")
    assert len(job.result["groups"]) == 1
    assert sorted(f["path"] for f in job.result["groups"][0]["files"]) == [
        str(base / "keep" / "copy.bin"), str(base / "keep" / "other.bin")]


def test_hard_links_hashed_once_and_not_grouped_alone(tmp_path):
    base = tmp_path / "tree"
    base.mkdir()
    data = os.urandom(150_000)
    (base / "orig.bin").write_bytes(data)
    os.link(base / "orig.bin", base / "link.bin")
    job = run_dupes(base, tmp_path / "cache.sqlite3")
    assert job.result["groups"] == []
    (base / "copy.bin").write_bytes(data)
    job = run_dupes(base, tmp_path / "cache.sqlite3")
    assert len(job.result["groups"]) == 1
    group = job.result["groups"][0]
    assert len(group["files"]) == 3
    # Two inodes only: the wasted space is one copy, not two.
    assert group["wasted"] == 150_000


def test_hash_file_both_single_pass(tmp_path, monkeypatch):
    target = tmp_path / "big.bin"
    data = os.urandom(300_000)
    target.write_bytes(data)
    opens = []

    real_open = open

    def counting_open(path, *args, **kwargs):
        if os.fspath(path) == os.fspath(target):
            opens.append(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", counting_open)
    full, prefix = hash_file_both(str(target))
    assert opens == [str(target)]
    assert full == hashlib.sha256(data).hexdigest()
    assert prefix == hashlib.sha256(data[:65536]).hexdigest()
    assert hash_head(str(target)) == prefix


def test_cache_reused_and_small_files_not_stored(tmp_path, monkeypatch):
    from app.memufiles import jobs as jobs_mod

    base = tmp_path / "tree"
    base.mkdir()
    big = os.urandom(2 * 1024 * 1024)
    (base / "one.bin").write_bytes(big)
    (base / "two.bin").write_bytes(big)
    other = bytearray(big)
    other[1000] = (other[1000] + 1) % 256  # inside the 64 KiB head, so it never reaches the full hash
    (base / "other.bin").write_bytes(bytes(other))
    (base / "tiny1.txt").write_text("same", encoding="utf-8")
    (base / "tiny2.txt").write_text("same", encoding="utf-8")
    cache_path = tmp_path / "cache.sqlite3"
    run_dupes(base, cache_path)
    from app.memufiles.cache import HashCache
    cache = HashCache(cache_path)
    assert cache.count() == 2  # the duplicate pair only
    cache.close()

    heads = []
    boths = []
    real_head, real_both = jobs_mod.hash_head, jobs_mod.hash_file_both
    monkeypatch.setattr(jobs_mod, "hash_head", lambda p, **kw: (heads.append(p), real_head(p, **kw))[1])
    monkeypatch.setattr(jobs_mod, "hash_file_both", lambda p, **kw: (boths.append(p), real_both(p, **kw))[1])
    run_dupes(base, cache_path)
    assert boths == []
    # The uncached "other.bin" and the two tiny files are re-headed; the
    # cached pair is not touched at all.
    assert sorted(os.path.basename(h) for h in heads) == ["other.bin", "tiny1.txt", "tiny2.txt"]


def test_cache_invalidated_when_file_changes(tmp_path):
    base = tmp_path / "tree"
    base.mkdir()
    data = os.urandom(2 * 1024 * 1024)
    (base / "one.bin").write_bytes(data)
    (base / "two.bin").write_bytes(data)
    cache_path = tmp_path / "cache.sqlite3"
    assert len(run_dupes(base, cache_path).result["groups"]) == 1
    changed = bytearray(data)
    changed[-1] = (changed[-1] + 1) % 256
    (base / "two.bin").write_bytes(bytes(changed))
    assert run_dupes(base, cache_path).result["groups"] == []


def test_cancel_before_and_during(tmp_path, monkeypatch):
    from app.memufiles import jobs as jobs_mod

    base = tmp_path / "tree"
    base.mkdir()
    (base / "a.bin").write_bytes(os.urandom(100_000))
    (base / "b.bin").write_bytes(os.urandom(100_000))
    job = Job("dupes", {"path": str(base)})
    job.cancel.set()
    with pytest.raises(FsError) as exc:
        Jobs()._dupes(job)
    assert exc.value.code == "cancelled"

    real_head = jobs_mod.hash_head

    def cancelling(path, **kwargs):
        job2.cancel.set()
        return real_head(path, **kwargs)

    monkeypatch.setattr(jobs_mod, "hash_head", cancelling)
    job2 = Job("dupes", {"path": str(base), "workers": 1, "cache_path": ":memory:"})
    with pytest.raises(FsError) as exc2:
        Jobs()._dupes(job2)
    assert exc2.value.code == "cancelled"


def test_default_cache_location_under_data_dir(tmp_path, monkeypatch):
    base = tmp_path / "tree"
    base.mkdir()
    data = os.urandom(2 * 1024 * 1024)
    (base / "one.bin").write_bytes(data)
    (base / "two.bin").write_bytes(data)
    run_dupes(base)
    assert (tmp_path / "data" / "memufiles" / "hash-cache.sqlite3").exists()
    assert run_dupes(base).result["groups"][0]["size"] == 2 * 1024 * 1024
