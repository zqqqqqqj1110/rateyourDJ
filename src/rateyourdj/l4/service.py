from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from rateyourdj.l1 import JsonProfileStore
from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.l3 import CandidateRetrievalService, RetrievalCandidate
from rateyourdj.l5 import FeedbackSignalModel

from .models import (
    DIVERSITY_PENALTY_WEIGHT,
    RankedSong,
    RankingResult,
    RankingWeights,
)
from .scoring import (
    diversity_similarity,
    ranking_reasons,
    score_candidate,
)


@dataclass(slots=True)
class _ScoredCandidate:
    candidate: RetrievalCandidate
    song: SongProfile
    base_score: float
    breakdown: dict[str, float]
    raw_scores: dict[str, float]


def _artist_key(song: SongProfile) -> str:
    return " ".join(
        str(song.metadata.get("artist") or "").strip().casefold().split()
    )


class RecommendationRankingService:
    def __init__(
        self,
        profile_store: JsonProfileStore,
        song_store: JsonSongStore,
        retrieval_service: CandidateRetrievalService | None = None,
        ranking_weights: RankingWeights | None = None,
    ) -> None:
        self.profile_store = profile_store
        self.song_store = song_store
        self.retrieval_service = retrieval_service or CandidateRetrievalService(
            profile_store,
            song_store,
        )
        self.ranking_weights = ranking_weights or RankingWeights()

    def rank(
        self,
        user_id: str,
        *,
        top_k: int = 20,
        candidate_pool_size: int | None = None,
        max_per_artist: int = 2,
        min_retrieval_score: float = 0.0,
    ) -> RankingResult:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if max_per_artist < 1:
            raise ValueError("max_per_artist must be at least 1")
        if candidate_pool_size is None:
            candidate_pool_size = max(top_k * 5, top_k)
        if candidate_pool_size < top_k:
            raise ValueError("candidate_pool_size must be at least top_k")

        profile = self.profile_store.load(user_id)
        feedback_model = FeedbackSignalModel(profile, self.song_store)
        retrieval = self.retrieval_service.retrieve(
            user_id,
            top_k=candidate_pool_size,
            max_per_artist=max_per_artist,
            min_score=min_retrieval_score,
        )
        missing_candidates: list[str] = []
        remaining: list[_ScoredCandidate] = []
        for candidate in retrieval.candidates:
            if not self.song_store.exists(candidate.candidate_song_id):
                missing_candidates.append(candidate.candidate_song_id)
                continue
            song = self.song_store.load(candidate.candidate_song_id)
            base_score, breakdown, raw_scores = score_candidate(
                profile,
                song,
                candidate,
                feedback_score=feedback_model.score(song),
                weights=self.ranking_weights,
            )
            remaining.append(
                _ScoredCandidate(
                    candidate=candidate,
                    song=song,
                    base_score=base_score,
                    breakdown=breakdown,
                    raw_scores=raw_scores,
                )
            )

        selected: list[tuple[_ScoredCandidate, float, float]] = []
        artist_counts: Counter[str] = Counter()
        while remaining and len(selected) < top_k:
            eligible: list[tuple[float, float, _ScoredCandidate]] = []
            for item in remaining:
                artist_key = _artist_key(item.song)
                if (
                    artist_key
                    and artist_counts[artist_key] >= max_per_artist
                ):
                    continue
                maximum_similarity = max(
                    (
                        diversity_similarity(
                            item.song,
                            existing.song,
                            weights=self.ranking_weights,
                        )
                        for existing, _, _ in selected
                    ),
                    default=0.0,
                )
                penalty = round(
                    self.ranking_weights.diversity_penalty_weight
                    * maximum_similarity,
                    6,
                )
                final_score = round(
                    max(0.0, min(item.base_score - penalty, 1.0)),
                    6,
                )
                eligible.append((final_score, penalty, item))

            if not eligible:
                break
            eligible.sort(
                key=lambda value: (
                    -value[0],
                    value[2].candidate.candidate_song_id,
                )
            )
            final_score, penalty, winner = eligible[0]
            selected.append((winner, final_score, penalty))
            remaining.remove(winner)
            artist_key = _artist_key(winner.song)
            if artist_key:
                artist_counts[artist_key] += 1

        ranked_songs = [
            RankedSong(
                rank=index,
                song_id=item.song.song_id,
                title=item.song.metadata["title"],
                artist=item.song.metadata["artist"],
                final_score=final_score,
                base_score=item.base_score,
                score_breakdown=item.breakdown,
                diversity_penalty=penalty,
                ranking_reasons=ranking_reasons(
                    item.raw_scores,
                    penalty,
                ),
                best_seed_song_id=item.candidate.best_seed_song_id,
                retrieval_sources=item.candidate.retrieval_sources,
            )
            for index, (item, final_score, penalty) in enumerate(
                selected,
                start=1,
            )
        ]
        return RankingResult(
            user_id=user_id,
            seed_song_ids=retrieval.seed_song_ids,
            missing_seed_song_ids=retrieval.missing_seed_song_ids,
            missing_candidate_song_ids=missing_candidates,
            ranked_songs=ranked_songs,
        )
