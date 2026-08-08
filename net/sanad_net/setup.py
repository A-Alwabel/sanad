"""`python -m sanad_net.setup` — get everything Sanad needs, in one command.

Downloads the right llama.cpp build for this machine and a small model, into
`.local/` next to the repo. Tells you what it is fetching and how big it is
before it starts, and never installs anything system-wide.

    python -m sanad_net.setup            # ask before downloading
    python -m sanad_net.setup --yes      # don't ask
    python -m sanad_net.setup --check    # just report what is present

Stdlib only, like the rest of Sanad.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import ssl
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent   # .../sanad
LOCAL = REPO_ROOT / ".local"
BIN_DIR = LOCAL / "bin"
MODELS_DIR = LOCAL / "models"

# Minimum build with the CVE-2026-34159 fix in llama.cpp's RPC backend.
MIN_BUILD = 8492
LLAMA_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"

MODELS = {
    "small": {
        "name": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/"
               "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "mb": 470,
        "why": "tiny; makes a first run fast and cheap to reproduce",
    },
    "large": {
        "name": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/"
               "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "mb": 1100,
        "why": "second rung of the capacity ladder — the network upgrades to it "
               "when enough memory is pledged",
    },
}


def human(n_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024 or unit == "GB":
            return f"{n_bytes:.0f} {unit}" if unit != "GB" else f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} GB"


def asset_pattern() -> tuple[str, str]:
    """Which llama.cpp release asset fits this machine, and a readable label."""
    system = platform.system()
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if system == "Windows":
        return ("bin-win-cpu-arm64" if arm else "bin-win-cpu-x64",
                f"Windows {'ARM64' if arm else 'x64'} (CPU build)")
    if system == "Darwin":
        return ("bin-macos-arm64" if arm else "bin-macos-x64",
                f"macOS {'Apple Silicon' if arm else 'Intel'}")
    if system == "Linux":
        return ("bin-ubuntu-arm64" if arm else "bin-ubuntu-x64",
                f"Linux {'ARM64' if arm else 'x64'}")
    raise SystemExit(f"unsupported platform: {system} {machine}. "
                     "Build llama.cpp yourself and pass --llama-bin to the coordinator.")


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "sanad-setup"})
    with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as r:
        return json.loads(r.read())


def download(url: str, dest: Path, label: str) -> None:
    """Stream to a temp file with a progress line, then move into place."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "sanad-setup"})
    started = time.time()
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as fh:
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        last = 0.0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            now = time.time()
            if now - last > 0.3:
                last = now
                pct = f"{done * 100 // total}%" if total else human(done)
                speed = done / max(now - started, 0.1) / (1 << 20)
                print(f"\r  {label}: {pct}  ({human(done)}"
                      f"{' of ' + human(total) if total else ''}, {speed:.1f} MB/s)   ",
                      end="", flush=True)
    tmp.replace(dest)
    print(f"\r  {label}: done ({human(dest.stat().st_size)})" + " " * 25)


def extract(archive: Path, into: Path) -> None:
    into.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                # Flatten: releases nest binaries one directory deep.
                name = Path(member.filename).name
                if not name or member.is_dir():
                    continue
                with zf.open(member) as src, open(into / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                name = Path(member.name).name
                src = tf.extractfile(member)
                if src is None:
                    continue
                with open(into / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    if os.name != "nt":
        for f in into.iterdir():
            if f.is_file() and not f.suffix:
                f.chmod(0o755)


def installed_build() -> int | None:
    """Build number of the llama.cpp already in .local/bin, if any."""
    exe = BIN_DIR / ("llama-server.exe" if os.name == "nt" else "llama-server")
    if not exe.exists():
        return None
    import re
    import subprocess
    try:
        out = subprocess.run([str(exe), "--version"], capture_output=True, text=True,
                             timeout=30, cwd=str(BIN_DIR))
        m = re.search(r"version:\s*(\d+)", (out.stderr or "") + (out.stdout or ""))
        return int(m.group(1)) if m else None
    except Exception:
        return None


def report() -> dict:
    build = installed_build()
    models = {k: (MODELS_DIR / v["name"]).exists() for k, v in MODELS.items()}
    return {"llama_build": build, "models": models}


def print_report(state: dict) -> None:
    build = state["llama_build"]
    if build is None:
        print("  llama.cpp:  not installed")
    elif build < MIN_BUILD:
        print(f"  llama.cpp:  b{build}  — TOO OLD (needs b{MIN_BUILD}+ for CVE-2026-34159)")
    else:
        print(f"  llama.cpp:  b{build}  ok")
    for key, present in state["models"].items():
        print(f"  model ({key}): {'present' if present else 'missing'}  {MODELS[key]['name']}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Download everything Sanad needs into .local/ (nothing system-wide).")
    ap.add_argument("--yes", "-y", action="store_true", help="don't ask for confirmation")
    ap.add_argument("--check", action="store_true", help="report what is present and exit")
    ap.add_argument("--models", default="small,large",
                    help="which models to fetch: small, large, or small,large")
    args = ap.parse_args()

    print("Sanad setup\n")
    state = report()
    if args.check:
        print("Currently installed:")
        print_report(state)
        return

    pattern, label = asset_pattern()
    wanted = [m.strip() for m in args.models.split(",") if m.strip() in MODELS]

    need_llama = state["llama_build"] is None or state["llama_build"] < MIN_BUILD
    need_models = [k for k in wanted if not state["models"].get(k)]

    if not need_llama and not need_models:
        print("Everything is already in place:")
        print_report(state)
        print(f"\nNext:  python -m sanad_net.run")
        return

    release = fetch_json(LLAMA_API) if need_llama else {}
    asset = None
    if need_llama:
        asset = next((a for a in release.get("assets", []) if pattern in a["name"]), None)
        if asset is None:
            raise SystemExit(f"no llama.cpp release asset matching '{pattern}'. "
                             "Build it yourself and pass --llama-bin.")

    print("This will download:")
    total_mb = 0
    if asset is not None:
        size_mb = asset.get("size", 0) / (1 << 20)
        total_mb += size_mb
        print(f"  - llama.cpp {release.get('tag_name', '')} for {label}  (~{size_mb:.0f} MB)")
        print("      the inference engine; Sanad drives it, it is not ours")
    for k in need_models:
        total_mb += MODELS[k]["mb"]
        print(f"  - {MODELS[k]['name']}  (~{MODELS[k]['mb']} MB)")
        print(f"      {MODELS[k]['why']}")
    print(f"\nTotal ~{total_mb:.0f} MB into {LOCAL}")
    print("Nothing is installed system-wide; delete that folder to undo all of it.\n")

    if not args.yes:
        try:
            if input("Continue? [Y/n] ").strip().lower() not in ("", "y", "yes"):
                print("Cancelled.")
                return
        except EOFError:
            raise SystemExit("no terminal to ask; re-run with --yes")

    if asset is not None:
        archive = LOCAL / asset["name"]
        download(asset["browser_download_url"], archive, "llama.cpp")
        print("  extracting...", end="", flush=True)
        extract(archive, BIN_DIR)
        archive.unlink(missing_ok=True)
        build = installed_build()
        print(f"\r  extracted to {BIN_DIR}  (build b{build})" + " " * 20)
        if build is not None and build < MIN_BUILD:
            raise SystemExit(f"the latest release is b{build}, older than the required "
                             f"b{MIN_BUILD}. Not proceeding.")

    for k in need_models:
        download(MODELS[k]["url"], MODELS_DIR / MODELS[k]["name"], f"model ({k})")

    print("\nReady:")
    print_report(report())
    print("\nNext:  python -m sanad_net.run")


if __name__ == "__main__":
    main()
