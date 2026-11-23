"""Explainable shortlist selection that is separate from topical relevance scoring."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from app.services.discovery_quality import RankedCandidate


@dataclass(frozen=True)
class SuitabilityCandidate:
    ranked: "RankedCandidate"
    directness: str
    publication_role: str
    role_provenance: str
    context_hints: tuple[str, ...]
    has_abstract: bool
    has_landing_page: bool
    metadata_completeness: float

    @property
    def reason(self) -> str:
        hints = "; ".join(self.context_hints) if self.context_hints else "no additional context hint"
        return (
            f"Topical suitability: {self.directness}. {self.ranked.reason} "
            f"Publication role: {self.publication_role} ({self.role_provenance}). "
            f"Screening context: {hints}. Metadata completeness: {self.metadata_completeness:.3f} "
            f"(abstract={'provided' if self.has_abstract else 'not provided by source'}, "
            f"landing page={'provided' if self.has_landing_page else 'not provided by source'}).")


def assess_suitability(topic: str, ranked: Sequence["RankedCandidate"]) -> list[SuitabilityCandidate]:
    return [_assess(candidate) for candidate in ranked]


def select_shortlist(candidates: Sequence[SuitabilityCandidate], *, shortlist_size: int) -> list[SuitabilityCandidate]:
    """Choose topical candidates first; use diversity only to break suitability ties."""
    if not 3 <= shortlist_size <= 5:
        raise ValueError("shortlist_size must be between 3 and 5.")
    selected: list[SuitabilityCandidate] = []
    seen_contexts: set[str] = set()
    for directness in ("direct", "adjacent", "related"):
        group = [item for item in candidates if item.directness == directness]
        scores = sorted({item.ranked.score for item in group}, reverse=True)
        for score in scores:
            tied = sorted((item for item in group if item.ranked.score == score), key=lambda item: (item.ranked.work.title.casefold(), item.ranked.work.openalex_id))
            # A new, explicitly labelled metadata hint breaks otherwise equal suitability ties.
            tied.sort(key=lambda item: (
                item.publication_role == "review_or_survey_hint",
                not bool(set(item.context_hints) - seen_contexts),
                item.ranked.work.title.casefold(),
                item.ranked.work.openalex_id,
            ))
            for item in tied:
                if len(selected) == shortlist_size:
                    return selected
                selected.append(item)
                seen_contexts.update(item.context_hints)
    return selected


def _assess(candidate: "RankedCandidate") -> SuitabilityCandidate:
    work = candidate.work
    title = work.title.casefold()
    title_concepts = set(candidate.title_concepts)
    core = {"fake news", "detection", "social media"}
    if core.issubset(title_concepts):
        directness = "direct"
    elif len(core & title_concepts) >= 2 and candidate.score > 0:
        directness = "adjacent"
    elif candidate.score > 0:
        directness = "related"
    else:
        directness = "insufficient"

    if "review" in title or "survey" in title:
        publication_role = "review_or_survey_hint"
        role_provenance = "heuristic title cue; not source-verified study design"
    elif work.publication_type:
        publication_role = "unknown"
        role_provenance = f"source-provided publication type: {work.publication_type}; does not establish study design"
    else:
        publication_role = "unknown"
        role_provenance = "not provided by source"

    hints: list[str] = []
    searchable = f"{work.title} {work.abstract or ''}".casefold()
    for label, phrase in (
        ("unverified metadata mention: transformer", "transformer"),
        ("unverified metadata mention: deep learning", "deep learning"),
        ("unverified metadata mention: neural network", "neural network"),
        ("unverified metadata mention: machine learning", "machine learning"),
        ("unverified metadata mention: multimodal", "multimodal"),
        ("unverified metadata context: covid-19", "covid-19"),
        ("unverified metadata context: streaming", "streaming"),
    ):
        if phrase in searchable:
            hints.append(label)
    completeness = round(sum((bool(work.abstract), bool(work.landing_page_url), bool(work.publication_type))) / 3, 3)
    return SuitabilityCandidate(candidate, directness, publication_role, role_provenance, tuple(hints), bool(work.abstract), bool(work.landing_page_url), completeness)
