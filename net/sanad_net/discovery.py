"""LAN discovery — so joining a network is one command, not an IP hunt.

The coordinator answers a small UDP broadcast on a fixed port; a node that is
started with `--discover` asks the local network "where is the coordinator?"
and gets back an address to register with.

Deliberately minimal and deliberately local:
- broadcast only (never routed off the subnet), so this cannot be used to find
  coordinators on the wider internet;
- the reply carries only what a node needs to dial in;
- discovery is a convenience, not a trust mechanism. Anyone on your LAN can
  answer, so the same trusted-network assumption that covers everything else in
  v0 covers this too. Pass an explicit `--coordinator` when you do not trust
  the network you are on.
"""

from __future__ import annotations

import json
import socket
import threading

DISCOVERY_PORT = 7859
MAGIC = "sanad-discover-v1"
REPLY = "sanad-coordinator-v1"


class DiscoveryResponder:
    """Runs beside the coordinator and answers 'where are you?' broadcasts."""

    def __init__(self, http_port: int, name: str = "sanad") -> None:
        self.http_port = http_port
        self.name = name
        self._sock: socket.socket | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", DISCOVERY_PORT))
        except OSError:
            sock.close()
            return          # another responder already owns the port; harmless
        sock.settimeout(1.0)
        self._sock = sock
        threading.Thread(target=self._serve, daemon=True).start()

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
            # Reply with the address this node reached us on, so the answer is
            # always dialable from the asker's side of the network.
            try:
                local_ip = self._local_ip_towards(addr[0])
                payload = json.dumps({
                    "reply": REPLY, "name": self.name,
                    "coordinator": f"http://{local_ip}:{self.http_port}",
                }).encode()
                self._sock.sendto(payload, addr)
            except OSError:
                continue

    @staticmethod
    def _local_ip_towards(peer_ip: str) -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((peer_ip, 9))     # no packet sent; selects the right interface
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            s.close()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            self._sock.close()
            self._sock = None


def discover(timeout_s: float = 3.0) -> list[dict]:
    """Broadcast on the LAN and collect coordinator replies.

    Returns a list of {"name", "coordinator", "from"} — usually one entry.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.5)
    found: dict[str, dict] = {}
    try:
        for target in ("255.255.255.255", "<broadcast>"):
            try:
                sock.sendto(MAGIC.encode(), (target, DISCOVERY_PORT))
            except OSError:
                continue
        deadline = threading.Event()
        timer = threading.Timer(timeout_s, deadline.set)
        timer.daemon = True
        timer.start()
        try:
            while not deadline.is_set():
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    rec = json.loads(data.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
                if rec.get("reply") != REPLY or "coordinator" not in rec:
                    continue
                found[rec["coordinator"]] = {
                    "name": rec.get("name", "sanad"),
                    "coordinator": rec["coordinator"],
                    "from": addr[0],
                }
        finally:
            timer.cancel()
    finally:
        sock.close()
    return list(found.values())
