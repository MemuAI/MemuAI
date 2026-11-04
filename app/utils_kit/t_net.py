# -*- coding: utf-8 -*-
"""Network utilities: DNS (DNS-over-HTTPS), public IP and IP geo, HTTP check with security headers, TLS certificate,
WHOIS, ping, port check, local network scan, subnet calculator, speed test, exchange rates and Wake-on-LAN.

Probing other hosts (ports, local network, Wake-on-LAN) is marked ``danger``: the chat asks the user first."""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import ssl
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .core import O, UtilError, as_float, as_int, human_size, table, utility

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 MemuUtils"
DNS_TYPES = {"A": 1, "NS": 2, "CNAME": 5, "SOA": 6, "PTR": 12, "MX": 15, "TXT": 16, "AAAA": 28, "SRV": 33, "CAA": 257, "DS": 43, "DNSKEY": 48, "HTTPS": 65}
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

def _client(timeout: float = 12.0, follow: bool = True):
    try:
        import httpx
    except ImportError:
        raise UtilError("Библиотека httpx недоступна", "httpx is not available")
    return httpx.Client(timeout=timeout, follow_redirects=follow, headers={"User-Agent": UA}, trust_env=True)

def _net_error(exc: Exception) -> UtilError:
    return UtilError(f"Нет связи: {exc}", f"Network error: {exc}")

def _host_of(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise UtilError("Введите домен или адрес", "Enter a domain or an address")
    if "://" in text:
        text = urlparse(text).hostname or ""
    text = text.split("/")[0].strip().strip(".")
    if text.count(":") == 1 and not text.startswith("["):
        text = text.split(":")[0]
    text = text.strip("[]")
    try:
        text = text.encode("idna").decode("ascii") if not _is_ip(text) else text
    except UnicodeError:
        pass
    if not re.fullmatch(r"[A-Za-z0-9.:_-]+", text):
        raise UtilError(f"Непохоже на домен: «{value}»", f"Does not look like a domain: “{value}”")
    return text.lower()

def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False

def doh_query(name: str, rtype: str = "A") -> List[Dict[str, Any]]:
    """DNS over HTTPS (Cloudflare, then Google): ``[{"name", "type", "ttl", "data"}]``."""
    last: Optional[Exception] = None
    for url in ("https://cloudflare-dns.com/dns-query", "https://dns.google/resolve"):
        try:
            with _client(8.0) as client:
                resp = client.get(url, params={"name": name, "type": rtype}, headers={"Accept": "application/dns-json"})
                resp.raise_for_status()
                data = resp.json()
            codes = {v: k for k, v in DNS_TYPES.items()}
            return [{"name": a.get("name", "").rstrip("."), "type": codes.get(a.get("type"), str(a.get("type"))), "ttl": a.get("TTL"),
                     "data": str(a.get("data", "")).strip()} for a in data.get("Answer") or []]
        except Exception as exc:  # noqa: BLE001 - try the next resolver
            last = exc
    raise _net_error(last or RuntimeError("DNS"))

@utility("net.dns", "net", "DNS-записи домена", "Domain DNS records",
         "Показывает A, AAAA, MX, TXT (SPF, DMARC), NS, CNAME, CAA, SOA и обратную запись IP — через DNS-over-HTTPS, без кэша провайдера.",
         "Shows A, AAAA, MX, TXT (SPF, DMARC), NS, CNAME, CAA, SOA and reverse IP records — over DNS-over-HTTPS, bypassing the ISP cache.",
         input="text", ai_default=True, placeholder="example.com",
         opts=[O("type", "choice", "Тип", "Type", "ALL", choices=[("ALL", "Основные (A, AAAA, MX, TXT, NS, CNAME)", "Main (A, AAAA, MX, TXT, NS, CNAME)")] + [(t, t, t) for t in DNS_TYPES] + [("MAIL", "Почта: MX, SPF, DMARC, DKIM", "Mail: MX, SPF, DMARC, DKIM")])],
         keys="dns записи домен mx txt spf dmarc ns nslookup dig резолв")
def net_dns(ctx):
    raw = ctx.need_text("Введите домен", "Enter a domain").strip().split()[0]
    host = _host_of(raw)
    rtype = ctx.o["type"]
    if _is_ip(host):
        rev = ipaddress.ip_address(host).reverse_pointer
        rows = [[r["name"], r["type"], str(r["ttl"]), r["data"]] for r in doh_query(rev, "PTR")]
        return {"table": table([ctx.tr("Имя", "Name"), ctx.tr("Тип", "Type"), "TTL", ctx.tr("Значение", "Value")], rows)} if rows else ctx.tr("Обратной записи нет", "No reverse record")
    types = ["A", "AAAA", "MX", "TXT", "NS", "CNAME"] if rtype == "ALL" else ["MX", "TXT"] if rtype == "MAIL" else [rtype]
    queries = [(host, t) for t in types]
    if rtype == "MAIL":
        queries += [(f"_dmarc.{host}", "TXT")] + [(f"{s}._domainkey.{host}", "TXT") for s in ("default", "google", "selector1", "selector2", "mail", "dkim", "k1")]
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda q: (q, _safe_doh(*q)), queries))
    rows = []
    seen = set()
    for (name, t), answers in results:
        for a in answers:
            if rtype == "MAIL" and a["type"] == "TXT" and name == host and "spf" not in a["data"].lower():
                continue
            key = (a["name"], a["type"], a["data"])
            if key not in seen:
                seen.add(key)
                rows.append([a["name"], a["type"], str(a["ttl"]), a["data"]])
    if not rows:
        return ctx.tr(f"Записей для {host} не найдено", f"No records found for {host}")
    note = ""
    if rtype == "MAIL":
        has = lambda word: any(word in r[3].lower() for r in rows)  # noqa: E731
        note = " · ".join([("SPF ✓" if has("v=spf1") else ctx.tr("SPF нет", "no SPF")), ("DMARC ✓" if has("v=dmarc1") else ctx.tr("DMARC нет", "no DMARC")),
                           ("DKIM ✓" if has("v=dkim1") or has("k=rsa") else ctx.tr("DKIM не найден среди типовых селекторов", "DKIM not found among common selectors"))])
    return {"table": table([ctx.tr("Имя", "Name"), ctx.tr("Тип", "Type"), "TTL", ctx.tr("Значение", "Value")], rows), "note": note}

def _safe_doh(name: str, rtype: str) -> List[Dict[str, Any]]:
    try:
        return doh_query(name, rtype)
    except UtilError:
        return []

@utility("net.ip", "net", "Мой IP и геолокация IP", "My IP and IP location",
         "Показывает ваш внешний IP (или данные любого IP/домена): страна, город, провайдер, часовой пояс, прокси/VPN.",
         "Shows your public IP (or any IP/domain): country, city, ISP, time zone, proxy/VPN.",
         input="text", ai_default=True, placeholder="пусто — ваш IP, или 8.8.8.8", placeholder_en="empty = your IP, or 8.8.8.8",
         keys="мой ip адрес внешний геолокация провайдер whatismyip ip address location")
def net_ip(ctx):
    target = str(ctx.text or "").strip()
    if target:
        host = _host_of(target)
        if not _is_ip(host):
            answers = [a for a in doh_query(host, "A") if a["type"] == "A"]
            if not answers:
                raise UtilError(f"У {host} нет IPv4-адреса", f"{host} has no IPv4 address")
            host = answers[0]["data"]
        target = host
    try:
        with _client(10.0) as client:
            data = client.get(f"https://ipwho.is/{target}").json()
            if not data.get("success", True) and not target:
                data = {}
            if not data or data.get("success") is False:
                alt = client.get(f"https://ipapi.co/{target + '/' if target else ''}json/").json()
                data = {"ip": alt.get("ip"), "country": alt.get("country_name"), "region": alt.get("region"), "city": alt.get("city"),
                        "connection": {"isp": alt.get("org"), "asn": alt.get("asn")}, "timezone": {"id": alt.get("timezone")},
                        "latitude": alt.get("latitude"), "longitude": alt.get("longitude")}
    except Exception as exc:  # noqa: BLE001
        raise _net_error(exc)
    conn = data.get("connection") or {}
    tz = data.get("timezone") or {}
    rows = [["IP", str(data.get("ip") or target)], [ctx.tr("Тип", "Type"), str(data.get("type") or "")],
            [ctx.tr("Страна", "Country"), f"{data.get('flag', {}).get('emoji', '') if isinstance(data.get('flag'), dict) else ''} {data.get('country') or ''}".strip()],
            [ctx.tr("Регион", "Region"), str(data.get("region") or "")], [ctx.tr("Город", "City"), str(data.get("city") or "")],
            [ctx.tr("Провайдер", "ISP"), str(conn.get("isp") or conn.get("org") or "")], ["ASN", str(conn.get("asn") or "")],
            [ctx.tr("Часовой пояс", "Time zone"), str(tz.get("id") or "")]]
    if data.get("latitude"):
        rows.append([ctx.tr("Координаты", "Coordinates"), f"{data.get('latitude')}, {data.get('longitude')}"])
    if data.get("security"):
        sec = data["security"]
        flags = [k for k in ("proxy", "vpn", "tor", "hosting") if sec.get(k)]
        rows.append([ctx.tr("Прокси/VPN", "Proxy/VPN"), ", ".join(flags) or ctx.tr("не обнаружено", "not detected")])
    return {"info": [r for r in rows if r[1].strip()], "note": ctx.tr("Геолокация по IP приблизительная", "IP geolocation is approximate")}

SEC_HEADERS = [("strict-transport-security", "HSTS"), ("content-security-policy", "CSP"), ("x-frame-options", "X-Frame-Options"),
               ("x-content-type-options", "X-Content-Type-Options"), ("referrer-policy", "Referrer-Policy"), ("permissions-policy", "Permissions-Policy")]

@utility("net.http", "net", "Проверка сайта: ответ, редиректы, заголовки", "Website check: status, redirects, headers",
         "Открывает адрес и показывает код ответа, цепочку редиректов (раскрывает короткие ссылки), время, размер, сервер и оценку заголовков безопасности.",
         "Opens a URL and shows the status, the redirect chain (expands short links), timing, size, server and a security headers grade.",
         input="text", ai_default=True, placeholder="https://example.com",
         opts=[O("method", "choice", "Метод", "Method", "GET", choices=[("GET", "GET", "GET"), ("HEAD", "HEAD", "HEAD")]),
               O("headers", "bool", "Показать все заголовки", "Show all headers", False)],
         keys="проверить сайт доступность код ответа редирект короткая ссылка заголовки http status headers redirect unshorten")
def net_http(ctx):
    url = ctx.need_text("Введите адрес", "Enter a URL").strip().split()[0]
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    hops = []
    try:
        with _client(15.0, follow=False) as client:
            current = url
            started = time.perf_counter()
            for _ in range(12):
                t0 = time.perf_counter()
                resp = client.request(ctx.o["method"], current)
                hops.append([str(resp.status_code), current, f"{(time.perf_counter() - t0) * 1000:.0f} {ctx.tr('мс', 'ms')}"])
                if resp.is_redirect and resp.headers.get("location"):
                    current = str(resp.next_request.url) if resp.next_request is not None else resp.headers["location"]
                    continue
                break
            total = time.perf_counter() - started
            body = resp.content if ctx.o["method"] == "GET" else b""
    except Exception as exc:  # noqa: BLE001
        raise _net_error(exc)
    headers = {k.lower(): v for k, v in resp.headers.items()}
    present = [label for key, label in SEC_HEADERS if key in headers]
    grade = "A+" if len(present) == 6 else "A" if len(present) == 5 else "B" if len(present) == 4 else "C" if len(present) == 3 else "D" if len(present) >= 1 else "F"
    info = [[ctx.tr("Итоговый адрес", "Final URL"), str(resp.url)], [ctx.tr("Код", "Status"), f"{resp.status_code} {resp.reason_phrase}"],
            [ctx.tr("Время всего", "Total time"), f"{total * 1000:.0f} {ctx.tr('мс', 'ms')}"], [ctx.tr("Редиректов", "Redirects"), str(len(hops) - 1)],
            ["HTTP", resp.http_version], [ctx.tr("Сервер", "Server"), headers.get("server", "—")], ["Content-Type", headers.get("content-type", "—")]]
    if body:
        info.append([ctx.tr("Размер", "Size"), human_size(len(body), ctx.en)])
        m = re.search(rb"<title[^>]*>(.*?)</title>", body[:300000], re.I | re.S)
        if m:
            info.append([ctx.tr("Заголовок страницы", "Page title"), re.sub(r"\s+", " ", m.group(1).decode("utf-8", "replace")).strip()[:200]])
    info.append([ctx.tr("Заголовки безопасности", "Security headers"), f"{grade} · " + (", ".join(present) or ctx.tr("нет", "none"))])
    missing = [label for key, label in SEC_HEADERS if key not in headers]
    if missing:
        info.append([ctx.tr("Не хватает", "Missing"), ", ".join(missing)])
    result: Dict[str, Any] = {"info": info}
    if len(hops) > 1:
        result["table"] = table([ctx.tr("Код", "Status"), ctx.tr("Адрес", "URL"), ctx.tr("Время", "Time")], hops)
    if ctx.o["headers"]:
        result["text"] = "\n".join(f"{k}: {v}" for k, v in resp.headers.items())
    return result

@utility("net.ssl", "net", "SSL-сертификат сайта", "Website TLS certificate",
         "Проверяет сертификат: кому и кем выдан, домены (SAN), срок действия и сколько дней осталось, версия TLS и шифр.",
         "Checks the certificate: issued to and by, domains (SAN), validity and days left, TLS version and cipher.",
         input="text", ai_default=True, placeholder="example.com",
         opts=[O("port", "int", "Порт", "Port", 443, min=1, max=65535)], keys="ssl tls сертификат https срок действия certificate expiry")
def net_ssl(ctx):
    host = _host_of(ctx.need_text("Введите домен", "Enter a domain").strip().split()[0])
    port = ctx.o["port"]
    verified, error = True, ""
    cert: Dict[str, Any] = {}
    version = cipher = ""
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert() or {}
                version, cipher = tls.version() or "", (tls.cipher() or ("",))[0]
    except ssl.SSLCertVerificationError as exc:
        verified, error = False, exc.verify_message or str(exc)
    except (OSError, ssl.SSLError) as exc:
        raise _net_error(exc)
    if not cert:
        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            with socket.create_connection((host, port), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=host) as tls:
                    der = tls.getpeercert(binary_form=True)
                    version, cipher = tls.version() or "", (tls.cipher() or ("",))[0]
            cert = _decode_der(der, ctx)
        except Exception as exc:  # noqa: BLE001
            raise _net_error(exc)

    def name(field):
        parts = []
        for rdn in cert.get(field) or ():
            for k, v in rdn:
                if k in ("commonName", "organizationName"):
                    parts.append(v)
        return ", ".join(parts)
    not_after = cert.get("notAfter")
    days = ""
    if not_after:
        try:
            end = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), timezone.utc)
            days = str((end - datetime.now(timezone.utc)).days)
        except (ValueError, OverflowError):
            pass
    sans = [v for k, v in cert.get("subjectAltName") or () if k == "DNS"]
    info = [[ctx.tr("Проверка", "Validation"), ctx.tr("сертификат действителен ✓", "valid ✓") if verified else ctx.tr(f"ошибка: {error}", f"failed: {error}")],
            [ctx.tr("Выдан для", "Issued to"), name("subject")], [ctx.tr("Кем выдан", "Issued by"), name("issuer")],
            [ctx.tr("Действует с", "Valid from"), str(cert.get("notBefore", ""))], [ctx.tr("Действует до", "Valid until"), str(not_after or "")],
            [ctx.tr("Осталось дней", "Days left"), days], ["TLS", f"{version} · {cipher}"],
            [ctx.tr("Домены (SAN)", "Domains (SAN)"), ", ".join(sans[:40]) + (f" … (+{len(sans) - 40})" if len(sans) > 40 else "")]]
    note = ""
    if days and int(days) < 14:
        note = ctx.tr(f"Сертификат истекает через {days} дн. — пора продлевать", f"The certificate expires in {days} days — renew it")
    return {"info": [r for r in info if r[1]], "note": note}

def _decode_der(der: bytes, ctx) -> Dict[str, Any]:
    import os
    import tempfile
    pem = ssl.DER_cert_to_PEM_cert(der)
    fd, path = tempfile.mkstemp(suffix=".pem", dir=str(ctx.work))
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(pem)
        return ssl._ssl._test_decode_cert(path)  # noqa: SLF001 - the stdlib's own decoder; no extra dependency
    except Exception:  # noqa: BLE001
        return {}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

def _whois_raw(server: str, query: str) -> str:
    with socket.create_connection((server, 43), timeout=12) as sock:
        sock.sendall((query + "\r\n").encode("idna" if not query.isascii() else "ascii", "ignore"))
        chunks = []
        sock.settimeout(12)
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
            if sum(map(len, chunks)) > 2_000_000:
                break
    raw = b"".join(chunks)
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")

WHOIS_FIELDS = [(r"(registrar|sponsoring registrar)\s*:", "Регистратор", "Registrar"), (r"(creation date|created|registered on|registration time)\s*:", "Создан", "Created"),
                (r"(registry expiry date|expiration date|expires on|paid-till|expire date|registrar registration expiration date)\s*:", "Оплачен до", "Expires"),
                (r"(updated date|last updated|changed)\s*:", "Обновлён", "Updated"), (r"(name server|nserver|nameservers)\s*:", "DNS-серверы", "Name servers"),
                (r"(domain status|status|state)\s*:", "Статус", "Status"), (r"(registrant organization|org|registrant)\s*:", "Владелец", "Registrant")]

@utility("net.whois", "net", "WHOIS домена или IP", "Domain or IP WHOIS",
         "Кому принадлежит домен или IP: регистратор, даты регистрации и окончания, DNS-серверы, статус; сырой ответ — по кнопке.",
         "Who owns a domain or an IP: registrar, created and expiry dates, name servers, status; the raw answer on request.",
         input="text", ai_default=True, placeholder="example.com",
         opts=[O("raw", "bool", "Показать полный ответ", "Show the full answer", False)], keys="whois домен владелец регистратор срок регистрации domain owner registrar")
def net_whois(ctx):
    host = _host_of(ctx.need_text("Введите домен или IP", "Enter a domain or an IP").strip().split()[0])
    if not _is_ip(host):
        labels = host.split(".")
        if len(labels) > 2 and labels[-2] not in ("co", "com", "net", "org", "gov", "edu", "ac", "msk", "spb"):
            host = ".".join(labels[-2:])
    try:
        iana = _whois_raw("whois.iana.org", host)
        refer = re.search(r"^(?:refer|whois):\s*(\S+)", iana, re.M | re.I)
        raw = _whois_raw(refer.group(1), host) if refer else iana
        second = re.search(r"Registrar WHOIS Server:\s*(\S+)", raw, re.I)
        if second and second.group(1).lower() not in (refer.group(1).lower() if refer else "",):
            try:
                more = _whois_raw(second.group(1).replace("whois://", ""), host)
                if len(more) > 200:
                    raw = raw + "\n\n" + more
            except OSError:
                pass
    except OSError as exc:
        raise _net_error(exc)
    info = [[ctx.tr("Запрос", "Query"), host]]
    for pattern, ru, en in WHOIS_FIELDS:
        values = []
        for line in raw.splitlines():
            m = re.match(rf"^\s*{pattern}\s*(.+)$", line, re.I)
            if m:
                value = m.group(m.lastindex).strip()
                if value and value not in values:
                    values.append(value)
        if values:
            info.append([ctx.tr(ru, en), ", ".join(values[:8])])
    if len(info) == 1 and re.search(r"no match|not found|no entries|available", raw, re.I):
        return {"info": info + [[ctx.tr("Статус", "Status"), ctx.tr("домен свободен или не найден", "free or not found")]]}
    result: Dict[str, Any] = {"info": info}
    if ctx.o["raw"] or len(info) <= 2:
        result["text"] = raw.strip()[:60000]
    return result

def _tcp_ping(host: str, port: int, timeout: float = 2.0) -> Optional[float]:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.perf_counter() - t0) * 1000
    except OSError:
        return None

@utility("net.ping", "net", "Пинг", "Ping",
         "Проверяет, отвечает ли узел, и меряет задержку: системный ping, а если ICMP закрыт — TCP-соединение к 443/80.",
         "Checks whether a host answers and measures the latency: the system ping, or a TCP connect to 443/80 when ICMP is blocked.",
         input="text", ai_default=True, placeholder="ya.ru",
         opts=[O("count", "int", "Попыток", "Attempts", 4, min=1, max=20)], keys="пинг задержка доступность ping latency reachable")
def net_ping(ctx):
    host = _host_of(ctx.need_text("Введите адрес", "Enter a host").strip().split()[0])
    count = ctx.o["count"]
    times: List[float] = []
    method = "ICMP"
    try:
        cmd = ["ping", "-n" if sys.platform == "win32" else "-c", str(count), host]
        if sys.platform != "win32":
            cmd[1:1] = ["-W", "2"]
        else:
            cmd[1:1] = ["-w", "2000"]
        proc = subprocess.run(cmd, capture_output=True, timeout=count * 3 + 5, creationflags=NO_WINDOW)
        out = proc.stdout.decode("cp866" if sys.platform == "win32" else "utf-8", "replace")
        times = [float(x.replace(",", ".")) for x in re.findall(r"(?:time|время)[=<]\s*([\d.,]+)\s*(?:ms|мс)", out, re.I)]
    except (OSError, subprocess.SubprocessError):
        times = []
    if not times:
        method = "TCP"
        for _ in range(count):
            t = _tcp_ping(host, 443) or _tcp_ping(host, 80)
            if t is not None:
                times.append(t)
            time.sleep(0.2)
    if not times:
        return {"info": [[ctx.tr("Узел", "Host"), host], [ctx.tr("Ответ", "Reply"), ctx.tr("нет ответа ✗", "no reply ✗")]]}
    loss = max(0, count - len(times)) * 100 // count
    return {"info": [[ctx.tr("Узел", "Host"), host], [ctx.tr("Метод", "Method"), method], [ctx.tr("Ответов", "Replies"), f"{len(times)}/{count} ({ctx.tr('потери', 'loss')} {loss}%)"],
                     [ctx.tr("Мин / сред / макс", "Min / avg / max"), f"{min(times):.1f} / {sum(times) / len(times):.1f} / {max(times):.1f} {ctx.tr('мс', 'ms')}"]]}

COMMON_PORTS = {21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 80: "HTTP", 110: "POP3", 135: "MS RPC", 139: "NetBIOS", 143: "IMAP", 443: "HTTPS",
                445: "SMB", 465: "SMTPS", 587: "SMTP (submission)", 993: "IMAPS", 995: "POP3S", 1433: "MS SQL", 1521: "Oracle", 1883: "MQTT", 2049: "NFS",
                3000: "Dev server", 3306: "MySQL", 3389: "RDP", 5000: "Dev server", 5432: "PostgreSQL", 5900: "VNC", 6379: "Redis", 8000: "HTTP alt",
                8080: "HTTP proxy", 8443: "HTTPS alt", 8888: "HTTP alt", 9000: "Dev/PHP-FPM", 9200: "Elasticsearch", 11434: "Ollama", 27017: "MongoDB"}

def _ports(spec: str) -> List[int]:
    spec = str(spec or "").strip()
    if not spec or spec.lower() in ("common", "популярные"):
        return sorted(COMMON_PORTS)
    out: List[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        if m:
            a, b = sorted((int(m.group(1)), int(m.group(2))))
            out += list(range(max(1, a), min(65535, b) + 1))
        elif part.isdigit() and 0 < int(part) < 65536:
            out.append(int(part))
        else:
            raise UtilError(f"Непонятный порт «{part}»", f"Bad port “{part}”")
    out = sorted(set(out))
    if len(out) > 2048:
        raise UtilError("Не больше 2048 портов за раз", "At most 2048 ports at a time")
    return out

@utility("net.ports", "net", "Проверка портов", "Port check",
         "Проверяет, какие TCP-порты открыты на узле (ваш сервер, роутер, ПК): популярные или свой список/диапазон.",
         "Checks which TCP ports are open on a host (your server, router, PC): the common ones or your own list/range.",
         input="text", danger=True, placeholder="192.168.1.1",
         opts=[O("ports", "text", "Порты", "Ports", "common", hint="common, 80,443 или 1-1024", hint_en="common, 80,443 or 1-1024"),
               O("timeout", "number", "Таймаут, с", "Timeout, s", 0.8, min=0.1, max=10, step=0.1)],
         keys="порты открытые сканер nmap порт ports scanner open tcp")
def net_ports(ctx):
    host = _host_of(ctx.need_text("Введите адрес", "Enter a host").strip().split()[0])
    ports = _ports(ctx.o["ports"])
    timeout = ctx.o["timeout"]
    try:
        socket.getaddrinfo(host, None)
    except OSError as exc:
        raise _net_error(exc)

    def probe(port):
        t = _tcp_ping(host, port, timeout)
        return port, t
    with ThreadPoolExecutor(min(128, len(ports))) as pool:
        found = [(p, t) for p, t in pool.map(probe, ports) if t is not None]
    if not found:
        return ctx.tr(f"Открытых портов нет (проверено {len(ports)})", f"No open ports (checked {len(ports)})")
    return {"table": table([ctx.tr("Порт", "Port"), ctx.tr("Служба", "Service"), ctx.tr("Отклик", "Response")],
                           [[str(p), COMMON_PORTS.get(p, ""), f"{t:.0f} {ctx.tr('мс', 'ms')}"] for p, t in found]),
            "note": ctx.tr(f"Открыто {len(found)} из {len(ports)}", f"{len(found)} of {len(ports)} open")}

def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"

def _arp_table() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        proc = subprocess.run(["arp", "-a"], capture_output=True, timeout=10, creationflags=NO_WINDOW)
        text = proc.stdout.decode("cp866" if sys.platform == "win32" else "utf-8", "replace")
        for ip, mac in re.findall(r"(\d+\.\d+\.\d+\.\d+)\)?\s+(?:at\s+)?([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})", text):
            out[ip] = mac.replace("-", ":").lower()
    except (OSError, subprocess.SubprocessError):
        pass
    return out

@utility("net.lan", "net", "Устройства в локальной сети", "Devices on the local network",
         "Ищет устройства в вашей сети (/24): IP, имя, MAC и открытые популярные порты — роутер, принтер, NAS, телевизор.",
         "Finds devices on your network (/24): IP, name, MAC and open common ports — router, printer, NAS, TV.",
         input="none", danger=True, opts=[O("subnet", "text", "Подсеть (пусто — своя)", "Subnet (empty = yours)", "")],
         keys="локальная сеть устройства сканер lan wifi роутер принтер network scan devices")
def net_lan(ctx):
    own = _local_ip()
    try:
        net = ipaddress.ip_network(ctx.o["subnet"] or f"{own}/24", strict=False)
    except ValueError as exc:
        raise UtilError(f"Неверная подсеть: {exc}", f"Bad subnet: {exc}")
    if net.num_addresses > 1024:
        raise UtilError("Слишком большая подсеть — не больше /22", "The subnet is too big — /22 at most")
    probe_ports = [80, 443, 22, 445, 139, 554, 631, 8080, 9100, 62078, 5000, 8009]

    def scan(ip):
        ip = str(ip)
        open_ports = []
        for port in probe_ports:
            if _tcp_ping(ip, port, 0.35) is not None:
                open_ports.append(port)
                if len(open_ports) >= 3:
                    break
        return ip, open_ports
    with ThreadPoolExecutor(96) as pool:
        results = list(pool.map(scan, list(net.hosts())))
    arp = _arp_table()
    rows = []
    for ip, ports in results:
        if not ports and ip not in arp and ip != own:
            continue
        try:
            name = socket.gethostbyaddr(ip)[0] if ports or ip in arp else ""
        except OSError:
            name = ""
        guess = "printer" if 9100 in ports or 631 in ports else "camera" if 554 in ports else "Chromecast/TV" if 8009 in ports else "iPhone/iPad" if 62078 in ports else ""
        rows.append([ip + (ctx.tr(" (этот ПК)", " (this PC)") if ip == own else ""), name, arp.get(ip, ""), ", ".join(map(str, ports)), guess])
    rows.sort(key=lambda r: tuple(int(x) for x in r[0].split()[0].split(".")))
    return {"table": table(["IP", ctx.tr("Имя", "Name"), "MAC", ctx.tr("Порты", "Ports"), ctx.tr("Похоже на", "Looks like")], rows),
            "note": ctx.tr(f"Найдено устройств: {len(rows)} в {net}", f"Devices found: {len(rows)} in {net}")}

@utility("net.subnet", "net", "Калькулятор подсетей IP", "IP subnet calculator",
         "Для 192.168.1.10/24 или 10.0.0.0 255.255.255.0: сеть, широковещательный адрес, маска, wildcard, диапазон узлов, количество; IPv6 тоже.",
         "For 192.168.1.10/24 or 10.0.0.0 255.255.255.0: network, broadcast, mask, wildcard, host range, count; IPv6 too.",
         input="text", live=True, ai_default=True, placeholder="192.168.1.10/24",
         opts=[O("split", "int", "Разбить на подсети с префиксом (0 — нет)", "Split into subnets with prefix (0 = no)", 0, min=0, max=128)],
         keys="подсеть маска cidr калькулятор ip сеть subnet mask calculator wildcard broadcast")
def net_subnet(ctx):
    raw = ctx.need_text("Введите адрес с маской", "Enter an address with a mask").strip()
    parts = raw.replace(",", " ").split()
    text = parts[0] if len(parts) == 1 else f"{parts[0]}/{parts[1]}"
    if "/" not in text:
        text += "/32" if ":" not in text else "/128"
    try:
        iface = ipaddress.ip_interface(text)
    except ValueError as exc:
        raise UtilError(f"Не понимаю адрес: {exc}", f"Cannot read the address: {exc}")
    net = iface.network
    hosts = net.num_addresses - (2 if net.version == 4 and net.prefixlen < 31 else 0)
    first = net.network_address + (1 if net.version == 4 and net.prefixlen < 31 else 0)
    last = net.broadcast_address - (1 if net.version == 4 and net.prefixlen < 31 else 0)
    info = [[ctx.tr("Адрес", "Address"), str(iface.ip)], [ctx.tr("Сеть", "Network"), str(net)], [ctx.tr("Маска", "Mask"), str(net.netmask)],
            ["Wildcard", str(net.hostmask)], [ctx.tr("Узлы", "Hosts"), f"{first} — {last}"], [ctx.tr("Узлов", "Host count"), f"{hosts:,}".replace(",", " ")]]
    if net.version == 4:
        info.insert(3, [ctx.tr("Широковещательный", "Broadcast"), str(net.broadcast_address)])
        info.append([ctx.tr("Класс и тип", "Class and type"), ("A" if iface.ip.packed[0] < 128 else "B" if iface.ip.packed[0] < 192 else "C" if iface.ip.packed[0] < 224 else "D/E")
                     + " · " + (ctx.tr("частный", "private") if iface.ip.is_private else ctx.tr("публичный", "public"))])
        info.append([ctx.tr("Двоичная маска", "Binary mask"), ".".join(f"{b:08b}" for b in net.netmask.packed)])
    else:
        info.append([ctx.tr("Тип", "Type"), ctx.tr("частный", "private") if iface.ip.is_private else ctx.tr("глобальный", "global")])
    result: Dict[str, Any] = {"info": info}
    split = ctx.o["split"]
    if split:
        if split <= net.prefixlen or split > net.max_prefixlen:
            raise UtilError(f"Префикс для разбиения должен быть от {net.prefixlen + 1} до {net.max_prefixlen}", f"The split prefix must be {net.prefixlen + 1}–{net.max_prefixlen}")
        if split - net.prefixlen > 10:
            raise UtilError("Получится больше 1024 подсетей", "That makes over 1024 subnets")
        result["table"] = table([ctx.tr("Подсеть", "Subnet"), ctx.tr("Узлы", "Hosts")],
                                [[str(s), f"{s.network_address + 1} — {s.broadcast_address - 1}" if s.version == 4 and s.prefixlen < 31 else f"{s.network_address} — {s.broadcast_address}"]
                                 for s in net.subnets(new_prefix=split)])
    return result

@utility("net.speed", "net", "Тест скорости интернета", "Internet speed test",
         "Меряет задержку, скорость скачивания и отдачи через серверы Cloudflare (около 10–20 секунд, ~40 МБ трафика).",
         "Measures latency, download and upload speed via Cloudflare servers (about 10–20 seconds, ~40 MB of traffic).",
         input="none", opts=[O("size", "choice", "Объём", "Size", "25", choices=[("10", "Быстро (10 МБ)", "Quick (10 MB)"), ("25", "Обычно (25 МБ)", "Normal (25 MB)"), ("100", "Точно (100 МБ)", "Accurate (100 MB)")])],
         keys="скорость интернета спидтест speedtest download upload ping")
def net_speed(ctx):
    size = int(ctx.o["size"]) * 1_000_000
    try:
        with _client(60.0) as client:
            lat = []
            for _ in range(5):
                t0 = time.perf_counter()
                client.get("https://speed.cloudflare.com/__down", params={"bytes": 0})
                lat.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            got = 0
            with client.stream("GET", "https://speed.cloudflare.com/__down", params={"bytes": size}) as resp:
                for chunk in resp.iter_bytes(262144):
                    got += len(chunk)
            down = got * 8 / (time.perf_counter() - t0) / 1e6
            payload = b"0" * min(size // 2, 25_000_000)
            t0 = time.perf_counter()
            client.post("https://speed.cloudflare.com/__up", content=payload, headers={"Content-Type": "application/octet-stream"})
            up = len(payload) * 8 / (time.perf_counter() - t0) / 1e6
            meta = {}
            try:
                meta = client.get("https://speed.cloudflare.com/meta").json()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        raise _net_error(exc)
    lat.sort()
    jitter = sum(abs(a - b) for a, b in zip(lat, lat[1:])) / max(1, len(lat) - 1)
    info = [[ctx.tr("Скачивание", "Download"), f"{down:.1f} {ctx.tr('Мбит/с', 'Mbps')}"], [ctx.tr("Отдача", "Upload"), f"{up:.1f} {ctx.tr('Мбит/с', 'Mbps')}"],
            [ctx.tr("Задержка", "Latency"), f"{lat[len(lat) // 2]:.0f} {ctx.tr('мс', 'ms')}"], [ctx.tr("Джиттер", "Jitter"), f"{jitter:.0f} {ctx.tr('мс', 'ms')}"]]
    if meta:
        info.append([ctx.tr("Сервер", "Server"), f"{meta.get('colo', '')} · {meta.get('city', '')}".strip(" ·")])
        info.append([ctx.tr("Провайдер", "ISP"), str(meta.get("asOrganization", ""))])
    return {"info": info, "note": ctx.tr("Результат зависит от загрузки сети и Wi-Fi", "The result depends on the network load and Wi-Fi")}

CURRENCY_NAMES = {"RUB": ("рубль", "ruble"), "USD": ("доллар США", "US dollar"), "EUR": ("евро", "euro"), "CNY": ("юань", "yuan"), "GBP": ("фунт", "pound"),
                  "JPY": ("иена", "yen"), "KZT": ("тенге", "tenge"), "BYN": ("бел. рубль", "Belarusian ruble"), "UAH": ("гривна", "hryvnia"), "TRY": ("лира", "lira"),
                  "AED": ("дирхам", "dirham"), "CHF": ("франк", "franc"), "AMD": ("драм", "dram"), "GEL": ("лари", "lari"), "UZS": ("сум", "som")}

def _rates() -> Tuple[Dict[str, float], str]:
    """Units of RUB per one unit of each currency, and the source/date."""
    with _client(12.0) as client:
        try:
            data = client.get("https://www.cbr-xml-daily.ru/daily_json.js").json()
            rates = {"RUB": 1.0}
            for code, v in (data.get("Valute") or {}).items():
                rates[code] = float(v["Value"]) / float(v.get("Nominal") or 1)
            return rates, f"ЦБ РФ / CBR · {str(data.get('Date', ''))[:10]}"
        except Exception:  # noqa: BLE001 - the CBR mirror is down: an open exchange-rate API
            data = client.get("https://open.er-api.com/v6/latest/RUB").json()
            rates = {k: 1 / float(v) for k, v in (data.get("rates") or {}).items() if v}
            return rates, f"open.er-api.com · {str(data.get('time_last_update_utc', ''))[:16]}"

@utility("net.currency", "net", "Курсы валют и конвертер", "Exchange rates and converter",
         "Переводит сумму между валютами по курсу ЦБ РФ на сегодня (запасной источник — open.er-api.com); пусто — таблица основных курсов.",
         "Converts an amount between currencies at today's Bank of Russia rate (fallback: open.er-api.com); empty = a table of the main rates.",
         input="text", ai_default=True, placeholder="100 USD в RUB", placeholder_en="100 USD to EUR",
         keys="курс валют доллар евро юань рубль конвертер валют exchange rate currency converter")
def net_currency(ctx):
    try:
        rates, source = _rates()
    except Exception as exc:  # noqa: BLE001
        raise _net_error(exc)
    text = str(ctx.text or "").strip().upper().replace(",", ".")
    aliases = {"РУБ": "RUB", "РУБЛЕЙ": "RUB", "РУБЛЬ": "RUB", "Р": "RUB", "₽": "RUB", "$": "USD", "ДОЛЛАР": "USD", "ДОЛЛАРОВ": "USD", "€": "EUR", "ЕВРО": "EUR",
               "ЮАНЬ": "CNY", "ЮАНЕЙ": "CNY", "¥": "CNY", "ТЕНГЕ": "KZT", "£": "GBP", "ЛИР": "TRY", "ДИРХАМ": "AED"}
    if not text:
        main = ["USD", "EUR", "CNY", "GBP", "KZT", "BYN", "TRY", "AED", "JPY", "CHF"]
        return {"table": table([ctx.tr("Валюта", "Currency"), ctx.tr("Курс, ₽", "Rate, ₽")],
                               [[f"{c} — {CURRENCY_NAMES.get(c, (c, c))[1 if ctx.en else 0]}", f"{rates[c]:.4f}"] for c in main if c in rates]), "note": source}
    tokens = re.findall(r"[\d.]+|[A-ZА-ЯЁ]+|[$€¥£₽]", text)
    amount = 1.0
    codes = []
    for t in tokens:
        if re.fullmatch(r"[\d.]+", t):
            amount = as_float(t, 1.0)
        else:
            code = aliases.get(t, t)
            if code in rates:
                codes.append(code)
    if not codes:
        raise UtilError("Не нашёл валюту: пример «100 USD в RUB»", "No currency found: e.g. “100 USD to EUR”")
    src = codes[0]
    dst = codes[1] if len(codes) > 1 else ("RUB" if src != "RUB" else "USD")
    value = amount * rates[src] / rates[dst]
    return {"info": [[f"{amount:g} {src}", f"{value:,.2f} {dst}".replace(",", " ")], [f"1 {src}", f"{rates[src] / rates[dst]:.4f} {dst}"],
                     [f"1 {dst}", f"{rates[dst] / rates[src]:.4f} {src}"]], "note": source}

@utility("net.wol", "net", "Разбудить ПК по сети (Wake-on-LAN)", "Wake a PC over the network (Wake-on-LAN)",
         "Отправляет «магический пакет» на MAC-адрес, чтобы включить компьютер в локальной сети (WoL должен быть включён в BIOS).",
         "Sends a “magic packet” to a MAC address to power on a computer on the local network (WoL must be enabled in BIOS).",
         input="text", danger=True, placeholder="AA:BB:CC:DD:EE:FF",
         opts=[O("broadcast", "text", "Широковещательный адрес", "Broadcast address", "255.255.255.255"), O("port", "int", "Порт", "Port", 9, min=1, max=65535)],
         keys="wake on lan wol включить компьютер разбудить magic packet")
def net_wol(ctx):
    mac = re.sub(r"[^0-9A-Fa-f]", "", ctx.need_text("Введите MAC-адрес", "Enter a MAC address"))
    if len(mac) != 12:
        raise UtilError("MAC-адрес — 12 шестнадцатеричных цифр, например AA:BB:CC:DD:EE:FF", "A MAC address is 12 hex digits, e.g. AA:BB:CC:DD:EE:FF")
    packet = b"\xff" * 6 + bytes.fromhex(mac) * 16
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for _ in range(3):
                s.sendto(packet, (ctx.o["broadcast"] or "255.255.255.255", ctx.o["port"]))
    except OSError as exc:
        raise _net_error(exc)
    pretty = ":".join(mac[i:i + 2] for i in range(0, 12, 2)).upper()
    return ctx.tr(f"Магический пакет отправлен на {pretty}", f"Magic packet sent to {pretty}")
