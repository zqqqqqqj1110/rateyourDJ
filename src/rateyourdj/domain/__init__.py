"""Domain-level capabilities shared by the agent layer.

This package holds business logic that is independent of the L1-L7 module
naming. The first capability is generative track discovery: an LLM proposes
candidate songs from the user's taste profile, and external music providers
ground those proposals against reality to drop hallucinations.
"""

from .discovery import (
    DiscoveredTrack,
    DiscoveryResult,
    DiscoveryService,
    GeneratedCandidate,
    TrackGenerator,
)
from .explanations import (
    Evidence,
    ExplanationGenerator,
    Reason,
    TrackExplanation,
)
from .generators import (
    DeepSeekTrackGenerator,
    TasteSeedTrackGenerator,
    TrackGeneratorError,
)

__all__ = [
    "DeepSeekTrackGenerator",
    "DiscoveredTrack",
    "DiscoveryResult",
    "DiscoveryService",
    "Evidence",
    "ExplanationGenerator",
    "GeneratedCandidate",
    "Reason",
    "TasteSeedTrackGenerator",
    "TrackExplanation",
    "TrackGenerator",
    "TrackGeneratorError",
]
