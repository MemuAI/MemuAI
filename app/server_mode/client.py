"""Client side of server mode: an API client over a pluggable transport.

The transport is deliberately abstract (``request(method, path, ...)``) so
the whole sync/bridge logic is testable with a mock transport and no live
network.  :class:`HttpxTransport` is the only real implementation; it is
imported lazily so importing this module never pulls in httpx.

The token is passed to the transport once and is never logged, never put
into an exception message and never returned by any method here.

Offline queue: when the transport raises (no connection), planned push /
pull operations are appended to a JSON queue file instead of being lost;
:meth:`ServerClient.flush_queue` replays them in order once the server is
reachable again and keeps the remainder on the first failure.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from .sync import Resolution, SyncAction, apply_resolutions, plan_sync

SERVER_API_PREFIX = "/api/server"


class ServerAuthError(Exception):
    """The server rejected the credentials (HTTP 401)."""


class ServerConnectionError(Exception):
    """The server could not be reached at all."""


@dataclass
class TransportResponse:
    status: int
    data: Any = None
    content: bytes = b""


class Transport(Protocol):
    def request(self, method: str, path: str, *, json: Any = None,
                data: Optional[bytes] = None,
                headers: Optional[Dict[str, str]] = None) -> TransportResponse:
        ...


class HttpxTransport:
    """Real HTTP transport over httpx. Created lazily, one client per use."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("server URL must start with http:// or https://")
        self._token = token
        self.timeout = timeout

    def request(self, method: str, path: str, *, json: Any = None,
                data: Optional[bytes] = None,
                headers: Optional[Dict[str, str]] = None) -> TransportResponse:
        import httpx

        merged = {"Authorization": "Bearer " + self._token}
        if headers:
            merged.update(headers)
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                response = client.request(method, self.base_url + path,
                                          json=json, content=data, headers=merged)
        except httpx.HTTPError as exc:
            raise ServerConnectionError(str(exc)) from exc
        parsed: Any = None
        if response.content:
            try:
                parsed = response.json()
            except ValueError:
                parsed = None
        return TransportResponse(response.status_code, parsed, response.content)

    def __repr__(self) -> str:  # never leak the token
        return f"HttpxTransport({self.base_url!r}, token=••••)"


@dataclass
class QueuedOp:
    kind: str  # "push" | "pull"
    name: str
    payload: Any = None  # bytes for push (base64-safe: stored as latin-1 str), dict for docs


class ServerClient:
    """Sync + bridge-queue client. All network access goes via ``transport``."""

    def __init__(self, transport: Transport, *,
                 queue_path: Optional[str | Path] = None,
                 local_files: Optional[Callable[[], Dict[str, Dict]]] = None,
                 local_file_reader: Optional[Callable[[str], Optional[bytes]]] = None,
                 local_file_writer: Optional[Callable[[str, bytes], None]] = None) -> None:
        self.transport = transport
        self.queue_path = Path(queue_path) if queue_path else None
        self._local_files = local_files
        self._local_file_reader = local_file_reader
        self._local_file_writer = local_file_writer

    # ---- low level -----------------------------------------------------
    def _call(self, method: str, path: str, **kwargs: Any) -> TransportResponse:
        response = self.transport.request(method, path, **kwargs)
        if response.status == 401:
            raise ServerAuthError("server rejected the token (HTTP 401)")
        return response

    # ---- connection ----------------------------------------------------
    def check_connection(self) -> Dict[str, Any]:
        started = time.monotonic()
        response = self._call("GET", SERVER_API_PREFIX + "/health")
        latency_ms = round((time.monotonic() - started) * 1000)
        if response.status != 200:
            return {"ok": False, "status": response.status,
                    "detail": (response.data or {}).get("error", "unexpected status")}
        return {"ok": True, "status": 200, "latency_ms": latency_ms,
                "server": (response.data or {}).get("server", "memuai-server")}

    # ---- files ---------------------------------------------------------
    def manifest(self) -> Dict[str, Any]:
        response = self._call("GET", SERVER_API_PREFIX + "/manifest")
        if response.status != 200:
            raise ServerConnectionError(f"manifest failed: HTTP {response.status}")
        return response.data or {}

    def push_file(self, name: str, data: bytes) -> Dict[str, Any]:
        response = self._call("PUT", SERVER_API_PREFIX + "/files/" + name, data=bytes(data),
                              headers={"Content-Type": "application/octet-stream"})
        if response.status not in (200, 201):
            raise ServerConnectionError(f"push failed: HTTP {response.status}")
        return response.data or {}

    def pull_file(self, name: str) -> bytes:
        response = self._call("GET", SERVER_API_PREFIX + "/files/" + name)
        if response.status == 404:
            raise KeyError(name)
        if response.status != 200:
            raise ServerConnectionError(f"pull failed: HTTP {response.status}")
        return response.content

    # ---- documents -----------------------------------------------------
    def push_doc(self, kind: str, doc_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self._call("PUT", f"{SERVER_API_PREFIX}/{kind}/{doc_id}", json=payload)
        if response.status not in (200, 201):
            raise ServerConnectionError(f"push doc failed: HTTP {response.status}")
        return response.data or {}

    def pull_doc(self, kind: str, doc_id: str) -> Optional[Dict[str, Any]]:
        response = self._call("GET", f"{SERVER_API_PREFIX}/{kind}/{doc_id}")
        if response.status == 404:
            return None
        if response.status != 200:
            raise ServerConnectionError(f"pull doc failed: HTTP {response.status}")
        return response.data

    # ---- bridge queue --------------------------------------------------
    def list_bridge_requests(self, status: str = "pending") -> List[Dict[str, Any]]:
        # Query string is part of the path so mock transports stay trivial.
        response = self._call("GET", SERVER_API_PREFIX + f"/bridge/requests?status={status}")
        if response.status != 200:
            raise ServerConnectionError(f"bridge list failed: HTTP {response.status}")
        return (response.data or {}).get("requests", [])

    def post_bridge_result(self, req_id: str, status: str,
                           result: Dict[str, Any]) -> Dict[str, Any]:
        response = self._call("POST", f"{SERVER_API_PREFIX}/bridge/requests/{req_id}/result",
                              json={"status": status, "result": result})
        if response.status != 200:
            raise ServerConnectionError(f"bridge result failed: HTTP {response.status}")
        return response.data or {}

    # ---- sync ----------------------------------------------------------
    def plan(self, remote_manifest: Dict[str, Any],
             base_manifest: Optional[Dict[str, Any]] = None) -> List[SyncAction]:
        if self._local_files is None:
            raise ValueError("local manifest provider is not configured")
        local_files = self._local_files()
        remote_files = (remote_manifest or {}).get("files", {})
        return plan_sync(local_files, remote_files, (base_manifest or {}).get("files", {}))

    def sync_files(self, *, base_manifest: Optional[Dict[str, Any]] = None,
                   resolutions: Optional[Dict[str, Resolution]] = None,
                   queue_on_failure: bool = True) -> Dict[str, Any]:
        """Apply a file sync plan. Returns counts + queued/conflict details."""
        try:
            remote = self.manifest()
        except (ServerConnectionError, ServerAuthError):
            raise
        except Exception as exc:  # transport-level failure -> offline queue
            if queue_on_failure:
                return {"ok": False, "queued": 0, "error": "unreachable",
                        "detail": "manifest unavailable; retry later"}
            raise ServerConnectionError(str(exc)) from exc
        actions = apply_resolutions(self.plan(remote, base_manifest),
                                    self._local_files() if self._local_files else {},
                                    remote.get("files", {}), resolutions or {})
        pushed, pulled, conflicts, queued = 0, 0, [], 0
        for action in actions:
            if action.kind == "conflict":
                conflicts.append(action.name)
                continue
            if action.kind == "skip":
                continue
            try:
                if action.kind == "push" and self._local_file_reader:
                    data = self._local_file_reader(action.name)
                    if data is not None:
                        self.push_file(action.name, data)
                        pushed += 1
                elif action.kind == "pull" and self._local_file_writer:
                    self._local_file_writer(action.name, self.pull_file(action.name))
                    pulled += 1
            except ServerConnectionError:
                if queue_on_failure:
                    self.enqueue(QueuedOp(action.kind, action.name))
                    queued += 1
                else:
                    raise
        return {"ok": True, "pushed": pushed, "pulled": pulled,
                "conflicts": conflicts, "queued": queued}

    # ---- offline queue -------------------------------------------------
    def _load_queue(self) -> List[Dict[str, Any]]:
        if not self.queue_path or not self.queue_path.is_file():
            return []
        try:
            data = json.loads(self.queue_path.read_text("utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save_queue(self, ops: List[Dict[str, Any]]) -> None:
        if not self.queue_path:
            return
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self.queue_path.write_text(json.dumps(ops, ensure_ascii=False), "utf-8")

    def enqueue(self, op: QueuedOp) -> int:
        ops = self._load_queue()
        ops.append({"kind": op.kind, "name": op.name})
        self._save_queue(ops)
        return len(ops)

    def queued_count(self) -> int:
        return len(self._load_queue())

    def flush_queue(self) -> Dict[str, Any]:
        """Replay queued ops in order; stop at the first failure, keep rest."""
        ops = self._load_queue()
        applied = 0
        remaining = list(ops)
        for op in ops:
            try:
                if op["kind"] == "push" and self._local_file_reader:
                    data = self._local_file_reader(op["name"])
                    if data is not None:
                        self.push_file(op["name"], data)
                elif op["kind"] == "pull" and self._local_file_writer:
                    self._local_file_writer(op["name"], self.pull_file(op["name"]))
                else:
                    continue
                applied += 1
                remaining = remaining[1:]
            except (ServerConnectionError, ServerAuthError, KeyError):
                break
        self._save_queue(remaining)
        return {"applied": applied, "remaining": len(remaining)}
