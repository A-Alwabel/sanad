"""LAN discovery — so joining a network is one command, not an IP hunt.

The coordinator answers a small UDP probe on a fixed port; a node started with
`--discover` asks the local network "where is the coordinator?" and gets back an
address to register with.

**What this is and is not.** Nodes ask by *broadcast*, which does not route off
the subnet. The responder, however, is a UDP socket: it will answer any probe
that reaches it, including a unicast one, so it is bound to the coordinator's
own `--bind` address and refuses to answer sources outside private address
space. Do not mistake either property for security:

- Anyone on your network can answer a probe, so an attacker can pose as the
  coordinator (an "evil twin"). A node that sees more than one answer refuses to
  guess and asks you to choose.
- Discovery is a convenience, not a trust mechanism. Pass an explicit
  `--coordinator` on any network you do not control.

This matches the rest of v0: trusted network, trusted operators.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time

DISCOVERY_PORT = 7859
MAGIC = "sanad-discover-v1"
REPLY = "sanad-coordinator-v1"
MAX_REPLIES = 16            # a node never needs more than a handful
REPLY_MIN_INTERVAL_S = 0.2  # per-source rate limit on the responder


def _is_private(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


class DiscoveryResponder:
    """Runs beside the coordinator and answers 'where are you?' probes."""

    def __init__(self, http_port: int, name: str = "sanad", bind: str = "0.0.0.0") -> None:
        self.http_port = http_port
        self.name = name
        self.bind = bind
        self.active = False
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._last_reply: dict[str, float] = {}
        self._ip_cache: dict[str, str] = {}

    def start(self) -> bool:
        """Bind and serve. Returns False if discovery is unavailable, so the
        caller can say so instead of advertising a feature that is not on."""
        if self.bind in ("127.0.0.1", "localhost"):
            return False        # loopback-only coordinator: nothing to discover
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # No SO_REUSEADDR: a UDP responder has no TIME_WAIT need, and
            # allowing a co-bind lets another local process race our replies.
            sock.bind((self.bind, DISCOVERY_PORT))
        except OSError:
            sock.close()
            return False
        sock.settimeout(1.0)
        self._sock = sock
        self.active = True
        threading.Thread(target=self._serve, daemon=True).start()
        return True

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                return
            if data.decode("utf-8", "replace").strip() != MAGIC:
                continue
            peer = addr[0]
            if not _is_private(peer):
                continue                      # never answer off-network probes
            now = time.time()
            if now - self._last_reply.get(peer, 0.0) < REPLY_MIN_INTERVAL_S:
                continue                      # cheap flood guard
            self._last_reply[peer] = now
            if len(self._last_reply) > 4096:
                self._last_reply.clear()
            try:
                local_ip = self._local_ip_towards(peer)
                if local_ip.startswith("169.254.") or local_ip == "0.0.0.0":
                    continue                  # link-local is not a usable address
                self._sock.sendto(json.dumps({
                    "reply": REPLY, "name": self.name,
                    "coordinator": f"http://{local_ip}:{self.http_port}",
                }).encode(), addr)
            except OSError:
                continue

    def _local_ip_towards(self, peer_ip: str) -> str:
        """Which of our addresses this peer can reach us on — cached per peer."""
        cached = self._ip_cache.get(peer_ip)
        if cached:
            return cached
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((peer_ip, 9))     # no packet sent; selects the interface
            ip = s.getsockname()[0]
        except OSError:
            ip = "127.0.0.1"
        finally:
            s.close()
        if len(self._ip_cache) > 1024:
            self._ip_cache.clear()
        self._ip_cache[peer_ip] = ip
        return ip

    def stop(self) -> None:
        self._stop.set()
        self.active = False
        if self._sock is not None:
            self._sock.close()
            self._sock = None


def discover(timeout_s: float = 3.0) -> list[dict]:
    """Broadcast on the LAN and collect coordinator replies.

    Waits the full window rather than returning on the first answer, so a fast
    responder gains no advantage over the real one — the caller can then see
    that the choice is ambiguous instead of being silently steered.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.4)
    found: dict[str, dict] = {}
    try:
        try:
            sock.sendto(MAGIC.encode(), ("255.255.255.255", DISCOVERY_PORT))
        except OSError:
            return []
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and len(found) < MAX_REPLIES:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if not _is_private(addr[0]):
                continue
            try:
                rec = json.loads(data.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            if rec.get("reply") != REPLY or "coordinator" not in rec:
                continue
            url = str(rec["coordinator"])[:200]
            if not url.startswith("http://"):
                continue
            found[url] = {"name": str(rec.get("name", "sanad"))[:64],
                          "coordinator": url, "from": addr[0]}
    finally:
        sock.close()
    return list(found.values())
