"""Release metadata shared by update checking and installer verification."""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import httpx

REPOSITORY = "MemuAI/MemuAI"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_INSTALLER_SIZE = 1024 * 1024 * 1024
_VERSION = r"\d{1,6}(?:\.\d{1,6}){1,3}"
_SETUP = re.compile(rf"^MemuAI-Setup-([vV]?{_VERSION})\.exe$", re.I)


class _ReleaseAssets(HTMLParser):
    """Read download links and their corresponding digests from GitHub's asset rows."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.assets = []
        self.row = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "li":
            self.row = {}
        if tag == "a":
            href = values.get("href") or ""
            if href.startswith(f"/{REPOSITORY}/releases/download/"):
                self.row.update(name=unquote(href.rsplit("/", 1)[-1]),
                                browser_download_url="https://github.com" + href)
        if tag == "clipboard-copy" and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", values.get("value") or ""):
            self.row["digest"] = values["value"]

    def handle_endtag(self, tag):
        if tag == "li" and self.row.get("name"):
            self.assets.append(self.row)
            self.row = {}


async def _github_page_payload(client) -> dict:
    response = await client.get(RELEASES_URL + "/latest", timeout=15, follow_redirects=True)
    response.raise_for_status()
    parsed = urlsplit(str(response.url))
    prefix = f"/{REPOSITORY}/releases/tag/"
    if parsed.scheme != "https" or parsed.hostname != "github.com" or not parsed.path.startswith(prefix):
        raise ValueError("Не удалось открыть последний релиз / Cannot locate latest release")
    tag = unquote(parsed.path[len(prefix):])
    listing = await client.get(RELEASES_URL + "/expanded_assets/" + quote(tag, safe=""), timeout=15, follow_redirects=True)
    listing.raise_for_status()
    https_url(str(listing.url))
    parser = _ReleaseAssets()
    parser.feed(listing.text)
    # A rate-limit fallback must not downgrade verification to an unchecked exe.
    assets = [a for a in parser.assets if a.get("digest")]
    return {"tag_name": tag, "html_url": str(response.url), "assets": assets}


async def fetch_payload(client, source: str) -> dict:
    https_url(source)
    try:
        response = await client.get(source, headers={"Accept": "application/vnd.github+json"},
                                    timeout=15, follow_redirects=True)
    except httpx.RequestError:
        if source == LATEST_URL:
            return await _github_page_payload(client)
        raise
    https_url(str(response.url))
    if source == LATEST_URL and (response.status_code in (403, 429) or response.status_code >= 500):
        return await _github_page_payload(client)
    response.raise_for_status()
    decoded = response.json()
    if not isinstance(decoded, dict):
        raise ValueError("Некорректный ответ сервера обновлений / Invalid update response")
    return decoded


def version_number(value: object) -> tuple[int, ...] | None:
    text = str(value or "").strip()
    if not re.fullmatch(rf"[vV]?{_VERSION}", text):
        return None
    parts = tuple(int(p) for p in text.lstrip("vV").split("."))
    return parts + (0,) * (4 - len(parts))


def https_url(value: object) -> str:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Некорректный HTTPS-адрес обновления / Invalid update HTTPS URL")
    return text


def release_offer(payload: dict, current: str) -> dict:
    """Read GitHub Releases or the existing custom JSON manifest format.

    Only the full suite installer is eligible: a Proxy-only exe must never
    replace MemuAI. Legacy tags such as 'MemuAI' use the installer filename.
    """
    result = {"current_version": current, "latest_version": current,
              "update_available": False, "release_url": RELEASES_URL, "status": "current"}
    if payload.get("draft") or payload.get("prerelease"):
        return result
    assets = payload.get("assets") or []
    if not isinstance(assets, list):
        raise ValueError("Некорректный список файлов релиза / Invalid release asset list")
    setups = [(a, _SETUP.fullmatch(str(a.get("name") or "")))
              for a in assets if isinstance(a, dict) and a.get("state", "uploaded") == "uploaded"]
    setups = [(a, match.group(1).lstrip("vV")) for a, match in setups if match]
    latest = next((str(payload[k]).strip().lstrip("vV") for k in ("version", "tag_name")
                   if version_number(payload.get(k)) is not None), "")
    if not latest and setups:
        latest = max((v for _, v in setups), key=version_number)
    if not latest:
        raise ValueError("Не удалось определить версию релиза / Cannot determine release version")
    result["latest_version"] = latest
    if payload.get("html_url"):
        result["release_url"] = https_url(payload["html_url"])
    if version_number(latest) <= (version_number(current) or (0, 0, 0, 0)):
        return result
    asset = next((a for a, v in setups if version_number(v) == version_number(latest)), {})
    url = asset.get("browser_download_url") or payload.get("download_url")
    if not url:
        result["status"] = "missing_installer"
        return result
    digest = str(asset.get("digest") or payload.get("sha256") or "").removeprefix("sha256:").lower()
    if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Некорректная контрольная сумма / Invalid installer checksum")
    size = int(asset.get("size") or payload.get("size") or 0)
    if size < 0 or size > MAX_INSTALLER_SIZE:
        raise ValueError("Недопустимый размер установщика / Invalid installer size")
    result.update(status="available", update_available=True, download_url=https_url(url),
                  sha256=digest, size=size)
    return result


def verify_installer(path: Path, size: int = 0, sha256: str = "") -> None:
    actual = path.stat().st_size
    if not actual or actual > MAX_INSTALLER_SIZE or (size and actual != size):
        raise ValueError("Установщик скачан не полностью / Incomplete installer download")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        if handle.read(2) != b"MZ":
            raise ValueError("Скачанный файл не является EXE / Download is not an executable")
        handle.seek(0)
        if sha256:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    if sha256 and digest.hexdigest() != sha256:
        raise ValueError("Контрольная сумма установщика не совпадает / Installer checksum mismatch")
