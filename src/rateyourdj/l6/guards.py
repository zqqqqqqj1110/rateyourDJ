from __future__ import annotations

import re
from typing import Any

from .errors import AgentLoopError
from .models import AgentRequest


def apply_request_patch(
    request: AgentRequest,
    patch: dict[str, Any],
) -> AgentRequest:
    if not patch:
        return request
    allowed = {
        "top_k",
        "max_per_artist",
        "min_retrieval_score",
        "preference_terms",
        "exclude_terms",
        "reference_artists",
        "avoid_artists",
        "refinement_notes",
        "intent",
        "exclude_seen",
    }
    if not set(patch) <= allowed:
        raise AgentLoopError("model request patch contains unknown fields")
    if (
        "top_k" in patch
        and query_has_explicit_count(request.query)
        and patch["top_k"] != request.top_k
    ):
        raise AgentLoopError("model cannot override an explicit song count")
    if request.max_per_artist == 1 and patch.get("max_per_artist", 1) != 1:
        raise AgentLoopError("model cannot relax requested artist diversity")
    if (
        request.min_retrieval_score > 0
        and "min_retrieval_score" in patch
        and (
            not isinstance(patch["min_retrieval_score"], (int, float))
            or patch["min_retrieval_score"] < request.min_retrieval_score
        )
    ):
        raise AgentLoopError(
            "model cannot relax an explicit similarity requirement"
        )
    if request.intent == "more" and patch.get("intent", "more") != "more":
        raise AgentLoopError("model cannot cancel the requested more intent")
    if request.exclude_seen and patch.get("exclude_seen", True) is not True:
        raise AgentLoopError("model cannot include songs already shown")
    # Adding reference artists / refinement notes the query did not imply is not
    # a safety violation (unlike overriding count or diversity). Rather than
    # collapsing the whole model run to rules, drop the unsupported field.
    patch = dict(patch)
    if (
        not request.reference_artists
        and patch.get("reference_artists")
        and not query_implies_similarity_reference(request.query)
    ):
        patch.pop("reference_artists", None)
    if (
        not request.refinement_notes
        and patch.get("refinement_notes")
        and not query_implies_refinement(request.query)
    ):
        patch.pop("refinement_notes", None)
    if not patch:
        return request
    if "exclude_terms" in patch and not isinstance(
        patch["exclude_terms"],
        list,
    ):
        raise AgentLoopError("model exclude_terms must be a string list")
    if "preference_terms" in patch and not isinstance(
        patch["preference_terms"],
        list,
    ):
        raise AgentLoopError("model preference_terms must be a string list")
    for field_name in (
        "reference_artists",
        "avoid_artists",
        "refinement_notes",
    ):
        if field_name in patch and not isinstance(patch[field_name], list):
            raise AgentLoopError(f"model {field_name} must be a string list")
    values = request.to_dict()
    values.update(patch)
    values["preference_terms"] = unique(
        [
            *request.preference_terms,
            *(
                patch.get("preference_terms", [])
                if isinstance(patch.get("preference_terms", []), list)
                else []
            ),
        ]
    )
    values["exclude_terms"] = unique(
        [
            *request.exclude_terms,
            *(
                patch.get("exclude_terms", [])
                if isinstance(patch.get("exclude_terms", []), list)
                else []
            ),
        ]
    )
    for field_name in (
        "reference_artists",
        "avoid_artists",
        "refinement_notes",
    ):
        values[field_name] = unique(
            [
                *getattr(request, field_name),
                *(
                    patch.get(field_name, [])
                    if isinstance(patch.get(field_name, []), list)
                    else []
                ),
            ]
        )
    if (
        isinstance(values["top_k"], bool)
        or not isinstance(values["top_k"], int)
        or not 1 <= values["top_k"] <= 50
    ):
        raise AgentLoopError("model top_k must be between 1 and 50")
    if (
        isinstance(values["max_per_artist"], bool)
        or not isinstance(values["max_per_artist"], int)
        or not 1 <= values["max_per_artist"] <= 10
    ):
        raise AgentLoopError("model max_per_artist must be between 1 and 10")
    min_score = values["min_retrieval_score"]
    if (
        isinstance(min_score, bool)
        or not isinstance(min_score, (int, float))
        or not 0 <= float(min_score) <= 1
    ):
        raise AgentLoopError("model min_retrieval_score must be between 0 and 1")
    for field_name in (
        "preference_terms",
        "exclude_terms",
        "reference_artists",
        "avoid_artists",
        "refinement_notes",
    ):
        terms = values[field_name]
        if (
            not isinstance(terms, list)
            or not all(isinstance(term, str) and term.strip() for term in terms)
        ):
            raise AgentLoopError(f"model {field_name} must be a string list")
        values[field_name] = unique(
            [normalized(term) for term in terms if normalized(term)]
        )
    values["preference_terms"] = canonical_preference_terms(
        values["preference_terms"]
    )
    if values["intent"] not in {"recommend", "more"}:
        raise AgentLoopError("model intent must be recommend or more")
    if request.intent != "more" and values["intent"] == "more":
        if not query_implies_refinement(request.query):
            raise AgentLoopError("model cannot invent a more intent")
    if values["intent"] != "more":
        values["exclude_seen"] = False
    elif query_implies_refinement(request.query):
        values["exclude_seen"] = True
    if not isinstance(values["exclude_seen"], bool):
        raise AgentLoopError("model exclude_seen must be boolean")
    return AgentRequest(
        query=request.query,
        top_k=values["top_k"],
        max_per_artist=values["max_per_artist"],
        min_retrieval_score=float(min_score),
        preference_terms=values["preference_terms"],
        exclude_terms=values["exclude_terms"],
        reference_artists=values["reference_artists"],
        avoid_artists=values["avoid_artists"],
        refinement_notes=values["refinement_notes"],
        intent=values["intent"],
        exclude_seen=values["exclude_seen"],
    )


def validate_retrieval_arguments(
    arguments: dict[str, Any],
    request: AgentRequest,
) -> None:
    for name, maximum in (("top_k", 1000), ("max_per_artist", 10)):
        value = arguments[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= maximum
        ):
            raise AgentLoopError(f"{name} is outside its allowed range")
    if request.max_per_artist == 1 and arguments["max_per_artist"] != 1:
        raise AgentLoopError("model cannot relax requested artist diversity")
    min_score = arguments["min_score"]
    if (
        isinstance(min_score, bool)
        or not isinstance(min_score, (int, float))
        or not 0 <= float(min_score) <= 1
    ):
        raise AgentLoopError("min_score must be between 0 and 1")


def canonical_preference_terms(terms: list[str]) -> list[str]:
    aliases = {
        "british rock": "british",
        "uk rock": "british",
        "english rock": "british",
        "britpop": "british",
        "英伦摇滚": "british",
    }
    return unique(
        [
            aliases.get(resolved, resolved)
            for term in terms
            if (resolved := normalized(term))
        ]
    )


def normalized(value: str) -> str:
    return " ".join(value.replace("_", " ").strip().casefold().split())


def compact(value: str) -> str:
    return "".join(character for character in value if character.isalnum())


def query_has_explicit_count(query: str) -> bool:
    return bool(
        re.search(r"\d{1,2}\s*(?:首|首歌|songs?)", query, re.I)
        or re.search(
            r"(?:二十|十[一二三四五六七八九]?|[一二两三四五六七八九])\s*首",
            query,
        )
    )


def query_implies_refinement(query: str) -> bool:
    lowered = query.casefold()
    markers = (
        "换一批",
        "再来",
        "还是不够",
        "不对",
        "不要这种",
        "别是这种",
        "换个方向",
        "重新来",
        "不想要",
        "不想听",
        "更像",
        "像 ",
        "like ",
        "more like",
    )
    return any(marker in lowered for marker in markers)


def query_implies_similarity_reference(query: str) -> bool:
    lowered = query.casefold()
    return any(
        marker in lowered
        for marker in (
            "像",
            "更像",
            "类似",
            "差不多",
            "same vibe",
            "similar to",
            "more like",
            "like ",
        )
    )


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
