"""Unit tests for sanad_net v0.2 (no llama.cpp binaries required)."""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sanad_net.coordinator import (  # noqa: E402
    Coordinator, ModelTier, Registry, parse_llama_log, pick_tier,
)
from sanad_net.ledger import Ledger  # noqa: E402

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
    def test_register_heartbeat_leave(self):
        reg = Registry()
        reg.register("n1", "127.0.0.1", 50060, "amina", 1000)
        self.assertTrue(reg.heartbeat("n1"))
        self.assertFalse(reg.heartbeat("nope"))
        self.assertEqual([n["node_id"] for n in reg.alive()], ["n1"])
        self.assertTrue(reg.leave("n1"))       # graceful exit
        self.assertFalse(reg.leave("n1"))      # idempotent
        self.assertEqual(reg.alive(), [])

    def test_ttl_expiry(self):
        reg = Registry()
        reg.register("n1", "127.0.0.1", 50060, "amina", 1000)
        reg._nodes["n1"]["last_seen"] = time.time() - 999  # simulate silence
        self.assertEqual(reg.alive(), [])


class TestCapacityLadder(unittest.TestCase):
    def test_pick_tier(self):
        self.assertIsNone(pick_tier(CATALOG, 100))                 # nothing fits
        self.assertEqual(pick_tier(CATALOG, 700).name, "small")    # only small fits
        self.assertEqual(pick_tier(CATALOG, 1600).name, "large")   # largest that fits
        self.assertEqual(pick_tier(CATALOG, 99999).name, "large")

    def test_ladder_events_on_join_and_leave(self):
        coord = Coordinator(FakeRunner(), CATALOG)
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


class FakeRunner:
    """Deterministic stand-in for llama-completion.

    Reports a shard map where the layer split follows the tensor_split ratio
    over 25 layers — mirroring the empirically verified --tensor-split behavior.
    """

    def __init__(self, delay_s: float = 0.15, fail_times: int = 0):
        self.delay_s = delay_s
        self.fail_times = fail_times
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def run(self, model_path, prompt, rpc_servers, tensor_split, max_tokens) -> dict:
        with self._lock:
            self.calls.append(prompt)
            if self.fail_times > 0:
                self.fail_times -= 1
                raise RuntimeError("simulated chain failure (node vanished mid-job)")
        time.sleep(self.delay_s)
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


class TestCoordinatorV02(unittest.TestCase):
    def _mk(self, runner=None) -> tuple[Coordinator, FakeRunner]:
        runner = runner or FakeRunner()
        coord = Coordinator(runner, CATALOG)
        coord.registry.register("n1", "127.0.0.1", 50060, "amina", 1000)
        coord.registry.register("n2", "127.0.0.1", 50061, "bilal", 600)
        return coord, runner

    def test_weighted_credits_follow_pledge(self):
        coord, _ = self._mk()
        result = coord.submit("anon", "hello", 25)
        # pledges 1000:600 over 25 layers -> 16:9 layers -> credits 16:9 of 25 tokens
        self.assertEqual(result["model"], "large")
        self.assertAlmostEqual(coord.ledger.balance("amina"), 16.0, places=2)
        self.assertAlmostEqual(coord.ledger.balance("bilal"), 9.0, places=2)

    def test_chain_repair_retries_once(self):
        coord, runner = self._mk(FakeRunner(fail_times=1))
        result = coord.submit("anon", "hello", 25)
        self.assertTrue(result["repaired"])
        self.assertEqual(len(runner.calls), 2)  # failed once, retried once
        self.assertTrue(any("chain failed" in e["event"] for e in coord.events))

    def test_priority_orders_queue(self):
        coord, runner = self._mk()
        coord.ledger.earn("vip", 100, "operated a node")
        done = []
        lock = threading.Lock()

        def ask(user, prompt):
            coord.submit(user, prompt, 10)
            with lock:
                done.append(user)

        t1 = threading.Thread(target=ask, args=("anon", "filler"))
        t1.start()
        time.sleep(0.05)  # filler now running
        t2 = threading.Thread(target=ask, args=("zed", "zed-q"))
        t2.start()
        time.sleep(0.03)  # zed queued first...
        t3 = threading.Thread(target=ask, args=("vip", "vip-q"))
        t3.start()  # ...vip queued second, with credits
        for t in (t1, t2, t3):
            t.join(timeout=10)
        self.assertEqual(done[0], "anon")
        self.assertLess(done.index("vip"), done.index("zed"))
        self.assertEqual(runner.calls[0], "filler")
        self.assertEqual(runner.calls[1], "vip-q")

    def test_no_nodes_errors(self):
        coord = Coordinator(FakeRunner(), CATALOG)
        with self.assertRaises(RuntimeError):
            coord.submit("anon", "hi", 5)

    def test_pool_too_small_errors(self):
        coord = Coordinator(FakeRunner(), CATALOG)
        coord.registry.register("tiny", "127.0.0.1", 50060, "amina", 100)
        with self.assertRaises(RuntimeError):
            coord.submit("anon", "hi", 5)


if __name__ == "__main__":
    unittest.main()
