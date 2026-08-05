"""Sanad client CLI.

    python -m sanad_net.client ask --user amina "Explain what Sanad is."
    python -m sanad_net.client status
"""

from __future__ import annotations

import argparse
import json
import urllib.request


def call(base: str, path: str, payload: dict | None = None) -> dict:
    if payload is None:
        with urllib.request.urlopen(f"{base}{path}", timeout=900) as resp:
            return json.loads(resp.read())
    req = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        return json.loads(resp.read())


def main() -> None:
    ap = argparse.ArgumentParser(description="Sanad client")
    ap.add_argument("--coordinator", default="http://127.0.0.1:7860")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ask = sub.add_parser("ask")
    ask.add_argument("prompt")
    ask.add_argument("--user", default="anon")
    ask.add_argument("--max-tokens", type=int, default=48)
    sub.add_parser("status")
    stmt = sub.add_parser("statement", help="wallet-style ledger statement")
    stmt.add_argument("--user", default=None, help="filter to one account")
    args = ap.parse_args()

    if args.cmd == "status":
        print(json.dumps(call(args.coordinator, "/status"), indent=2, ensure_ascii=False))
    elif args.cmd == "statement":
        entries = call(args.coordinator, "/ledger")["entries"]
        if args.user:
            entries = [e for e in entries if e["account"] == args.user]
        for e in entries:
            print(f"{e['delta']:+9.3f}  {e['account']:10s}  {e['reason']}")
        total = sum(e["delta"] for e in entries)
        print(f"{total:+9.3f}  TOTAL")
    else:
        result = call(args.coordinator, "/ask", {
            "user": args.user, "prompt": args.prompt, "max_tokens": args.max_tokens,
        })
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
