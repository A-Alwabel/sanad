"""Unit tests for sanad_net v0.2.1 (no llama.cpp binaries required)."""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sanad_net.coordinator import (  # noqa: E402
    CapacityError, ChainFailure, Coordinator, ModelTier, Registry,
    make_handler, parse_llama_log, pick_tier,
)
from sanad_net.ledger import Ledger  # noqa: E402
from sanad_net.node import SensorFSM  # noqa: E402

# Real log excerpt captured from llama.cpp b10276 (verbose) on 2026-08-05.
# Note: llama.cpp logs layer assignments once per load pass (2 passes observed);
# layer 2 below moves RPC0 -> RPC1 between passes — the LAST assignment wins.
SAMPLE_LOG = """
0.00.175.803 I llama_prepare_model_devices: using device RPC0 (127.0.0.1:50060) (unknown id) - 12337 MiB free
0.00.176.902 I llama_prepare_model_devices: using device RPC1 (127.0.0.1:50061) (unknown id) - 12341 MiB free
0.00.348.305 D load_tensors: layer   0 assigned to device RPC0, is_swa = 0
0.00.348.310 D load_tensors: layer   1 assigned to device RPC0, is_swa = 0
0.00.348.312 D load_tensors: layer   2 assigned to device RPC0, is_swa = 0
0.00.348.316 D load_tensors: layer  13 assigned to device RPC1, is_swa = 0
0.01.719.973 D load_tensors: layer   0 assigned to device RPC0, is_swa = 0
0.01.719.975 D load_tensors: layer   1 assigned to device RPC0, is_swa = 0
0.01.719.976 D load_tensors: layer   2 assigned to device RPC1, is_swa = 0
0.01.719.978 D load_tensors: layer  13 assigned to device RPC1, is_swa = 0
0.06.026.159 I common_perf_print: prompt eval time =      77.13 ms /     4 tokens (   19.28 ms per token,    51.86 tokens per second)
0.06.026.161 I common_perf_print:        eval time =    1025.62 ms /    31 runs   (   33.08 ms per token,    30.23 tokens per second)
"""

TIER_S = ModelTier(name="small", path=Path("small.gguf"), file_mb=470.0)   # needs 658
TIER_L = ModelTier(name="large", path=Path("large.gguf"), file_mb=1100.0)  # needs 1540
CATALOG = [TIER_S, TIER_L]


class FakeRunner:
    """Deterministic stand-in for llama-completion.

    Reports a shard map where the layer split follows the tensor_split ratio
    over 25 layers — mirroring the empirically verified --tensor-split behavior.
    `gate`: if set, run() blocks until the event is set (deterministic timing).
    `fail_times`: raise ChainFailure for the first N calls.
    `unprovable`: return an empty shard_map (parse-failure simulation).
    """

    def __init__(self, delay_s: float = 0.05, fail_times: int = 0,
                 gate: threading.Event | None = None, unprovable: bool = False):
        self.delay_s = delay_s
        self.fail_times = fail_times
        self.gate = gate
        self.unprovable = unprovable
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def run(self, model_path, prompt, rpc_servers, tensor_split, max_tokens) -> dict:
        with self._lock:
            self.calls.append(prompt)
            fail = self.fail_times > 0
            if fail:
                self.fail_times -= 1
        if self.gate is not None:
            self.gate.wait(timeout=10)
        if fail:
            raise ChainFailure("simulated chain failure (node vanished mid-job)")
        time.sleep(self.delay_s)
        if self.unprovable:
            return {"text": "???", "wall_s": self.delay_s, "shard_map": {},
                    "decode_tokens": 25, "tok_per_s": 60.0}
        total = sum(tensor_split)
        n_layers = [round(25 * s / total) for s in tensor_split]
        n_layers[-1] = 25 - sum(n_layers[:-1])
        shard_map, start = {}, 0
        for i, (ep, n) in enumerate(zip(rpc_servers, n_layers)):
            shard_map[f"RPC{i}"] = {"endpoint": ep, "layers": f"{start}-{start + n - 1}", "n_layers": n}
            start += n
        return {
            "text": f"echo:{prompt}", "wall_s": self.delay_s,
            "shard_map": shard_map, "decode_tokens": 25, "tok_per_s": 60.0,
        }


def mk_coord(runner=None, nodes=True) -> tuple[Coordinator, FakeRunner]:
    runner = runner or FakeRunner()
    coord = Coordinator(runner, CATALOG)
    coord.RETRY_DELAY_S = 0.05  # fast retries in tests
    if nodes:
        coord.registry.register("n1", "127.0.0.1", 50060, "amina", 1000)
        coord.registry.register("n2", "127.0.0.1", 50061, "bilal", 600)
    return coord, runner


class TestParseLlamaLog(unittest.TestCase):
    def test_shard_map_and_perf(self):
        out = parse_llama_log(SAMPLE_LOG)
        self.assertEqual(set(out["shard_map"]), {"RPC0", "RPC1"})
        self.assertEqual(out["shard_map"]["RPC0"]["endpoint"], "127.0.0.1:50060")
        self.assertEqual(out["shard_map"]["RPC0"]["layers"], "0-1")
        self.assertEqual(out["shard_map"]["RPC0"]["n_layers"], 2)  # duplicates deduped
        self.assertEqual(out["shard_map"]["RPC1"]["layers"], "2-13")  # last pass wins
        self.assertEqual(out["shard_map"]["RPC1"]["n_layers"], 2)
        self.assertEqual(out["decode_tokens"], 31)  # decode line, NOT the prompt-eval line
        self.assertAlmostEqual(out["tok_per_s"], 30.23)


class TestLedger(unittest.TestCase):
    def test_earn_spend_clamp(self):
        led = Ledger()
        led.earn("a", 10, "served")
        self.assertEqual(led.balance("a"), 10)
        spent = led.spend("a", 25, "big job")
        self.assertEqual(spent, 10)  # clamped — never negative
        self.assertEqual(led.balance("a"), 0)
        self.assertEqual(led.spend("ghost", 5, "x"), 0)

    def test_no_transfer_api_exists(self):
        # Non-tradeability is structural: the ledger must not grow a transfer path.
        self.assertFalse(hasattr(Ledger, "transfer"))


class TestRegistry(unittest.TestCase):
    def test_register_heartbeat_leave_suspect(self):
        reg = Registry()
        reg.register("n1", "127.0.0.1", 50060, "amina", 1000)
        self.assertTrue(reg.heartbeat("n1"))
        self.assertFalse(reg.heartbeat("nope"))
        self.assertEqual([n["node_id"] for n in reg.alive()], ["n1"])
        reg.suspect("n1")
        self.assertEqual(reg.alive(), [])          # dead until next heartbeat
        self.assertTrue(reg.heartbeat("n1"))
        self.assertEqual([n["node_id"] for n in reg.alive()], ["n1"])  # revived
        self.assertTrue(reg.leave("n1"))
        self.assertFalse(reg.leave("n1"))          # idempotent
        self.assertEqual(reg.alive(), [])

    def test_ttl_expiry(self):
        reg = Registry()
        reg.register("n1", "127.0.0.1", 50060, "amina", 1000)
        reg._nodes["n1"]["last_seen"] = time.time() - 999  # simulate silence
        self.assertEqual(reg.alive(), [])


class TestCapacityLadder(unittest.TestCase):
    def test_pick_tier_boundaries(self):
        self.assertIsNone(pick_tier(CATALOG, 100))
        self.assertIsNone(pick_tier(CATALOG, TIER_S.need_mb - 1))
        self.assertEqual(pick_tier(CATALOG, TIER_S.need_mb).name, "small")   # exact fit
        self.assertEqual(pick_tier(CATALOG, TIER_L.need_mb - 1).name, "small")
        self.assertEqual(pick_tier(CATALOG, TIER_L.need_mb).name, "large")
        self.assertEqual(pick_tier(CATALOG, 99999).name, "large")

    def test_ladder_events_on_join_and_leave(self):
        coord, _ = mk_coord(nodes=False)
        coord.registry.register("n1", "127.0.0.1", 50060, "amina", 1000)
        coord.current_tier()
        coord.registry.register("n2", "127.0.0.1", 50061, "bilal", 600)
        coord.current_tier()  # pool 1600 -> large
        coord.registry.leave("n2")
        coord.current_tier()  # pool 1000 -> small again
        transitions = [e["event"] for e in coord.events if e["event"].startswith("LADDER")]
        self.assertIn("small", transitions[0])
        self.assertIn("UP", transitions[1])
        self.assertIn("large", transitions[1])
        self.assertIn("DOWN", transitions[2])
        self.assertIn("small", transitions[2])

    def test_status_does_not_emit_ladder_events(self):
        coord, _ = mk_coord()
        before = len(coord.events)
        coord.status()
        coord.status()
        self.assertEqual(len(coord.events), before)


class TestSensorFSM(unittest.TestCase):
    def test_drain_after_sustained_busy(self):
        fsm = SensorFSM(busy_at=50, resume_at=25, busy_samples=2, calm_samples=3)
        self.assertIsNone(fsm.step(80, serving=True))      # 1st busy sample
        self.assertEqual(fsm.step(80, serving=True), "drain")  # 2nd -> drain

    def test_short_spike_ignored(self):
        fsm = SensorFSM(busy_at=50, resume_at=25, busy_samples=2, calm_samples=3)
        self.assertIsNone(fsm.step(80, serving=True))
        self.assertIsNone(fsm.step(10, serving=True))      # spike broken
        self.assertIsNone(fsm.step(80, serving=True))      # streak restarted

    def test_rejoin_after_sustained_calm(self):
        fsm = SensorFSM(busy_at=50, resume_at=25, busy_samples=2, calm_samples=3)
        self.assertIsNone(fsm.step(10, serving=False))
        self.assertIsNone(fsm.step(10, serving=False))
        self.assertEqual(fsm.step(10, serving=False), "rejoin")

    def test_failed_sample_resets_and_never_acts(self):
        fsm = SensorFSM(busy_at=50, resume_at=25, busy_samples=2, calm_samples=3)
        self.assertIsNone(fsm.step(80, serving=True))
        self.assertIsNone(fsm.step(None, serving=True))    # sample failed: reset
        self.assertIsNone(fsm.step(80, serving=True))      # streak starts over
        self.assertEqual(fsm.step(80, serving=True), "drain")
        # calm side too
        self.assertIsNone(fsm.step(10, serving=False))
        self.assertIsNone(fsm.step(None, serving=False))
        self.assertIsNone(fsm.step(10, serving=False))
        self.assertIsNone(fsm.step(10, serving=False))
        self.assertEqual(fsm.step(10, serving=False), "rejoin")


class TestCoordinatorScheduling(unittest.TestCase):
    def test_weighted_credits_follow_pledge_and_conserve(self):
        coord, _ = mk_coord()
        result = coord.submit("anon", "hello", 25)
        self.assertEqual(result["model"], "large")
        # pledges 1000:600 over 25 layers -> 16:9 layers -> credits 16:9 of 25 tokens
        self.assertAlmostEqual(coord.ledger.balance("amina"), 16.0, places=2)
        self.assertAlmostEqual(coord.ledger.balance("bilal"), 9.0, places=2)
        # conservation: total minted to operators == tokens == what anon was (maximally) charged
        minted = sum(e.delta for e in coord.ledger.entries() if e.delta > 0 and e.account != "anon")
        self.assertAlmostEqual(minted, 25.0, places=2)
        self.assertEqual(coord.ledger.balance("anon"), 0.0)  # clamped, still served

    def test_escrow_settlement_refund(self):
        coord, _ = mk_coord()
        coord.ledger.earn("maha", 100, "operated a node")
        coord.submit("maha", "q", 50)          # escrow 50, actual 25
        self.assertAlmostEqual(coord.ledger.balance("maha"), 75.0, places=2)
        reasons = [e.reason for e in coord.ledger.entries() if e.account == "maha"]
        self.assertTrue(any("escrow for job" in r for r in reasons))
        self.assertTrue(any("escrow refund" in r for r in reasons))

    def test_priority_orders_queue_deterministically(self):
        gate = threading.Event()
        coord, runner = mk_coord(FakeRunner(gate=gate))
        coord.ledger.earn("vip", 100, "operated a node")
        done: list[str] = []
        lock = threading.Lock()

        def ask(user, prompt):
            coord.submit(user, prompt, 10)
            with lock:
                done.append(user)

        threads = [threading.Thread(target=ask, args=("anon", "filler"))]
        threads[0].start()
        while not runner.calls:          # filler provably inside run()
            time.sleep(0.01)
        for user, prompt in [("zed", "zed-q"), ("vip", "vip-q")]:  # zed queued FIRST
            t = threading.Thread(target=ask, args=(user, prompt))
            t.start()
            threads.append(t)
            time.sleep(0.05)
        gate.set()                        # release everything
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(runner.calls[0], "filler")
        self.assertEqual(runner.calls[1], "vip-q")   # credits beat arrival order
        self.assertEqual(runner.calls[2], "zed-q")   # anonymous still served

    def test_anti_starvation_fifo_slot(self):
        gate = threading.Event()
        coord, runner = mk_coord(FakeRunner(gate=gate))
        for v in ("v1", "v2", "v3", "v4"):
            coord.ledger.earn(v, 1000, "operator")
        threads = [threading.Thread(target=coord.submit, args=("anon", "filler", 5))]
        threads[0].start()
        while not runner.calls:
            time.sleep(0.01)
        # zero-credit "zayd" enqueued BEFORE a stream of four credit-holders
        order = [("zayd", "zayd-q"), ("v1", "q1"), ("v2", "q2"), ("v3", "q3"), ("v4", "q4")]
        for user, prompt in order:
            t = threading.Thread(target=coord.submit, args=(user, prompt, 5))
            t.start()
            threads.append(t)
            time.sleep(0.05)
        gate.set()
        for t in threads:
            t.join(timeout=15)
        # Every 3rd slot is FIFO: zayd (earliest seq in queue) must be served
        # at slot 3 despite four higher-priority rivals.
        self.assertEqual(runner.calls[0], "filler")
        self.assertIn("zayd-q", runner.calls[:3])

    def test_timed_out_job_is_cancelled_refunded_and_never_runs(self):
        gate = threading.Event()
        coord, runner = mk_coord(FakeRunner(gate=gate))
        coord.ledger.earn("maha", 100, "operator")
        t1 = threading.Thread(target=lambda: coord.submit("anon", "filler", 5))
        t1.start()
        while not runner.calls:
            time.sleep(0.01)
        with self.assertRaises(TimeoutError):
            coord.submit("maha", "doomed", 40, timeout_s=0.2)   # queued behind filler
        self.assertAlmostEqual(coord.ledger.balance("maha"), 100.0, places=2)  # escrow refunded
        gate.set()
        t1.join(timeout=10)
        time.sleep(0.3)  # give the worker a chance to (wrongly) run it
        self.assertNotIn("doomed", runner.calls)   # cancelled job never executed

    def test_capacity_errors_fail_fast_without_repair(self):
        coord, runner = mk_coord(nodes=False)
        with self.assertRaises(RuntimeError) as ctx:
            coord.submit("anon", "hi", 5)
        self.assertIn("no live nodes", str(ctx.exception))
        self.assertEqual(runner.calls, [])         # runner never invoked
        self.assertFalse(any("chain failed" in e["event"] for e in coord.events))

    def test_pool_too_small_fails_fast(self):
        coord, runner = mk_coord(nodes=False)
        coord.registry.register("tiny", "127.0.0.1", 50060, "amina", 100)
        with self.assertRaises(RuntimeError) as ctx:
            coord.submit("anon", "hi", 5)
        self.assertIn("cannot hold", str(ctx.exception))
        self.assertFalse(any("chain failed" in e["event"] for e in coord.events))

    def test_chain_repair_evicts_stale_and_retries_with_survivor(self):
        gate = threading.Event()
        coord, runner = mk_coord(FakeRunner(gate=gate, fail_times=1))
        # Registration normally precedes jobs by seconds; Windows' coarse clock
        # can otherwise give registration and job-start the same timestamp.
        for nid in ("n1", "n2"):
            coord.registry._nodes[nid]["last_seen"] -= 1.0
        result_box: dict = {}

        def ask():
            result_box["r"] = coord.submit("anon", "hello", 25)

        t = threading.Thread(target=ask)
        t.start()
        while not runner.calls:
            time.sleep(0.01)
        coord.registry.heartbeat("n1")   # n1 heartbeats DURING the job -> fresh -> survives
        gate.set()                       # first attempt now fails; n2 is stale -> suspected
        t.join(timeout=10)
        r = result_box["r"]
        self.assertTrue(r["repaired"])
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual([n["node_id"] for n in r["pipeline"]], ["n1"])  # survivor only
        self.assertTrue(any("chain failed" in e["event"] for e in coord.events))

    def test_unprovable_shard_map_charges_and_pays_nobody(self):
        coord, _ = mk_coord(FakeRunner(unprovable=True))
        coord.ledger.earn("maha", 100, "operator")
        coord.submit("maha", "q", 50)
        self.assertAlmostEqual(coord.ledger.balance("maha"), 100.0, places=2)  # fully refunded
        self.assertEqual(coord.ledger.balance("amina"), 0.0)                    # nothing minted
        self.assertTrue(any("unprovable" in e["event"] for e in coord.events))


class TestHTTPRoundTrip(unittest.TestCase):
    def test_full_http_surface(self):
        coord, _ = mk_coord(nodes=False)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(coord))
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"

        def call(path, payload=None):
            if payload is None:
                with urllib.request.urlopen(f"{base}{path}", timeout=10) as r:
                    return json.loads(r.read())
            req = urllib.request.Request(
                f"{base}{path}", data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())

        try:
            call("/register", {"node_id": "n1", "host": "127.0.0.1", "port": 50060,
                               "operator": "amina", "pledge_mb": 1000})
            call("/register", {"node_id": "n2", "host": "127.0.0.1", "port": 50061,
                               "operator": "bilal", "pledge_mb": 600})
            st = call("/status")
            self.assertEqual(st["model"], "large")
            r = call("/ask", {"user": "anon", "prompt": "hi", "max_tokens": 25})
            self.assertEqual(r["decode_tokens"], 25)
            entries = call("/ledger")["entries"]
            minted = sum(e["delta"] for e in entries if e["delta"] > 0)
            self.assertAlmostEqual(minted, 25.0, places=2)
            self.assertTrue(call("/leave", {"node_id": "n2"})["ok"])
            self.assertEqual(call("/status")["model"], "small")
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
