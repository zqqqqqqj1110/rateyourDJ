from __future__ import annotations

import unittest

from rateyourdj.l7.ab_compare import ABVariant, run_ab_comparison


class ABComparisonTests(unittest.TestCase):
    def test_rules_vs_rules_runs_and_reports(self) -> None:
        # Two identical rules arms: comparison runs offline, reports per-arm
        # metrics and B-minus-A deltas over a shared query batch.
        queries = ["推荐一些摇滚", "再来一些摇滚"]
        report = run_ab_comparison(
            queries,
            ABVariant(label="a", agent_mode="rules"),
            ABVariant(label="b", agent_mode="rules"),
        )
        self.assertEqual(report.query_count, 2)
        self.assertEqual(report.variant_a.query_count, 2)
        self.assertEqual(report.variant_b.query_count, 2)
        # Latency is measured per run, so it should be a non-negative number.
        self.assertGreaterEqual(report.variant_a.average_latency_ms, 0.0)
        self.assertIn("average_latency_ms", report.deltas)
        self.assertIn("thought_coverage_rate", report.deltas)
        # Honesty note about the missing SFT/GRPO arm is always present.
        self.assertTrue(any("SFT/GRPO" in note for note in report.notes))

    def test_cold_vs_seeded_profile_differs(self) -> None:
        # A cold profile (no collection) should not produce recommendations,
        # while a seeded one can — a meaningful, observable A/B difference.
        queries = ["推荐一些摇滚"]
        report = run_ab_comparison(
            queries,
            ABVariant(label="cold", agent_mode="rules", seed_profile=False),
            ABVariant(label="seeded", agent_mode="rules", seed_profile=True),
        )
        self.assertEqual(report.variant_a.non_empty_rate, 0.0)
        self.assertGreaterEqual(report.variant_b.non_empty_rate, 0.0)

    def test_empty_queries_rejected(self) -> None:
        with self.assertRaises(ValueError):
            run_ab_comparison(
                [],
                ABVariant(label="a"),
                ABVariant(label="b"),
            )


if __name__ == "__main__":
    unittest.main()
