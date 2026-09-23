"""Dependency-free HTTP + WebSocket server.

Everything here is stdlib on purpose: no pip install step means no wheel-compatibility
surprises on a fresh Python, and the panel keeps working after a Python upgrade.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_TEXT = 0x1
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


def _frame(payload: bytes, opcode: int = OP_TEXT) -> bytes:
    header = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += n.to_bytes(2, "big")
    else:
        header.append(127)
        header += n.to_bytes(8, "big")
    return bytes(header) + payload


class WSClient:
    """One connected panel. Reads run on their own thread, writes are lock-guarded."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.alive = True
        self._send_lock = threading.Lock()

    def send_text(self, text: str) -> None:
        self.send_raw(_frame(text.encode("utf-8")))

    def send_raw(self, data: bytes) -> None:
        if not self.alive:
            return
        try:
            with self._send_lock:
                self.sock.sendall(data)
        except OSError:
            self.alive = False

    def close(self) -> None:
        if self.alive:
            self.alive = False
            try:
                self.sock.sendall(_frame(b"", OP_CLOSE))
            except OSError:
                pass

    # -- framing ------------------------------------------------------------
    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    def read_message(self) -> str | None:
        """Return the next text message, or None for control frames we handled."""
        b0, b1 = self._read_exact(2)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        if length == 126:
            length = int.from_bytes(self._read_exact(2), "big")
        elif length == 127:
            length = int.from_bytes(self._read_exact(8), "big")
        if length > 1 << 20:
            raise ConnectionError("oversized frame")

        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))

        if opcode == OP_CLOSE:
            raise ConnectionError("client closed")
        if opcode == OP_PING:
            self.send_raw(_frame(payload, OP_PONG))
            return None
        if opcode == OP_PONG:
            return None
        if opcode == OP_TEXT:
            return payload.decode("utf-8", "replace")
        return None


class Hub:
    """Bridges the telemetry loop and the connected panels."""

    def __init__(self) -> None:
        self._clients: set[WSClient] = set()
        self._lock = threading.Lock()
        self.on_command = lambda msg, client: None
        self.on_connect = lambda client: None
        # Fired with the number of panels still connected, every time one goes away --
        # including the ones broadcast() finds dead rather than the ones that hang up
        # politely, which is most of them when a tablet goes out of wifi range.
        self.on_disconnect = lambda remaining: None

    def add(self, client: WSClient) -> None:
        with self._lock:
            self._clients.add(client)

    def remove(self, client: WSClient) -> None:
        with self._lock:
            was_there = client in self._clients
            self._clients.discard(client)
            remaining = len(self._clients)
        # Outside the lock: the callback asks the hub how many are left, and this lock
        # is not reentrant.
        if was_there:
            self.on_disconnect(remaining)

    def broadcast(self, payload: dict) -> None:
        data = _frame(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        with self._lock:
            targets = list(self._clients)
        for client in targets:
            client.send_raw(data)
            if not client.alive:
                self.remove(client)

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)


def make_handler(hub: Hub, web_root: Path, api):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "RigDeck"

        def log_message(self, fmt, *args):  # quiet; the console shows link state instead
            pass

        # -- routing --------------------------------------------------------
        def do_GET(self):
            if self.path.split("?")[0] == "/ws":
                self._serve_ws()
                return
            if self.path.startswith("/api/"):
                self._serve_api_get()
                return
            self._serve_static()

        def do_HEAD(self):
            # The pairing page asks this way whether there is an apk to offer, because a
            # request for a file that is not there is answered with the panel instead --
            # so only the headers tell the two apart, and there is no reason to send a
            # megabyte to find out.
            self._serve_static(body=False)

        def do_POST(self):
            if self.path.startswith("/api/"):
                self._serve_api_post()
                return
            self.send_error(404)

        # -- api ------------------------------------------------------------
        def _json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_api_get(self):
            route = self.path.split("?")[0]
            if route == "/api/status":
                self._json(api.status())
            elif route == "/api/config":
                self._json(api.get_config())
            else:
                self.send_error(404)

        def _serve_api_post(self):
            route = self.path.split("?")[0]
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json({"error": "bad json"}, 400)
                return
            if route == "/api/config":
                self._json(api.set_config(body))
            else:
                self.send_error(404)

        # -- static ---------------------------------------------------------
        def _serve_static(self, body=True):
            route = self.path.split("?")[0]
            rel = route.lstrip("/") or "index.html"
            # Cells are immutable for as long as an export lasts and the panel asks for
            # them by version, so they can be kept. meta.json is how the tablet learns
            # there is a new version at all -- cache that and a re-export never arrives.
            is_map = route.startswith("/maps/") and not route.endswith("/meta.json")
            target = (web_root / rel).resolve()
            if not str(target).startswith(str(web_root.resolve())) or not target.is_file():
                # Map cells are fetched as JSON, so a missing one has to say so plainly --
                # falling back to the panel would hand the tablet HTML to parse.
                if is_map:
                    self.send_error(404)
                    return
                target = web_root / "index.html"
                if not target.is_file():
                    self.send_error(404)
                    return
            data = target.read_bytes()
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix == ".webmanifest":
                ctype = "application/manifest+json"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            # The cells only change when the map is re-exported, and then under a new
            # version, so let the tablet keep them.
            self.send_header("Cache-Control", "max-age=604800" if is_map else "no-store")
            self.end_headers()
            if body:
                self.wfile.write(data)

        # -- websocket ------------------------------------------------------
        def _serve_ws(self):
            key = self.headers.get("Sec-WebSocket-Key")
            if not key or "websocket" not in (self.headers.get("Upgrade") or "").lower():
                self.send_error(400, "expected a websocket upgrade")
                return

            accept = base64.b64encode(
                hashlib.sha1((key + WS_GUID).encode()).digest()
            ).decode()
            self.close_connection = True
            self.wfile.write(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: " + accept.encode() + b"\r\n\r\n"
            )
            self.wfile.flush()

            client = WSClient(self.connection)
            self.connection.settimeout(None)
            # A tablet that sleeps, drops off Wi-Fi or is killed by Android leaves the
            # socket half open: this thread blocks in recv for good and the panel stays
            # in the hub as a client that can never be written to. Keep-alive probes let
            # the OS notice within about twenty seconds and close it for us.
            try:
                self.connection.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 3000))
            except (AttributeError, OSError):
                try:
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                except OSError:
                    pass
            hub.add(client)
            hub.on_connect(client)
            try:
                while client.alive:
                    message = client.read_message()
                    if message is None:
                        continue
                    try:
                        hub.on_command(json.loads(message), client)
                    except ValueError:
                        pass
            except (ConnectionError, OSError):
                pass
            finally:
                client.alive = False
                hub.remove(client)

    return Handler


class _QuietServer(ThreadingHTTPServer):
    """A tablet that walks out of Wi-Fi range drops its keep-alive connections, and the
    stock server answers that with a full traceback on stderr. It is not an error worth
    reporting -- anything else still is."""

    # Windows takes SO_REUSEADDR to mean "let a second socket bind this port too", which
    # would hide a duplicate server instead of refusing it.
    allow_reuse_address = False

    def handle_error(self, request, client_address) -> None:
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)


class RigDeckServer:
    def __init__(self, hub: Hub, web_root: Path, api, port: int) -> None:
        self.hub = hub
        self.port = port
        self._httpd = _QuietServer(("0.0.0.0", port), make_handler(hub, web_root, api))
        self._httpd.daemon_threads = True

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        return thread

    def shutdown(self) -> None:
        self._httpd.shutdown()


def lan_addresses() -> list[str]:
    """Best-effort list of LAN IPs the tablet could reach us on, primary first."""
    found: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))  # no traffic sent; just picks the default route
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found or ["127.0.0.1"]
