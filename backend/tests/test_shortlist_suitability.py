import json
from pathlib import Path

from app.services.discovery_quality import rank_candidates
from app.services.openalex import NormalizedWork
from app.services.shortlist_suitability import assess_suitability, select_shortlist


TOPIC = "AI-based fake news detection on social media"


def load_fixture() -> list[NormalizedWork]:
    fixture_path = Path(__file__).parent / "fixtures" / "shortlist_quality.json"
    records = json.loads(fixture_path.read_text(encoding="utf-8"))
    return [NormalizedWork(
        openalex_id=record["id"], title=record["title"], doi=None,
        publication_year=2024, authors=[], abstract=record["abstract"],
        landing_page_url=None, open_access_metadata={}, publication_type=record["publication_type"],
    ) for record in records]


def test_manually_reviewable_fixture_orders_relevance_and_excludes_topic_drift() -> None:
    suitability = assess_suitability(TOPIC, rank_candidates(TOPIC, load_fixture()))
    selected = select_shortlist(suitability, shortlist_size=3)
    selected_ids = [candidate.ranked.work.openalex_id for candidate in selected]
    assert "W-direct-transformer" in selected_ids
    assert "W-direct-review" in selected_ids
    assert "W-adjacent-covid" in selected_ids
    assert "W-irrelevant" not in selected_ids


def test_publication_role_and_missing_metadata_are_explicit_not_quality_claims() -> None:
    candidates = {item.ranked.work.openalex_id: item for item in assess_suitability(TOPIC, rank_candidates(TOPIC, load_fixture()))}
    review = candidates["W-direct-review"]
    article = candidates["W-direct-transformer"]
    assert review.publication_role == "review_or_survey_hint"
    assert "heuristic title cue" in review.role_provenance
    assert review.has_abstract is False
    assert "not provided by source" in review.reason
    assert article.publication_role == "unknown"
    assert "does not establish study design" in article.role_provenance


def test_ties_saturation_diversity_and_repeat_selection_are_deterministic() -> None:
    works = [
        NormalizedWork("W1", "Transformer fake news detection on social media", None, 2024, [], None, None, {}, "article"),
        NormalizedWork("W2", "Deep learning fake news detection on social media", None, 2024, [], None, None, {}, "article"),
        NormalizedWork("W3", "Machine learning fake news detection on social media", None, 2024, [], None, None, {}, "article"),
    ]
    suitability = assess_suitability(TOPIC, rank_candidates(TOPIC, works))
    assert {candidate.ranked.score for candidate in suitability} == {1.0}
    first = select_shortlist(suitability, shortlist_size=3)
    second = select_shortlist(suitability, shortlist_size=3)
    assert [item.ranked.work.openalex_id for item in first] == [item.ranked.work.openalex_id for item in second]
    assert any("transformer" in hint for item in first for hint in item.context_hints)
    assert any("deep learning" in hint for item in first for hint in item.context_hints)


def test_review_hint_does_not_displace_equally_relevant_unknown_role_candidates() -> None:
    works = [
        NormalizedWork("W1", "Transformer fake news detection on social media", None, 2024, [], None, None, {}, "article"),
        NormalizedWork("W2", "Deep learning fake news detection on social media", None, 2024, [], None, None, {}, "article"),
        NormalizedWork("W3", "Machine learning fake news detection on social media", None, 2024, [], None, None, {}, "article"),
        NormalizedWork("W4", "A survey of fake news detection on social media", None, 2024, [], None, None, {}, "review"),
    ]
    selected = select_shortlist(assess_suitability(TOPIC, rank_candidates(TOPIC, works)), shortlist_size=3)
    assert "W4" not in [item.ranked.work.openalex_id for item in selected]
