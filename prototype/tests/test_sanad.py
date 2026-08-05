"""Tests for the Sanad v0 simulation.

Run from the prototype/ directory:

    python -m unittest discover -s tests -v
"""

import asyncio
import unittest

from sanad import Coordinator, CreditLedger, Job, MockWorker, ModelSpec, WorkerInfo

GiB = 1024**3


class SanadTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.model = ModelSpec("test-8l", n_layers=8, bytes_per_layer=GiB)
        self.ledger = CreditLedger()
        self.coord = Coordinator(self.model, self.ledger)

    def add_worker(
        self, wid: str, vram_gib: int, layer_range: tuple[int, int], latency_ms: float = 10.0
    ) -> MockWorker:
        worker = MockWorker(
            WorkerInfo(wid, vram_gib * GiB, layer_range, latency_ms), self.ledger
        )
        self.coord.register(worker)
        return worker


class TestPipelineAssembly(SanadTestCase):
    def test_pipeline_covers_all_layers_exactly(self) -> None:
        self.add_worker("a", 4, (0, 4))
        self.add_worker("b", 4, (4, 8))
        stages = self.coord.build_pipeline()
        self.assertEqual(stages[0].layers[0], 0)
        self.assertEqual(stages[-1].layers[1], self.model.n_layers)
        for prev, nxt in zip(stages, stages[1:]):
            self.assertEqual(prev.layers[1], nxt.layers[0])  # contiguous, no gaps
        self.assertEqual(
            sum(b - a for (a, b) in (s.layers for s in stages)), self.model.n_layers
        )

    def test_picks_lowest_latency_cover(self) -> None:
        self.add_worker("a", 4, (0, 4), latency_ms=10.0)
        self.add_worker("b", 4, (4, 8), latency_ms=10.0)
        self.add_worker("slow-full", 8, (0, 8), latency_ms=50.0)
        stages = self.coord.build_pipeline()
        self.assertEqual([s.worker.info.id for s in stages], ["a", "b"])  # 20 < 50 ms

    def test_over_claiming_worker_rejected(self) -> None:
        liar = MockWorker(WorkerInfo("liar", 2 * GiB, (0, 4), 5.0), self.ledger)
        with self.assertRaises(ValueError):
            self.coord.register(liar)  # claims 4 layers = 4 GiB with 2 GiB
        self.assertNotIn("liar", self.coord.workers)

    def test_uncoverable_model_raises(self) -> None:
        self.add_worker("a", 4, (0, 4))  # layers 4-8 uncovered
        with self.assertRaises(RuntimeError):
            self.coord.build_pipeline()


class TestCredits(SanadTestCase):
    def test_credits_accrue_proportionally_to_layers_served(self) -> None:
        self.add_worker("small", 2, (0, 2))   # 2/8 of the model
        self.add_worker("large", 6, (2, 8))   # 6/8 of the model
        self.coord.submit(Job("j1", "client", "hi", max_tokens=4))
        asyncio.run(self.coord.run_all())
        self.assertAlmostEqual(self.ledger.balance("small"), 4 * 2 / 8)
        self.assertAlmostEqual(self.ledger.balance("large"), 4 * 6 / 8)

    def test_credits_are_not_transferable(self) -> None:
        # Non-transferability is by design: no such method may exist.
        self.assertFalse(hasattr(self.ledger, "transfer"))

    def test_spend_clamps_at_zero(self) -> None:
        self.assertEqual(self.ledger.spend("nobody", 5.0), 0.0)
        self.ledger.earn("worker", tokens=3, weight=1.0)
        self.assertEqual(self.ledger.spend("worker", 5.0), 3.0)
        self.assertEqual(self.ledger.balance("worker"), 0.0)


class TestScheduling(SanadTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.add_worker("a", 4, (0, 4))
        self.add_worker("b", 4, (4, 8))

    def test_priority_ordering_respects_balances(self) -> None:
        self.ledger.earn("rich", tokens=100, weight=1.0)
        self.ledger.earn("mid", tokens=10, weight=1.0)
        for job in (
            Job("j-anon", "anon", "p", 2),
            Job("j-mid", "mid", "p", 2),
            Job("j-rich", "rich", "p", 2),
        ):
            self.coord.submit(job)
        self.assertEqual(
            [j.id for j in self.coord.queue_order()], ["j-rich", "j-mid", "j-anon"]
        )

    def test_anonymous_eventually_served_no_starvation(self) -> None:
        self.ledger.earn("rich", tokens=100, weight=1.0)
        anon_job = Job("j-anon", "anon", "p", max_tokens=3)
        rich_job = Job("j-rich", "rich", "p", max_tokens=3)
        self.coord.submit(anon_job)  # anonymous arrives FIRST
        self.coord.submit(rich_job)
        done = asyncio.run(self.coord.run_all())
        self.assertEqual([j.id for j in done], ["j-rich", "j-anon"])  # priority...
        self.assertEqual(len(anon_job.tokens), 3)  # ...but anon fully served
        self.assertEqual(self.ledger.balance("anon"), 0.0)  # and paid nothing


if __name__ == "__main__":
    unittest.main()
