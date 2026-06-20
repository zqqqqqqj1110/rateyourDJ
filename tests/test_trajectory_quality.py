from __future__ import annotations

import unittest

from rateyourdj.l7.trajectory_quality import (
    check_quality_gate,
    compute_trajectory_quality,
)


def _discovery_call(generated: int, grounded: int, dropped: int) -> dict:
    return {
        "tool": "discover_tracks",
        "observation": {
            "status": "ok",
            "data": {
                "generated": generated,
                "grounded": grounded,
                "dropped": dropped,
                "hallucination_rate": round(dropped / generated, 6) if generated else 0.0,
            },
        },
    }


def _trajectory(
    *,
    discovery: tuple[int, int, int] | None = None,
    decisions: list[dict] | None = None,
) -> dict:
    record: dict = {"tool_calls": [], "agent_decisions": decisions or []}
    if discovery is not None:
        record["tool_calls"].append(_discovery_call(*discovery))
    return record


class GroundingQualityTests(unittest.TestCase):
    def test_aggregates_generated_grounded_dropped(self) -> None:
        records = [
            _trajectory(discovery=(10, 8, 2)),
            _trajectory(discovery=(6, 6, 0)),
        ]
        report = compute_trajectory_quality(records)
        self.assertEqual(report.total_generated, 16)
        self.assertEqual(report.total_grounded, 14)
        self.assertEqual(report.total_dropped, 2)
        self.assertEqual(report.discovery_trajectory_count, 2)
        self.assertEqual(report.discovery_coverage_rate, 1.0)
        self.assertAlmostEqual(report.grounding_rate, round(14 / 16, 6))
        # mean of [0.2, 0.0]
        self.assertAlmostEqual(report.average_hallucination_rate, 0.1)

    def test_no_discovery_yields_zero_rates(self) -> None:
        report = compute_trajectory_quality([_trajectory()])
        self.assertEqual(report.total_generated, 0)
        self.assertEqual(report.grounding_rate, 0.0)
        self.assertEqual(report.discovery_coverage_rate, 0.0)


class ReactTraceQualityTests(unittest.TestCase):
    def test_thought_coverage(self) -> None:
        decisions = [
            {"kind": "tool", "tool_name": "search_tracks", "thought": "reflect and search"},
            {"kind": "finish", "thought": "enough results"},
            # program-injected step (not a model decision) -> excluded
            {"kind": "program_discovery_first", "summary": "x"},
        ]
        report = compute_trajectory_quality([_trajectory(decisions=decisions)])
        self.assertEqual(report.model_decision_count, 2)
        self.assertEqual(report.decisions_with_thought, 2)
        self.assertEqual(report.thought_coverage_rate, 1.0)
        self.assertEqual(report.model_decisions_missing_thought, 0)
        self.assertEqual(report.average_thoughts_per_trajectory, 2.0)
        self.assertGreater(report.average_thought_length, 0)

    def test_missing_thought_counted(self) -> None:
        decisions = [
            {"kind": "tool", "tool_name": "search_tracks", "thought": ""},
            {"kind": "finish", "thought": "done"},
        ]
        report = compute_trajectory_quality([_trajectory(decisions=decisions)])
        self.assertEqual(report.model_decision_count, 2)
        self.assertEqual(report.decisions_with_thought, 1)
        self.assertEqual(report.model_decisions_missing_thought, 1)
        self.assertEqual(report.thought_coverage_rate, 0.5)


class QualityGateTests(unittest.TestCase):
    def test_gate_passes_good_metrics(self) -> None:
        records = [
            _trajectory(
                discovery=(10, 9, 1),
                decisions=[{"kind": "finish", "thought": "all good"}],
            )
        ]
        report = compute_trajectory_quality(records)
        gate = check_quality_gate(report)
        self.assertTrue(gate.passed, gate.failures)

    def test_gate_fails_high_hallucination(self) -> None:
        records = [_trajectory(discovery=(10, 2, 8))]  # 0.8 hallucination
        report = compute_trajectory_quality(records)
        gate = check_quality_gate(report, max_hallucination_rate=0.5)
        self.assertFalse(gate.passed)
        self.assertTrue(any("hallucination" in f for f in gate.failures))

    def test_gate_fails_missing_thought(self) -> None:
        decisions = [{"kind": "tool", "tool_name": "x", "thought": ""}]
        report = compute_trajectory_quality([_trajectory(decisions=decisions)])
        gate = check_quality_gate(report, max_missing_thought=0)
        self.assertFalse(gate.passed)
        self.assertTrue(any("thought" in f for f in gate.failures))

    def test_empty_dataset_passes_vacuously(self) -> None:
        report = compute_trajectory_quality([])
        gate = check_quality_gate(report)
        self.assertTrue(gate.passed)
        self.assertEqual(gate.checked, {})


if __name__ == "__main__":
    unittest.main()
