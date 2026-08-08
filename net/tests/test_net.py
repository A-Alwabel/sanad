"""Unit tests for sanad_net (no llama.cpp binaries or model required)."""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sanad_net.coordinator import (  # noqa: E402
    NODE_TTL_S, CapacityError, ChainFailure, Coordinator, ModelTier, Registry,
    make_handler, pick_tier,
)
from sanad_net.engine import EngineError, parse_shard_map  # noqa: E402
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
"""

TIER_S = ModelTier(name="small", path=Path("small.gguf"), file_mb=470.0)   # needs 658
TIER_L = ModelTier(name="large", path=Path("large.gguf"), file_mb=1100.0)  # needs 1540
CATALOG = [TIER_S, TIER_L]


class FakeEngine:
    """Stand-in for a resident llama-server.

    Reports a shard map whose layer split follows the tensor_split ratio over
    25 layers, mirroring the empirically verified --tensor-split behavior.
    """

    def __init__(self, mgr, model_path, nodes):
        self.mgr = mgr
        self.model_path = model_path
        self.nodes = nodes
        self.started_at = time.time()
        self.load_s = 0.01
        total = sum(float(n["pledge_mb"]) for n in nodes) or 1.0
        counts = [round(25 * float(n["pledge_mb"]) / total) for n in nodes]
        counts[-1] = 25 - sum(counts[:-1])
        self.shard_map, start = {}, 0
        for i, (n, c) in enumerate(zip(nodes, counts)):
            self.shard_map[f"RPC{i}"] = {
                "endpoint": f"{n['host']}:{n['port']}",
                "layers": f"{start}-{start + c - 1}", "n_layers": c,
            }
            start += c

    def alive(self):
        return True

    def chat(self, messages, max_tokens, on_token=None, temperature=0.7):
        self.mgr.calls.append(messages[-1]["content"])
        self.mgr.message_lists.append(list(messages))
        if self.mgr.fail_times > 0:
            self.mgr.fail_times -= 1
            raise EngineError("simulated pipeline failure (node vanished mid-job)")
        if self.mgr.gate is not None:
            self.mgr.gate.wait(timeout=10)
        time.sleep(self.mgr.delay_s)
        for piece in ("hel", "lo ", "world"):
            if on_token:
                on_token(piece)
        if self.mgr.unprovable:
            self.shard_map = {}
        return {"text": f"echo:{messages[-1]['content']}", "decode_tokens": 25,
                "tok_per_s": 60.0, "wall_s": self.mgr.delay_s, "ttft_s": 0.01}

    def complete(self, prompt, max_tokens, on_token=None):
        return self.chat([{"role": "user", "content": prompt}], max_tokens, on_token)

    def stop(self):
        pass


class FakeEngines:
    """Stand-in EngineManager: same warm/cold semantics, no subprocess."""

    def __init__(self, delay_s=0.05, fail_times=0, gate=None, unprovable=False):
        self.delay_s = delay_s
        self.fail_times = fail_times
        self.gate = gate
        self.unprovable = unprovable
        self.calls: list[str] = []
        self.message_lists: list[list] = []
        self.engine = None
        self.signature = None
        self.restarts = 0
        self.starts = 0
        self.on_event = lambda msg: None

    @staticmethod
    def _sig(model_path, nodes):
        return (str(model_path), tuple((n["node_id"], n["pledge_mb"]) for n in nodes))

    def ensure(self, model_path, nodes):
        sig = self._sig(model_path, nodes)
        if self.engine is not None and self.signature == sig:
            return self.engine                   # warm
        if self.engine is not None:
            self.restarts += 1
        self.engine = FakeEngine(self, model_path, nodes)
        self.signature = sig
        self.starts += 1
        return self.engine

    def invalidate(self):
        self.signature = None

    def stop(self):
        self.engine = None


def mk_coord(engines=None, nodes=True, concurrency=4) -> tuple[Coordinator, FakeEngines]:
    engines = engines or FakeEngines()
    coord = Coordinator(engines, CATALOG, concurrency=concurrency)
    coord.RETRY_DELAY_S = 0.05  # fast retries in tests
    if nodes:
        coord.registry.register("n1", "127.0.0.1", 50060, "amina", 1000)
        coord.registry.register("n2", "127.0.0.1", 50061, "bilal", 600)
    return coord, engines


class TestParseShardMap(unittest.TestCase):
    def test_shard_map(self):
        sm = parse_shard_map(SAMPLE_LOG)
        self.assertEqual(set(sm), {"RPC0", "RPC1"})
        self.assertEqual(sm["RPC0"]["endpoint"], "127.0.0.1:50060")
        self.assertEqual(sm["RPC0"]["layers"], "0-1")
        self.assertEqual(sm["RPC0"]["n_layers"], 2)   # duplicate passes deduped
        self.assertEqual(sm["RPC1"]["layers"], "2-13")  # last pass wins
        self.assertEqual(sm["RPC1"]["n_layers"], 2)

    def test_empty_log(self):
        self.assertEqual(parse_shard_map("nothing to see"), {})


class TestLedger(unittest.TestCase):
    def test_earn_spend_clamp(self):
        led = Ledger()
        led.earn("a", 10, "served")
        self.assertEqual(led.balance("a"), 10)
        self.assertEqual(led.spend("a", 25, "big job"), 10)  # clamped — never negative
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
        self.assertEqual(reg.alive(), [])            # dead until next heartbeat
        self.assertTrue(reg.heartbeat("n1"))
        self.assertEqual([n["node_id"] for n in reg.alive()], ["n1"])   # revived
        self.assertTrue(reg.leave("n1"))
        self.assertFalse(reg.leave("n1"))            # idempotent
        self.assertEqual(reg.alive(), [])

    def test_ttl_expiry(self):
        reg = Registry()
        reg.register("n1", "127.0.0.1", 50060, "amina", 1000)
        reg._nodes["n1"]["last_seen"] = time.time() - 999
        self.assertEqual(reg.alive(), [])


class TestCapacityLadder(unittest.TestCase):
    def test_pick_tier_boundaries(self):
        self.assertIsNone(pick_tier(CATALOG, 100))
        self.assertIsNone(pick_tier(CATALOG, TIER_S.need_mb - 1))
        self.assertEqual(pick_tier(CATALOG, TIER_S.need_mb).name, "small")   # exact fit
        self.assertEqual(pick_tier(CATALOG, TIER_L.need_mb - 1).name, "small")
        self.assertEqual(pick_tier(CATALOG, TIER_L.need_mb).name, "large")

    def test_ladder_events_on_join_and_leave(self):
        coord, _ = mk_coord(nodes=False)
        coord.registry.register("n1", "127.0.0.1", 50060, "amina", 1000)
        coord.current_tier()
        coord.registry.register("n2", "127.0.0.1", 50061, "bilal", 600)
        coord.current_tier()   # pool 1600 -> large
        coord.registry.leave("n2")
        coord.current_tier()   # pool 1000 -> small again
        t = [e["event"] for e in coord.events if e["event"].startswith("LADDER")]
        self.assertIn("small", t[0])
        self.assertIn("UP", t[1]); self.assertIn("large", t[1])
        self.assertIn("DOWN", t[2]); self.assertIn("small", t[2])

    def test_status_does_not_emit_ladder_events(self):
        coord, _ = mk_coord()
        before = len(coord.events)
        coord.status(); coord.status()
        self.assertEqual(len(coord.events), before)


class TestResidentEngine(unittest.TestCase):
    def test_second_request_reuses_the_pipeline(self):
        coord, engines = mk_coord()
        first = coord.submit("anon", "one", 25)
        second = coord.submit("anon", "two", 25)
        self.assertFalse(first["engine_warm"])       # built for this request
        self.assertTrue(second["engine_warm"])       # reused, no reload
        self.assertEqual(engines.starts, 1)
        self.assertEqual(engines.restarts, 0)

    def test_membership_change_rebuilds_the_pipeline(self):
        coord, engines = mk_coord()
        coord.submit("anon", "one", 25)
        coord.registry.leave("n2")                   # pipeline changed -> must rebuild
        coord.submit("anon", "two", 25)
        self.assertEqual(engines.starts, 2)
        self.assertEqual(engines.restarts, 1)

    def test_streaming_pushes_tokens_then_done(self):
        import queue
        coord, _ = mk_coord()
        q: queue.Queue = queue.Queue()
        result = coord.submit("anon", "hi", 25, stream_q=q)
        kinds = []
        while not q.empty():
            kinds.append(q.get_nowait()[0])
        self.assertEqual(kinds, ["token", "token", "token", "done"])
        self.assertEqual(result["decode_tokens"], 25)


class TestSensorFSM(unittest.TestCase):
    def test_drain_after_sustained_busy(self):
        fsm = SensorFSM(50, 25, busy_samples=2, calm_samples=3)
        self.assertIsNone(fsm.step(80, serving=True))
        self.assertEqual(fsm.step(80, serving=True), "drain")

    def test_short_spike_ignored(self):
        fsm = SensorFSM(50, 25, busy_samples=2, calm_samples=3)
        self.assertIsNone(fsm.step(80, serving=True))
        self.assertIsNone(fsm.step(10, serving=True))    # spike broken
        self.assertIsNone(fsm.step(80, serving=True))    # streak restarted

    def test_rejoin_after_sustained_calm(self):
        fsm = SensorFSM(50, 25, busy_samples=2, calm_samples=3)
        self.assertIsNone(fsm.step(10, serving=False))
        self.assertIsNone(fsm.step(10, serving=False))
        self.assertEqual(fsm.step(10, serving=False), "rejoin")

    def test_failed_sample_resets_and_never_acts(self):
        fsm = SensorFSM(50, 25, busy_samples=2, calm_samples=3)
        self.assertIsNone(fsm.step(80, serving=True))
        self.assertIsNone(fsm.step(None, serving=True))  # sample failed: reset
        self.assertIsNone(fsm.step(80, serving=True))
        self.assertEqual(fsm.step(80, serving=True), "drain")


class TestSamplerSelection(unittest.TestCase):
    def test_make_sampler_returns_a_working_sampler(self):
        from sanad_net.node import make_sampler
        s = make_sampler()
        self.assertTrue(hasattr(s, "other_load_percent"))
        first = s.other_load_percent(None)           # may be None on first sample
        self.assertTrue(first is None or 0.0 <= first <= 100.0)
        second = s.other_load_percent(None)
        self.assertTrue(second is None or 0.0 <= second <= 100.0)


class TestCoordinatorScheduling(unittest.TestCase):
    def test_weighted_credits_follow_pledge_and_conserve(self):
        coord, _ = mk_coord()
        result = coord.submit("anon", "hello", 25)
        self.assertEqual(result["model"], "large")
        # pledges 1000:600 over 25 layers -> 16:9 layers -> credits 16:9 of 25 tokens
        self.assertAlmostEqual(coord.ledger.balance("amina"), 16.0, places=2)
        self.assertAlmostEqual(coord.ledger.balance("bilal"), 9.0, places=2)
        minted = sum(e.delta for e in coord.ledger.entries() if e.delta > 0 and e.account != "anon")
        self.assertAlmostEqual(minted, 25.0, places=2)
        self.assertEqual(coord.ledger.balance("anon"), 0.0)   # clamped, still served

    def test_escrow_settlement_refund(self):
        coord, _ = mk_coord()
        coord.ledger.earn("maha", 100, "operated a node")
        coord.submit("maha", "q", 50)            # escrow 50, actual 25
        self.assertAlmostEqual(coord.ledger.balance("maha"), 75.0, places=2)
        reasons = [e.reason for e in coord.ledger.entries() if e.account == "maha"]
        self.assertTrue(any("escrow for job" in r for r in reasons))
        self.assertTrue(any("escrow refund" in r for r in reasons))

    def test_credits_buy_priority_when_the_server_is_saturated(self):
        # Priority is rate x waiting time: a well-funded account accrues faster,
        # so it overtakes a guest who was queued first. It cannot skip an
        # arbitrarily long queue — see the starvation-bound test below.
        gate = threading.Event()
        coord, engines = mk_coord(FakeEngines(gate=gate), concurrency=1)
        coord.ledger.earn("vip", 100_000, "operated many nodes")
        threads = [threading.Thread(target=coord.submit, args=("anon", "filler", 10))]
        threads[0].start()
        while not engines.calls:
            time.sleep(0.01)
        for user, prompt in [("zed", "zed-q"), ("vip", "vip-q")]:   # zed queued FIRST
            t = threading.Thread(target=coord.submit, args=(user, prompt, 10))
            t.start(); threads.append(t); time.sleep(0.05)
        gate.set()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(engines.calls[0], "filler")
        self.assertEqual(engines.calls[1], "vip-q")   # credits won despite arriving later
        self.assertEqual(engines.calls[2], "zed-q")   # the guest is still served

    def test_a_guest_who_waits_long_enough_overtakes_a_richer_newcomer(self):
        # The property static-priority queueing does NOT have (Cobham 1954):
        # a zero-credit job's score keeps climbing, so it cannot be deferred
        # forever by a stream of better-funded arrivals.
        coord, _ = mk_coord(nodes=False, concurrency=1)
        now = time.time()
        guest = {"user": "zed", "priority": 0.0, "seq": 1, "submitted_at": now - 60}
        whale = {"user": "vip", "priority": -100_000.0, "seq": 2, "submitted_at": now - 1}
        coord._pending = [guest, whale]
        coord._served = 1                     # not a reserve-lane slot
        picked = coord._pop_next_locked()
        self.assertIs(picked, guest, "a long-waiting guest must eventually win")

    def test_reserve_lane_rotates_across_accounts_not_jobs(self):
        # One guest flooding the queue must not own the whole reserve lane and
        # starve the other guests it exists to protect.
        coord, _ = mk_coord(nodes=False, concurrency=1)
        now = time.time()
        coord._pending = [
            {"user": "flooder", "priority": 0.0, "seq": 1, "submitted_at": now - 10},
            {"user": "flooder", "priority": 0.0, "seq": 2, "submitted_at": now - 9},
            {"user": "quiet", "priority": 0.0, "seq": 3, "submitted_at": now - 8},
        ]
        coord._served = 2                     # next pop is the reserve lane
        first = coord._pop_next_locked()
        self.assertEqual(first["user"], "flooder")
        coord._served = 2                     # reserve lane again
        second = coord._pop_next_locked()
        self.assertEqual(second["user"], "quiet",
                         "the lane must rotate to an account that has not just been served")

    def test_anti_starvation_fifo_slot(self):
        gate = threading.Event()
        coord, engines = mk_coord(FakeEngines(gate=gate), concurrency=1)
        for v in ("v1", "v2", "v3", "v4"):
            coord.ledger.earn(v, 1000, "operator")
        threads = [threading.Thread(target=coord.submit, args=("anon", "filler", 5))]
        threads[0].start()
        while not engines.calls:
            time.sleep(0.01)
        for user, prompt in [("zayd", "zayd-q"), ("v1", "q1"), ("v2", "q2"),
                             ("v3", "q3"), ("v4", "q4")]:
            t = threading.Thread(target=coord.submit, args=(user, prompt, 5))
            t.start(); threads.append(t); time.sleep(0.05)
        gate.set()
        for t in threads:
            t.join(timeout=15)
        # every 3rd slot is FIFO: the zero-credit user must be served at slot 3
        self.assertEqual(engines.calls[0], "filler")
        self.assertIn("zayd-q", engines.calls[:3])

    def test_timed_out_job_is_cancelled_refunded_and_never_runs(self):
        gate = threading.Event()
        coord, engines = mk_coord(FakeEngines(gate=gate), concurrency=1)
        coord.ledger.earn("maha", 100, "operator")
        t1 = threading.Thread(target=lambda: coord.submit("anon", "filler", 5))
        t1.start()
        while not engines.calls:
            time.sleep(0.01)
        with self.assertRaises(TimeoutError):
            coord.submit("maha", "doomed", 40, timeout_s=0.2)
        self.assertAlmostEqual(coord.ledger.balance("maha"), 100.0, places=2)  # refunded
        gate.set()
        t1.join(timeout=10)
        time.sleep(0.3)
        self.assertNotIn("doomed", engines.calls)     # cancelled job never executed

    def test_capacity_errors_fail_fast_without_repair(self):
        coord, engines = mk_coord(nodes=False)
        with self.assertRaises(RuntimeError) as ctx:
            coord.submit("anon", "hi", 5)
        self.assertIn("no live nodes", str(ctx.exception))
        self.assertEqual(engines.calls, [])
        self.assertFalse(any("chain failed" in e["event"] for e in coord.events))

    def test_pool_too_small_fails_fast(self):
        coord, _ = mk_coord(nodes=False)
        coord.registry.register("tiny", "127.0.0.1", 50060, "amina", 100)
        with self.assertRaises(RuntimeError) as ctx:
            coord.submit("anon", "hi", 5)
        self.assertIn("cannot hold", str(ctx.exception))

    def test_chain_repair_evicts_stale_and_retries_with_survivor(self):
        gate = threading.Event()
        coord, engines = mk_coord(FakeEngines(gate=gate, fail_times=1), concurrency=1)
        # n2 has been genuinely silent past the TTL; n1 is heartbeating normally.
        # A node that is merely quiet for a second must NOT be evicted because
        # some other request failed.
        coord.registry._nodes["n2"]["last_seen"] = time.time() - (NODE_TTL_S + 5)
        box: dict = {}
        t = threading.Thread(target=lambda: box.update(r=coord.submit("anon", "hello", 25)))
        t.start()
        while not engines.calls:
            time.sleep(0.01)
        coord.registry.heartbeat("n1")   # n1 is fresh -> survives; n2 is silent -> evicted
        gate.set()
        t.join(timeout=10)
        r = box["r"]
        self.assertTrue(r["repaired"])
        self.assertEqual(len(engines.calls), 2)
        self.assertEqual([n["node_id"] for n in r["pipeline"]], ["n1"])
        self.assertTrue(any("chain failed" in e["event"] for e in coord.events))

    def test_unprovable_shard_map_charges_and_pays_nobody(self):
        coord, _ = mk_coord(FakeEngines(unprovable=True))
        coord.ledger.earn("maha", 100, "operator")
        coord.submit("maha", "q", 50)
        self.assertAlmostEqual(coord.ledger.balance("maha"), 100.0, places=2)  # refunded
        self.assertEqual(coord.ledger.balance("amina"), 0.0)                    # nothing minted
        self.assertTrue(any("unprovable" in e["event"] for e in coord.events))


class TestConversation(unittest.TestCase):
    def test_whole_conversation_reaches_the_engine(self):
        coord, engines = mk_coord()
        convo = [
            {"role": "user", "content": "my name is Abdullah"},
            {"role": "assistant", "content": "hello Abdullah"},
            {"role": "user", "content": "what is my name?"},
        ]
        coord.submit("anon", "what is my name?", 25, messages=convo)
        sent = engines.message_lists[-1]
        self.assertEqual(len(sent), 3)                      # history, not just the last turn
        self.assertEqual(sent[0]["content"], "my name is Abdullah")
        self.assertEqual(sent[-1]["role"], "user")

    def test_single_prompt_becomes_one_user_message(self):
        coord, engines = mk_coord()
        coord.submit("anon", "hello", 25)
        self.assertEqual(engines.message_lists[-1], [{"role": "user", "content": "hello"}])


class TestLedgerDurability(unittest.TestCase):
    def test_credits_survive_a_restart(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.jsonl"
            first = Ledger(path=path)
            first.earn("amina", 12.5, "served layers")
            first.spend("amina", 4.0, "inference")
            self.assertAlmostEqual(first.balance("amina"), 8.5, places=6)
            first.close()

            revived = Ledger(path=path)                      # coordinator restarts
            self.assertAlmostEqual(revived.balance("amina"), 8.5, places=6)
            self.assertEqual(len(revived.entries()), 2)
            self.assertTrue(revived.audit()["consistent"])
            revived.close()

    def test_corrupt_trailing_line_is_skipped_not_fatal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.jsonl"
            led = Ledger(path=path)
            led.earn("amina", 5.0, "served")
            led.close()
            with open(path, "a", encoding="utf-8") as fh:
                fh.write('{"ts": 1, "account": "amina", "delta": ')   # crash mid-write
            revived = Ledger(path=path)
            self.assertAlmostEqual(revived.balance("amina"), 5.0, places=6)
            revived.close()

    def test_audit_detects_tampering(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            led = Ledger(path=Path(d) / "l.jsonl")            # auditing needs a file to read
            led.earn("amina", 10.0, "served")
            self.assertTrue(led.audit()["consistent"])
            led._balances["amina"] = 999.0                    # simulate a corrupted cache
            audit = led.audit()
            self.assertFalse(audit["consistent"])
            self.assertIn("amina", audit["mismatches"])
            led.close()


class TestConcurrency(unittest.TestCase):
    def test_jobs_run_in_parallel_up_to_the_slot_count(self):
        gate = threading.Event()
        coord, engines = mk_coord(FakeEngines(gate=gate), concurrency=3)
        threads = [threading.Thread(target=coord.submit, args=("u%d" % i, "q%d" % i, 5))
                   for i in range(3)]
        for t in threads:
            t.start()
        deadline = time.time() + 5
        while len(engines.calls) < 3 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(engines.calls), 3, "all three should be in flight at once")
        gate.set()
        for t in threads:
            t.join(timeout=10)

    def test_concurrency_is_bounded(self):
        gate = threading.Event()
        coord, engines = mk_coord(FakeEngines(gate=gate), concurrency=2)
        threads = [threading.Thread(target=coord.submit, args=("u%d" % i, "q%d" % i, 5))
                   for i in range(4)]
        for t in threads:
            t.start()
        time.sleep(0.4)
        self.assertEqual(len(engines.calls), 2, "the 3rd and 4th must wait for a slot")
        gate.set()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(len(engines.calls), 4)


class TestDiscovery(unittest.TestCase):
    def test_responder_answers_a_broadcast(self):
        from sanad_net.discovery import DiscoveryResponder, discover
        responder = DiscoveryResponder(http_port=7899, name="test-net")
        responder.start()
        if responder._sock is None:
            self.skipTest("discovery port unavailable in this environment")
        try:
            found = discover(timeout_s=2.0)
            self.assertTrue(found, "no coordinator answered the broadcast")
            # Other coordinators may share this LAN; find ours among the replies.
            ours = [f for f in found if f["name"] == "test-net"]
            self.assertTrue(ours, f"our responder did not answer; got {found}")
            self.assertTrue(ours[0]["coordinator"].startswith("http://"))
            self.assertTrue(ours[0]["coordinator"].endswith(":7899"))
        finally:
            responder.stop()


class TestRegressionsFromReview(unittest.TestCase):
    """Each of these encodes a bug an adversarial review found in v0.4."""

    def test_stream_errors_become_EngineError_so_repair_can_act(self):
        # ConnectionResetError is NOT a URLError; catching only URLError let a
        # dead pipeline bypass chain repair entirely.
        from sanad_net.engine import STREAM_ERRORS
        for exc in (ConnectionResetError, http.client.IncompleteRead,
                    urllib.error.URLError, OSError):
            self.assertTrue(issubclass(exc, STREAM_ERRORS),
                            f"{exc.__name__} must be treated as a stream failure")

    def test_engine_drains_inflight_before_a_rebuild_stops_it(self):
        from sanad_net.engine import Engine
        eng = Engine.__new__(Engine)                 # no subprocess needed
        eng._inflight = 0
        eng._idle = threading.Condition()
        eng._retired = False
        eng.proc = None
        stopped_at: list[float] = []
        eng.stop = lambda: stopped_at.append(time.time())

        with eng._idle:                              # simulate one in-flight answer
            eng._inflight = 1
        released_at: list[float] = []

        def finish_later():
            time.sleep(0.3)
            with eng._idle:
                eng._inflight -= 1
                released_at.append(time.time())
                eng._idle.notify_all()

        threading.Thread(target=finish_later, daemon=True).start()
        self.assertTrue(eng.retire(timeout_s=5))
        self.assertTrue(stopped_at and released_at)
        self.assertGreaterEqual(stopped_at[0], released_at[0],
                                "the engine stopped before the in-flight answer finished")

    def test_healthy_nodes_are_not_evicted_by_one_failed_request(self):
        coord, engines = mk_coord(FakeEngines(fail_times=1), concurrency=1)
        coord.registry.heartbeat("n1")
        coord.registry.heartbeat("n2")               # both freshly alive
        coord.submit("anon", "hello", 25)
        self.assertEqual({n["node_id"] for n in coord.registry.alive()}, {"n1", "n2"},
                         "a heartbeating node must survive another request's failure")

    def test_ladder_budget_accounts_for_concurrency(self):
        one = ModelTier(name="m", path=Path("m.gguf"), file_mb=1000.0, slots=1)
        many = ModelTier(name="m", path=Path("m.gguf"), file_mb=1000.0, slots=8)
        self.assertGreater(many.need_mb, one.need_mb,
                           "more concurrent slots need more KV cache, so more pooled memory")

    def test_system_message_survives_truncation(self):
        from sanad_net.coordinator import MAX_MESSAGES
        coord, _ = mk_coord(nodes=False)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(coord))
        handler = make_handler(coord)
        parse = handler._parse_ask
        long_convo = [{"role": "system", "content": "always answer in French"}]
        for i in range(MAX_MESSAGES + 10):
            long_convo.append({"role": "user" if i % 2 == 0 else "assistant",
                               "content": f"m{i}"})
        long_convo.append({"role": "user", "content": "final question"})
        _user, _prompt, _n, msgs = parse(handler, {"messages": long_convo})
        server.server_close()
        self.assertEqual(msgs[0]["role"], "system", "the system message must not be trimmed away")
        self.assertEqual(msgs[0]["content"], "always answer in French")
        self.assertEqual(msgs[-1]["content"], "final question")
        self.assertLessEqual(len(msgs), MAX_MESSAGES)

    def test_audit_reports_in_memory_ledgers_as_not_durable(self):
        audit = Ledger().audit()
        self.assertFalse(audit["durable"])
        self.assertIsNone(audit["consistent"])       # never claims verification it didn't do

    def test_audit_reads_the_file_and_catches_divergence(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            led = Ledger(path=Path(d) / "l.jsonl")
            led.earn("amina", 10.0, "served")
            self.assertTrue(led.audit()["consistent"])
            led._balances["amina"] = 999.0           # memory now disagrees with disk
            audit = led.audit()
            self.assertFalse(audit["consistent"])
            self.assertEqual(audit["mismatches"]["amina"]["on_disk"], 10.0)
            led.close()

    def test_closed_durable_ledger_refuses_to_mint(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            led = Ledger(path=Path(d) / "l.jsonl")
            led.close()
            with self.assertRaises(RuntimeError):
                led.earn("amina", 5.0, "served")     # silently losing it would be worse

    def test_reserve_is_atomic_for_concurrent_requests(self):
        led = Ledger()
        led.earn("amina", 100.0, "operated")
        seen: list[float] = []

        def take():
            before, _burned = led.reserve("amina", 60.0, "escrow")
            seen.append(before)

        threads = [threading.Thread(target=take) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(sorted(seen), [40.0, 100.0],
                         "the second reserve must see the first one's debit")
        self.assertAlmostEqual(led.balance("amina"), 0.0, places=6)

    def test_discovery_ignores_non_private_sources(self):
        from sanad_net.discovery import _is_private
        self.assertTrue(_is_private("192.168.1.5"))
        self.assertTrue(_is_private("10.0.0.3"))
        self.assertTrue(_is_private("127.0.0.1"))
        self.assertFalse(_is_private("8.8.8.8"))
        self.assertFalse(_is_private("not-an-ip"))

    def test_discovery_is_off_for_a_loopback_coordinator(self):
        from sanad_net.discovery import DiscoveryResponder
        r = DiscoveryResponder(http_port=7899, bind="127.0.0.1")
        self.assertFalse(r.start(), "a loopback-only coordinator has nothing to discover")
        self.assertFalse(r.active)


class TestSetupAndRun(unittest.TestCase):
    """The install path is a feature: if it misleads a newcomer, nothing else matters."""

    def test_asset_pattern_covers_every_supported_platform(self):
        from sanad_net import setup
        import platform as plat
        cases = [("Windows", "AMD64", "bin-win-cpu-x64"), ("Windows", "ARM64", "bin-win-cpu-arm64"),
                 ("Darwin", "arm64", "bin-macos-arm64"), ("Darwin", "x86_64", "bin-macos-x64"),
                 ("Linux", "x86_64", "bin-ubuntu-x64"), ("Linux", "aarch64", "bin-ubuntu-arm64")]
        orig_system, orig_machine = plat.system, plat.machine
        try:
            for system, machine, expected in cases:
                plat.system = lambda s=system: s
                plat.machine = lambda m=machine: m
                pattern, label = setup.asset_pattern()
                self.assertEqual(pattern, expected, f"{system}/{machine}")
                self.assertTrue(label, "every platform needs a human-readable label")
        finally:
            plat.system, plat.machine = orig_system, orig_machine

    def test_unsupported_platform_says_what_to_do(self):
        from sanad_net import setup
        import platform as plat
        orig = plat.system
        try:
            plat.system = lambda: "Plan9"
            with self.assertRaises(SystemExit) as ctx:
                setup.asset_pattern()
            self.assertIn("--llama-bin", str(ctx.exception))   # tells them the way out
        finally:
            plat.system = orig

    def test_min_build_matches_the_node_guard(self):
        # Two copies of a security constant is one too many to let drift.
        from sanad_net import node, setup
        self.assertEqual(setup.MIN_BUILD, node.MIN_LLAMA_BUILD)

    def test_preflight_names_what_is_missing(self):
        from sanad_net import run as run_mod
        orig_build, orig_dir = run_mod.installed_build, run_mod.MODELS_DIR
        try:
            run_mod.installed_build = lambda: None
            run_mod.MODELS_DIR = Path("/nonexistent-for-tests")
            problems = run_mod.preflight()
            self.assertTrue(any("llama.cpp" in p for p in problems))
            self.assertTrue(any("model" in p for p in problems))

            run_mod.installed_build = lambda: 1  # ancient build
            self.assertTrue(any("too old" in p for p in run_mod.preflight()))
        finally:
            run_mod.installed_build, run_mod.MODELS_DIR = orig_build, orig_dir

    def test_human_readable_sizes(self):
        from sanad_net.setup import human
        self.assertEqual(human(512), "512 B")
        self.assertEqual(human(2 * 1024), "2 KB")
        self.assertEqual(human(5 * 1024 * 1024), "5 MB")
        self.assertEqual(human(3 * 1024 ** 3), "3.0 GB")


class TestHTTPSurface(unittest.TestCase):
    def _serve(self, coord):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(coord))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, f"http://127.0.0.1:{server.server_address[1]}"

    @staticmethod
    def _call(base, path, payload=None):
        if payload is None:
            with urllib.request.urlopen(f"{base}{path}", timeout=10) as r:
                return json.loads(r.read())
        req = urllib.request.Request(
            f"{base}{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def test_full_round_trip(self):
        coord, _ = mk_coord(nodes=False)
        server, base = self._serve(coord)
        try:
            self._call(base, "/register", {"node_id": "n1", "host": "127.0.0.1", "port": 50060,
                                           "operator": "amina", "pledge_mb": 1000})
            self._call(base, "/register", {"node_id": "n2", "host": "127.0.0.1", "port": 50061,
                                           "operator": "bilal", "pledge_mb": 600})
            self.assertEqual(self._call(base, "/status")["model"], "large")
            r = self._call(base, "/ask", {"user": "anon", "prompt": "hi", "max_tokens": 25})
            self.assertEqual(r["decode_tokens"], 25)
            entries = self._call(base, "/ledger")["entries"]
            self.assertAlmostEqual(sum(e["delta"] for e in entries if e["delta"] > 0), 25.0, places=2)
            self.assertTrue(self._call(base, "/leave", {"node_id": "n2"})["ok"])
            self.assertEqual(self._call(base, "/status")["model"], "small")
        finally:
            server.shutdown()
            server.server_close()

    def test_chat_page_is_served(self):
        coord, _ = mk_coord()
        server, base = self._serve(coord)
        try:
            with urllib.request.urlopen(f"{base}/", timeout=10) as r:
                page = r.read().decode()
                self.assertEqual(r.status, 200)
                self.assertIn("text/html", r.headers.get("Content-Type", ""))
            for needle in ("Sanad", "/ask/stream", "<textarea"):
                self.assertIn(needle, page)
        finally:
            server.shutdown()
            server.server_close()

    def test_sse_stream_endpoint(self):
        coord, _ = mk_coord()
        server, base = self._serve(coord)
        try:
            req = urllib.request.Request(
                f"{base}/ask/stream",
                data=json.dumps({"user": "anon", "prompt": "hi", "max_tokens": 25}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            events = []
            with urllib.request.urlopen(req, timeout=20) as resp:
                self.assertIn("text/event-stream", resp.headers.get("Content-Type", ""))
                buf = ""
                for raw in resp:
                    buf += raw.decode()
                    while "\n\n" in buf:
                        chunk, buf = buf.split("\n\n", 1)
                        if chunk.strip().startswith("data:"):
                            events.append(json.loads(chunk.strip()[5:]))
            self.assertEqual([e["type"] for e in events], ["token", "token", "token", "done"])
            self.assertEqual("".join(e["text"] for e in events if e["type"] == "token"), "hello world")
        finally:
            server.shutdown()
            server.server_close()

    def test_input_validation(self):
        coord, _ = mk_coord(nodes=False)
        server, base = self._serve(coord)

        def post_raw(path, body):
            req = urllib.request.Request(
                f"{base}{path}", data=body.encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code

        try:
            # Python's json parses Infinity/NaN — both must be rejected
            for bad in ("Infinity", "NaN"):
                code = post_raw("/register", '{"node_id":"evil","host":"127.0.0.1","port":1,'
                                             f'"operator":"x","pledge_mb":{bad}}}')
                self.assertEqual(code, 500)
            self.assertEqual(coord.registry.alive(), [])
            # negative max_tokens is clamped, not honored
            coord.registry.register("n1", "127.0.0.1", 50060, "amina", 1000)
            r = self._call(base, "/ask", {"user": "anon", "prompt": "x", "max_tokens": -99})
            self.assertEqual(r["decode_tokens"], 25)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
