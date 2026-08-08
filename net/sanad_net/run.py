"""`python -m sanad_net.run` — start Sanad and open the chat page.

The happy path takes no flags. It starts a coordinator, starts one node on this
machine, waits until the network is ready, and opens your browser.

    python -m sanad_net.run                 # a network on this machine
    python -m sanad_net.run --join          # join someone else's network instead
    python -m sanad_net.run --no-browser    # don't open a browser

Ctrl-C stops everything it started.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from .setup import BIN_DIR, LOCAL, MODELS, MODELS_DIR, installed_build, MIN_BUILD

NET_DIR = Path(__file__).resolve().parent.parent
CR = chr(13)          # carriage return, for in-place progress lines


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def free_port(preferred: int) -> int:
    for port in (preferred, 0):
        s = socket.socket()
        try:
            s.bind(("", port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    return preferred


def say(msg: str = "") -> None:
    print(msg, flush=True)


def wait_until(check, timeout_s: float, note: str = "") -> bool:
    """Wait, but tell the user something is happening — a first run spends
    real seconds loading a model, and silence reads as a hang."""
    deadline = time.time() + timeout_s
    dots = 0
    while time.time() < deadline:
        try:
            if check():
                if note:
                    print(flush=True)
                return True
        except Exception:
            pass
        if note:
            dots += 1
            print(CR + "  " + note + "." * (dots % 4) + "   ", end="", flush=True)
        time.sleep(1.0)
    if note:
        print(flush=True)
    return False


def status(url: str) -> dict:
    with urllib.request.urlopen(f"{url}/status", timeout=5) as r:
        return json.loads(r.read())


def preflight() -> list[str]:
    """Everything missing, in plain words."""
    problems = []
    build = installed_build()
    if build is None:
        problems.append("llama.cpp is not installed")
    elif build < MIN_BUILD:
        problems.append(f"llama.cpp b{build} is too old (needs b{MIN_BUILD}+)")
    if not any((MODELS_DIR / m["name"]).exists() for m in MODELS.values()):
        problems.append("no model files found")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description="Start Sanad and open the chat page.")
    ap.add_argument("--join", metavar="URL", nargs="?", const="discover",
                    help="lend this machine to an existing network instead of starting one; "
                         "give a coordinator URL, or omit it to find one on your network")
    ap.add_argument("--name", default=None, help="the account your contribution is credited to")
    ap.add_argument("--pledge-mb", type=float, default=1500,
                    help="how much memory to lend the network (MB)")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--dedicated", action="store_true",
                    help="never pause for the machine's owner (for a spare box or server)")
    args = ap.parse_args()

    problems = preflight()
    if problems:
        print("Sanad is not set up yet:")
        for p in problems:
            print(f"  - {p}")
        print("\nRun this first:\n    python -m sanad_net.setup")
        raise SystemExit(1)

    operator = args.name or os.environ.get("USERNAME") or os.environ.get("USER") or "me"
    procs: list[subprocess.Popen] = []

    def spawn(mod_args: list[str]) -> subprocess.Popen:
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        p = subprocess.Popen([sys.executable, "-m", *mod_args], cwd=str(NET_DIR), env=env)
        procs.append(p)
        return p

    def shutdown(*_):
        print("\nStopping...")
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=15)
            except Exception:
                p.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)

    try:
        if args.join:
            # Lend this machine to someone else's network.
            node_args = ["sanad_net.node", "--node-id", f"{operator}-{os.getpid()}",
                         "--operator", operator, "--host", "0.0.0.0",
                         "--port", str(free_port(50070)),
                         "--pledge-mb", str(args.pledge_mb),
                         "--rpc-bin", str(BIN_DIR)]
            if args.dedicated:
                node_args += ["--busy-at", "101"]
            if args.join == "discover":
                say("Looking for a network on your local network...")
                node_args += ["--discover"]
            else:
                node_args += ["--coordinator", args.join]
            spawn(node_args)
            print(f"\nLending {args.pledge_mb:.0f} MB as '{operator}'. Ctrl-C to stop.")
            say("Your credits are kept if you leave — withdrawal is never punished.")
            procs[0].wait()
            return

        # Start a network of our own.
        models = [str(MODELS_DIR / m["name"]) for m in MODELS.values()
                  if (MODELS_DIR / m["name"]).exists()]
        port = free_port(args.port)
        ip = lan_ip()
        url = f"http://{ip}:{port}"

        say("Starting Sanad...")
        spawn(["sanad_net.coordinator", "--port", str(port), "--bind", "0.0.0.0",
               "--models", ",".join(models), "--llama-bin", str(BIN_DIR),
               "--ledger", str(LOCAL / "ledger.jsonl")])
        if not wait_until(lambda: status(url) is not None, 60, "starting the coordinator"):
            raise SystemExit("the coordinator did not start; see the messages above")

        node_args = ["sanad_net.node", "--node-id", f"{operator}-local", "--operator", operator,
                     "--host", "0.0.0.0", "--port", str(free_port(50070)),
                     "--pledge-mb", str(args.pledge_mb),
                     "--coordinator", url, "--rpc-bin", str(BIN_DIR)]
        if args.dedicated:
            node_args += ["--busy-at", "101"]
        spawn(node_args)

        if not wait_until(lambda: len(status(url)["nodes"]) >= 1, 90,
                          "waiting for this machine to join"):
            raise SystemExit("the node did not join; see the messages above")

        st = status(url)
        print(f"\n  Sanad is running at  {url}")
        say(f"  serving:  {st['model']}")
        say(f"  nodes:    {len(st['nodes'])}  ({st['pool_mb']:.0f} MB pledged, as '{operator}')")
        print(f"\n  Anyone on your network can join with:")
        say(f"      python -m sanad_net.run --join")
        say(f"  Or just open {url} in a browser to chat.")
        if st.get("headroom"):
            say(f"\n  Your network can already hold a bigger model than it's serving.")
            say(f"  Grow the ladder:  python -m sanad_net.models")
        print("\n  Ctrl-C to stop.")

        if not args.no_browser:
            webbrowser.open(url)
        procs[0].wait()
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()


if __name__ == "__main__":
    main()
