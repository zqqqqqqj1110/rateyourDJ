import json
import os
import unittest
from unittest.mock import patch

from rateyourdj.l6 import (
    AgentTurn,
    DeepSeekProvider,
    LLMProviderError,
    configured_llm_provider,
)


def tool_response(name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ]
                }
            }
        ]
    }


class DeepSeekProviderTests(unittest.TestCase):
    def make_turn(self) -> AgentTurn:
        return AgentTurn(
            user_id="private-user-id",
            query="不要 Artist B，推荐一首摇滚",
            request={
                "query": "不要 Artist B，推荐一首摇滚",
                "top_k": 1,
                "max_per_artist": 2,
                "min_retrieval_score": 0.0,
                "preference_terms": ["rock"],
                "exclude_terms": ["artist b"],
                "reference_artists": [],
                "avoid_artists": [],
                "refinement_notes": [],
                "intent": "recommend",
                "exclude_seen": False,
            },
            session={
                "session_id": "session-id",
                "turn_count": 0,
                "preference_terms": [],
                "exclude_terms": [],
                "seen_song_ids": [],
            },
            tool_schemas=[
                {
                    "type": "function",
                    "name": "L4.rank_candidates",
                    "description": "Rank songs.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user_id": {"type": "string"},
                            "top_k": {"type": "integer"},
                        },
                        "required": ["user_id"],
                        "additionalProperties": False,
                    },
                }
            ],
            tool_history=[
                {
                    "arguments": {"user_id": "private-user-id"},
                    "observation": {
                        "data": {"user_id": "private-user-id", "version": 1}
                    },
                }
            ],
            validation_feedback=[],
            remaining_steps=5,
        )

    def test_maps_internal_tool_names_and_hides_user_id(self) -> None:
        payloads: list[dict[str, object]] = []

        def request_json(payload: dict[str, object]) -> dict[str, object]:
            payloads.append(payload)
            return tool_response(
                "L4__rank_candidates",
                {"top_k": 5},
            )

        provider = DeepSeekProvider(
            "test-key",
            request_json=request_json,
        )

        decision = provider.next_decision(self.make_turn())

        self.assertEqual(decision.kind, "tool")
        self.assertEqual(decision.tool_name, "L4.rank_candidates")
        self.assertEqual(decision.arguments, {"top_k": 5})
        payload = payloads[0]
        user_content = json.loads(payload["messages"][1]["content"])
        self.assertEqual(user_content["user_scope"], "current_user")
        self.assertNotIn("user_id", user_content)
        self.assertNotIn(
            "private-user-id",
            json.dumps(user_content["tool_history"]),
        )
        rank_tool = payload["tools"][0]["function"]
        self.assertEqual(rank_tool["name"], "L4__rank_candidates")
        self.assertNotIn(
            "user_id",
            rank_tool["parameters"]["properties"],
        )
        self.assertNotIn(
            "user_id",
            rank_tool["parameters"]["required"],
        )
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_parses_update_and_finish_control_tools(self) -> None:
        responses = [
            tool_response(
                "agent_update_request",
                {
                    "summary": "capture exclusion",
                    "request_patch": {"exclude_terms": ["artist b"]},
                },
            ),
            tool_response(
                "agent_finish",
                {
                    "summary": "constraints satisfied",
                    "response_text": "为你找到一首歌。",
                },
            ),
        ]
        provider = DeepSeekProvider(
            "test-key",
            request_json=lambda _payload: responses.pop(0),
        )

        update = provider.next_decision(self.make_turn())
        finish = provider.next_decision(self.make_turn())

        self.assertEqual(update.kind, "update")
        self.assertEqual(update.request_patch, {"exclude_terms": ["artist b"]})
        self.assertEqual(finish.kind, "finish")
        self.assertEqual(finish.response_text, "为你找到一首歌。")

    def test_supplies_summary_for_valid_update_patch(self) -> None:
        provider = DeepSeekProvider(
            "test-key",
            request_json=lambda _payload: tool_response(
                "agent_update_request",
                {
                    "request_patch": {
                        "exclude_terms": ["pink floyd"],
                    }
                },
            ),
        )

        decision = provider.next_decision(self.make_turn())

        self.assertEqual(decision.kind, "update")
        self.assertEqual(decision.summary, "update structured request")
        self.assertEqual(
            decision.request_patch,
            {"exclude_terms": ["pink floyd"]},
        )

    def test_parses_refinement_fields_from_update_patch(self) -> None:
        provider = DeepSeekProvider(
            "test-key",
            request_json=lambda _payload: tool_response(
                "agent_update_request",
                {
                    "summary": "refine toward oasis and away from punk",
                    "request_patch": {
                        "intent": "more",
                        "exclude_seen": True,
                        "reference_artists": ["Oasis"],
                        "avoid_artists": ["Sex Pistols"],
                        "refinement_notes": ["less punk", "more melodic"],
                    },
                },
            ),
        )

        decision = provider.next_decision(self.make_turn())

        self.assertEqual(decision.kind, "update")
        self.assertEqual(decision.request_patch["intent"], "more")
        self.assertEqual(decision.request_patch["reference_artists"], ["Oasis"])
        self.assertEqual(
            decision.request_patch["avoid_artists"],
            ["Sex Pistols"],
        )

    def test_prefers_request_update_when_multiple_tool_calls_are_returned(
        self,
    ) -> None:
        response = tool_response("agent_finish", {"summary": "done"})
        response["choices"][0]["message"]["tool_calls"].append(
            {
                "type": "function",
                "function": {
                    "name": "agent_update_request",
                    "arguments": json.dumps(
                        {
                            "summary": "capture exclusion",
                            "request_patch": {
                                "exclude_terms": ["pink floyd"]
                            },
                        }
                    ),
                },
            }
        )
        provider = DeepSeekProvider(
            "test-key",
            request_json=lambda _payload: response,
        )

        decision = provider.next_decision(self.make_turn())

        self.assertEqual(decision.kind, "update")
        self.assertEqual(
            decision.request_patch,
            {"exclude_terms": ["pink floyd"]},
        )

    def test_environment_configuration_is_optional_or_required(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(configured_llm_provider("auto"))
            with self.assertRaises(ValueError):
                configured_llm_provider("deepseek")

        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "secret",
                "DEEPSEEK_MODEL": "custom-model",
            },
            clear=True,
        ):
            provider = configured_llm_provider("auto")

        self.assertIsInstance(provider, DeepSeekProvider)
        self.assertEqual(provider.name, "deepseek:custom-model")


if __name__ == "__main__":
    unittest.main()
