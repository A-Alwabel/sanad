"""Unit tests for sanad_net (no llama.cpp binaries required)."""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sanad_net.coordinator import Coordinator, Registry, parse_llama_log  # noqa: E402
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
    def test_register_heartbeat_alive(self):
        reg = Registry()
        reg.register("n1", "127.0.0.1", 50060, "amina")
        self.assertTrue(reg.heartbeat("n1"))
        self.assertFalse(reg.heartbeat("nope"))
        self.assertEqual([n["node_id"] for n in reg.alive()], ["n1"])
        reg._nodes["n1"]["last_seen"] = time.time() - 999  # simulate silence
        self.assertEqual(reg.alive(), [])


class FakeRunner:
    """Deterministic stand-in for llama-completion."""

    def __init__(self, delay_s: float = 0.15):
        self.delay_s = delay_s
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def run(self, prompt: str, rpc_servers: list[str], max_tokens: int) -> dict:
        with self._lock:
            self.calls.append(prompt)
        time.sleep(self.delay_s)
        return {
            "text": f"echo:{prompt}", "wall_s": self.delay_s,
            "shard_map": {"RPC0": {"endpoint": rpc_servers[0], "layers": "0-11", "n_layers": 12}},
            "decode_tokens": 10, "tok_per_s": 60.0,
        }


class TestCoordinatorScheduling(unittest.TestCase):
    def _mk(self) -> tuple[Coordinator, FakeRunner]:
        runner = FakeRunner()
        coord = Coordinator(runner)  # type: ignore[arg-type]
        coord.registry.register("n1", "127.0.0.1", 50060, "amina")
        coord.registry.register("n2", "127.0.0.1", 50061, "bilal")
        return coord, runner

    def test_credits_minted_and_split(self):
        coord, _ = self._mk()
        result = coord.submit("anon", "hello", 10)
        self.assertEqual(result["decode_tokens"], 10)
        self.assertEqual(coord.ledger.balance("amina"), 5.0)  # 10 tokens / 2 nodes
        self.assertEqual(coord.ledger.balance("bilal"), 5.0)
        self.assertEqual(coord.ledger.balance("anon"), 0.0)  # clamped, still served

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
        coord = Coordinator(FakeRunner())  # type: ignore[arg-type]
        with self.assertRaises(RuntimeError):
            coord.submit("anon", "hi", 5)


if __name__ == "__main__":
    unittest.main()
