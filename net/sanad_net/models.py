"""`python -m sanad_net.models` — find free open models that fit your network.

The capacity ladder only means something if there is somewhere to climb. This
finds real GGUF models on Hugging Face, ranked by how widely used they are,
filtered to what your pooled memory can actually hold, and adds them to your
ladder.

    python -m sanad_net.models                      # what fits the memory you have
    python -m sanad_net.models --pool-mb 32000      # what a bigger group could serve
    python -m sanad_net.models --search qwen3       # look for something specific
    python -m sanad_net.models --add <repo> --quant Q4_K_M   # download and add a rung

Everything it lists is free and open-weight: downloadable, no key, no
subscription. Licences are shown, never assumed — some open weights carry terms
you should read before running them for other people.

Stdlib only, like the rest of Sanad.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .setup import MODELS_DIR, download, human

HF_API = "https://huggingface.co/api"
UA = {"User-Agent": "sanad-models"}

# Quantizations worth offering, best-value first. Q4_K_M is the usual sweet
# spot; the smaller ones exist so a modest group can still reach a big model.
QUANT_PREFERENCE = ["Q4_K_M", "Q4_K_S", "IQ4_XS", "Q5_K_M", "Q3_K_M", "IQ3_M", "Q6_K", "Q8_0"]

# Licences that are genuinely "download it and run it". Others are shown with a
# warning rather than hidden — the user decides, but never by accident.
PERMISSIVE = {"apache-2.0", "mit", "bsd-3-clause", "cc-by-4.0", "openrail", "cc0-1.0"}


def api(path: str, params: dict | None = None) -> list | dict:
    url = f"{HF_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def parse_params(name: str) -> float | None:
    """Rough parameter count from a repo name, in billions. MoE names like
    '30B-A3B' report total size, which is what memory has to hold."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])", name.replace("_", "-"))
    return float(m.group(1)) if m else None


def search_models(query: str | None, limit: int = 40) -> list[dict]:
    params = {"filter": "gguf", "sort": "downloads", "direction": "-1", "limit": limit}
    if query:
        params["search"] = query
    out = []
    for m in api("/models", params):
        mid = m.get("id", "")
        if not mid or "embed" in mid.lower():          # embedding models aren't chat models
            continue
        out.append({
            "id": mid,
            "downloads": m.get("downloads", 0),
            "likes": m.get("likes", 0),
            "params_b": parse_params(mid),
        })
    return out


def community_ratings(coordinator: str | None) -> dict[str, dict]:
    """Per-model standing from your OWN network's users, keyed by model name.

    This is the signal that matters: how a model performs *here*, not how
    popular it is on a hub. Download count is a fallback for models nobody in
    your community has tried yet (cold start).
    """
    if not coordinator:
        return {}
    try:
        with urllib.request.urlopen(f"{coordinator}/feedback", timeout=10) as r:
            data = json.loads(r.read())
        return {row["model"]: row for row in data.get("ranking", [])}
    except Exception:
        return {}


def _model_name(repo: str, filename: str) -> str:
    """The catalog name a downloaded file gets — the GGUF stem, matching how
    the coordinator names tiers, so ratings line up with repos."""
    return Path(filename).stem


# Files that end in .gguf but are NOT a loadable chat model:
#  - mmproj / *proj*  : the vision projector for multimodal repos, useless alone
#  - a split shard    : "...-00001-of-00003.gguf" is one piece; llama.cpp wants
#                       the first shard, but a lone middle shard is unusable, so
#                       we only accept a shard set via its "00001-of" member.
_NOT_A_MODEL = re.compile(r"(mmproj|-proj|\bproj\b|vision|clip)", re.IGNORECASE)
_SPLIT = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)


def repo_files(repo: str) -> list[dict]:
    """Loadable GGUF chat-model files in a repo, with their real total sizes.

    Excludes vision projectors and reduces a split model to a single entry
    (the first shard) whose size is the sum of all its shards.
    """
    try:
        tree = api(f"/models/{repo}/tree/main")
    except (urllib.error.HTTPError, urllib.error.URLError):
        return []
    split_groups: dict[str, list[dict]] = {}
    files: list[dict] = []
    for f in tree:
        path = f.get("path", "")
        if not path.endswith(".gguf") or "/" in path:      # top-level only
            continue
        if _NOT_A_MODEL.search(path):                       # projectors etc.
            continue
        size = int(f.get("size") or (f.get("lfs") or {}).get("size") or 0)
        m = _SPLIT.search(path)
        if m:
            key = path[: m.start()]
            split_groups.setdefault(key, []).append({"path": path, "bytes": size,
                                                      "idx": int(m.group(1)),
                                                      "total": int(m.group(2))})
        else:
            files.append({"path": path, "bytes": size})
    # A split set becomes one downloadable entry: its first shard, sized as the whole.
    for key, shards in split_groups.items():
        if len(shards) != shards[0]["total"]:
            continue                                        # incomplete upload, skip
        first = min(shards, key=lambda s: s["idx"])
        files.append({"path": first["path"], "bytes": sum(s["bytes"] for s in shards),
                      "split": True})
    return files


def repo_license(repo: str) -> str:
    try:
        info = api(f"/models/{repo}")
    except (urllib.error.HTTPError, urllib.error.URLError):
        return "unknown"
    card = info.get("cardData") or {}
    return str(card.get("license") or "unknown")


def rank_quant(filename: str) -> int:
    upper = filename.upper()
    for i, q in enumerate(QUANT_PREFERENCE):
        if q in upper:
            return i
    return len(QUANT_PREFERENCE)


def best_fitting_file(files: list[dict], budget_mb: float) -> dict | None:
    """The best-quality quant that fits the memory budget.

    A model needs more than its file size at run time (KV cache, overhead), so
    the caller's budget should already be the *usable* share of the pool.
    """
    fitting = [f for f in files if f["bytes"] / 1e6 <= budget_mb]
    if not fitting:
        return None
    fitting.sort(key=lambda f: (rank_quant(f["path"]), -f["bytes"]))
    return fitting[0]


def usable_budget(pool_mb: float) -> float:
    """Pooled pledge minus what the runtime needs beyond the weights.

    Mirrors the coordinator's ladder maths (MODEL_OVERHEAD + KV per slot), so
    what this tool offers is what the ladder will actually accept.
    """
    from .coordinator import KV_PER_SLOT, MODEL_OVERHEAD
    return pool_mb / (MODEL_OVERHEAD + KV_PER_SLOT * 4)


def current_pool_mb(coordinator: str | None) -> float | None:
    if not coordinator:
        return None
    try:
        with urllib.request.urlopen(f"{coordinator}/status", timeout=10) as r:
            return float(json.loads(r.read())["pool_mb"])
    except Exception:
        return None


def cmd_list(args: argparse.Namespace) -> None:
    pool = args.pool_mb or current_pool_mb(args.coordinator)
    if pool is None:
        print("No pool size given and no running network found.")
        print("Pass --pool-mb <MB> to see what a group of that size could serve.\n")
        pool = 8000
        print(f"Showing what {human(pool * 1e6)} of pooled memory would reach:\n")
    else:
        print(f"Pooled memory: {human(pool * 1e6)}"
              f"{' (from your running network)' if not args.pool_mb else ''}\n")
    budget = usable_budget(pool)

    print(f"Searching Hugging Face for free GGUF models"
          f"{' matching ' + repr(args.search) if args.search else ''}...\n")
    candidates = search_models(args.search, limit=args.limit)

    shown = 0
    for c in candidates:
        if shown >= args.top:
            break
        files = repo_files(c["id"])
        if not files:
            continue
        pick = best_fitting_file(files, budget)
        smallest = min(files, key=lambda f: f["bytes"])
        lic = repo_license(c["id"])
        flag = "" if lic.lower() in PERMISSIVE else f"  [licence: {lic} - read it]"
        if pick:
            print(f"  FITS   {c['id']}")
            print(f"         {pick['path']}  {human(pick['bytes'])}"
                  f"   {c['downloads']:,} downloads{flag}")
            print(f"         add it:  python -m sanad_net.models --add {c['id']} "
                  f"--quant {pick['path']}")
        else:
            need = smallest["bytes"] / 1e6 * (usable_budget(1) ** -1)
            print(f"  too big  {c['id']}  (smallest quant {human(smallest['bytes'])}, "
                  f"needs ~{human(need * 1e6)} pooled){flag}")
        print()
        shown += 1

    print("Everything above is free and open-weight: no key, no subscription.")
    print("Add a bigger rung and the network climbs to it as soon as enough")
    print("memory is pledged - that is the whole point of the ladder.")


def cmd_add(args: argparse.Namespace) -> None:
    repo = args.add
    files = repo_files(repo)
    if not files:
        raise SystemExit(f"no GGUF files found in {repo} (is the repo id right?)")

    if args.quant and args.quant.endswith(".gguf"):
        chosen = next((f for f in files if f["path"] == args.quant), None)
    else:
        want = (args.quant or "Q4_K_M").upper()
        chosen = next((f for f in files if want in f["path"].upper()), None)
    if chosen is None:
        print(f"'{args.quant}' not found in {repo}. Available:")
        for f in sorted(files, key=lambda f: f["bytes"])[:20]:
            print(f"   {f['path']:<55} {human(f['bytes'])}")
        raise SystemExit(1)

    lic = repo_license(repo)
    print(f"\n  {repo}")
    print(f"  file:     {chosen['path']}")
    print(f"  size:     {human(chosen['bytes'])}")
    print(f"  licence:  {lic}"
          + ("" if lic.lower() in PERMISSIVE else "   <- not a standard permissive licence; "
                                                  "read it before serving others with this"))
    # A split model needs every shard; llama.cpp opens the set from the first.
    to_get = [chosen["path"]]
    if chosen.get("split"):
        m = _SPLIT.search(chosen["path"])
        stem, total = chosen["path"][: m.start()], int(m.group(2))
        to_get = [f"{stem}-{i:05d}-of-{total:05d}.gguf" for i in range(1, total + 1)]
        print(f"  (split model: {total} shards)")

    dest = MODELS_DIR / chosen["path"]
    if dest.exists():
        print(f"\nAlready downloaded: {dest}")
    else:
        if not args.yes:
            try:
                if input("\nDownload? [Y/n] ").strip().lower() not in ("", "y", "yes"):
                    print("Cancelled.")
                    return
            except EOFError:
                raise SystemExit("no terminal to ask; re-run with --yes")
        for name in to_get:
            url = f"https://huggingface.co/{repo}/resolve/main/{name}"
            download(url, MODELS_DIR / name, name)

    print(f"\nAdded. Include it in your ladder by passing it to the coordinator:")
    print(f"    --models <your other models>,{dest}")
    print("The ladder sorts rungs by size automatically and serves the largest")
    print("one the pledged memory can hold.")


def catalog_on_disk() -> list[dict]:
    """The GGUF models already available to serve, smallest first."""
    if not MODELS_DIR.exists():
        return []
    files = [{"path": f.name, "bytes": f.stat().st_size}
             for f in MODELS_DIR.iterdir() if f.suffix == ".gguf"]
    return sorted(files, key=lambda f: f["bytes"])


def best_available(pool_mb: float) -> dict | None:
    """The best model already downloaded that the current pool can serve."""
    return best_fitting_file(catalog_on_disk(), usable_budget(pool_mb))


def scout_once(pool_mb: float, search: str | None, limit: int,
               coordinator: str | None = None) -> dict | None:
    """Is there a better model than what's on disk that the pool could serve?

    "Better" is judged first by how models perform with YOUR users (community
    ratings from the running coordinator), and only then by download popularity
    for models nobody here has tried yet. Returns the single best candidate to
    add, or None. Never downloads anything.
    """
    budget = usable_budget(pool_mb)
    have = best_available(pool_mb)
    have_bytes = have["bytes"] if have else 0
    ratings = community_ratings(coordinator)
    candidates = []
    for c in search_models(search, limit=limit):
        files = repo_files(c["id"])
        pick = best_fitting_file(files, budget)
        if not pick:
            continue
        # Only worth suggesting if it's a meaningfully bigger rung we lack.
        if pick["bytes"] <= have_bytes * 1.3 or (MODELS_DIR / pick["path"]).exists():
            continue
        lic = repo_license(c["id"])
        name = _model_name(c["id"], pick["path"])
        rated = ratings.get(name)
        candidates.append({
            "repo": c["id"], "file": pick["path"], "bytes": pick["bytes"],
            "downloads": c["downloads"], "license": lic,
            "permissive": lic.lower() in PERMISSIVE,
            # community score if this exact model has been rated here; else -1
            # so unrated models fall behind any that your users have judged.
            "community_score": rated["score"] if rated else -1.0,
            "community_ratings": rated["total"] if rated else 0,
        })
    if not candidates:
        return None
    # Rank: a model your users actually liked beats a bigger unknown one; among
    # equally-(un)rated models, prefer the bigger rung.
    candidates.sort(key=lambda c: (c["community_score"], c["bytes"]), reverse=True)
    return candidates[0]


def cmd_watch(args: argparse.Namespace) -> None:
    """The Scout: periodically check whether the network has outgrown its model,
    and propose (or, only with --auto-fetch, download) a better one.

    It never installs or switches a model on its own without --auto-fetch, and
    even then it only DOWNLOADS — bringing a model into the live ladder is still
    a human passing it to the coordinator. Automatic model *selection* is a
    governance decision and a supply-chain risk: a poisoned model topping a
    popularity list must never be able to deploy itself across the network.
    """
    import time as _time
    print("Scout watching for models your network could grow into.")
    print("It suggests; it does not silently change what you serve"
          + (" (--auto-fetch: it will download, but you still add it)." if args.auto_fetch
             else ".") + "\n")
    seen: set[str] = set()
    while True:
        pool = args.pool_mb or current_pool_mb(args.coordinator)
        if pool is None:
            print("(no running network and no --pool-mb; nothing to size against)")
            return
        cand = scout_once(pool, args.search, args.limit, coordinator=args.coordinator)
        if cand and cand["file"] not in seen:
            seen.add(cand["file"])
            print(f"[scout] your pool is now {human(pool * 1e6)}. A bigger model fits:")
            lic = "" if cand["permissive"] else f"  (licence: {cand['license']} - read it)"
            print(f"        {cand['repo']}")
            print(f"        {cand['file']}  {human(cand['bytes'])}  "
                  f"{cand['downloads']:,} downloads{lic}")
            if args.auto_fetch and cand["permissive"]:
                print("        --auto-fetch is on: downloading (you still add it to --models)")
                url = f"https://huggingface.co/{cand['repo']}/resolve/main/{cand['file']}"
                try:
                    download(url, MODELS_DIR / cand["file"], cand["file"])
                except Exception as exc:
                    print(f"        download failed: {exc}")
            else:
                print(f"        add it:  python -m sanad_net.models --add {cand['repo']} "
                      f"--quant {cand['file']}")
            print()
        if args.once:
            return
        _time.sleep(args.interval)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Find and add free open-weight models to your capacity ladder.")
    ap.add_argument("--search", help="filter by name, e.g. qwen3, llama, gemma")
    ap.add_argument("--pool-mb", type=float, help="pooled memory to plan for (MB)")
    ap.add_argument("--coordinator", default="http://127.0.0.1:7860",
                    help="running network to read the real pool size from")
    ap.add_argument("--top", type=int, default=8, help="how many to show")
    ap.add_argument("--limit", type=int, default=40, help="how many candidates to consider")
    ap.add_argument("--add", metavar="REPO", help="download a model and add it to the ladder")
    ap.add_argument("--quant", help="which quantization (default Q4_K_M), or an exact filename")
    ap.add_argument("--watch", action="store_true",
                    help="the Scout: keep checking whether the network could serve a "
                         "bigger model, and suggest one")
    ap.add_argument("--auto-fetch", action="store_true",
                    help="with --watch: also DOWNLOAD a suggested permissive-licence model "
                         "(you still add it to --models yourself)")
    ap.add_argument("--interval", type=float, default=1800.0,
                    help="with --watch: seconds between checks (default 30 min)")
    ap.add_argument("--once", action="store_true", help="with --watch: check once and exit")
    ap.add_argument("--yes", "-y", action="store_true")
    args = ap.parse_args()

    if args.watch:
        cmd_watch(args)
    elif args.add:
        cmd_add(args)
    else:
        cmd_list(args)


if __name__ == "__main__":
    main()
