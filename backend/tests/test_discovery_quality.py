from sqlalchemy import func, select

from app.models.research import Paper, PaperExternalIdentifier, ResearchJob, ResearchJobPaper, SearchResult, SearchRun
from app.services.discovery_quality import (
    QualityDiscoveryService,
    QueryExpander,
    SourceRecord,
    rank_candidates,
)
from app.services.openalex import NormalizedWork


def work(identifier: str, title: str, abstract: str | None, year: int = 2024) -> NormalizedWork:
    return NormalizedWork(
        openalex_id=identifier,
        title=title,
        doi=f"10.1000/{identifier.casefold()}",
        publication_year=year,
        authors=[],
        abstract=abstract,
        landing_page_url=None,
        open_access_metadata={"oa_url": "https://example.org/fulltext"},
    )


def test_query_expansion_is_deterministic_meaningful_and_unique() -> None:
    expander = QueryExpander()
    topic = "AI-based fake news detection on social media"
    queries = expander.expand(topic)
    assert queries == expander.expand(topic)
    assert len(queries) == 5
    assert len({query.casefold() for query in queries}) == 5
    assert "machine learning fake news detection" in queries
    assert "social media misinformation classification" in queries


def test_ranking_uses_title_abstract_synonyms_and_keeps_missing_abstract() -> None:
    topic = "AI-based fake news detection on social media"
    relevant = work("W1", "Transformer fake news detection on social media", "A deep learning misinformation classification study.")
    missing_abstract = work("W2", "Machine learning fake news detection", None)
    irrelevant = work("W3", "Soil moisture forecasting for agriculture", "Crop yield prediction from weather data.")
    ranked = rank_candidates(topic, [irrelevant, missing_abstract, relevant])
    assert [candidate.work.openalex_id for candidate in ranked] == ["W1", "W2", "W3"]
    assert ranked[1].score > 0
    assert "abstract unavailable" in ranked[1].reason
    assert ranked[2].score == 0
    assert "AI methods" in ranked[0].reason


class RepeatingSource:
    source_name = "openalex"

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.records = [
            SourceRecord(work("W1", "Machine learning fake news detection on social media", "Misinformation classification using neural networks."), 1),
            SourceRecord(work("W2", "Transformer misinformation detection", "Fake news detection across social media."), 2),
            SourceRecord(work("W3", "Cross-domain fake news classification", None), 3),
        ]

    def search(self, query: str, *, limit: int) -> list[SourceRecord]:
        self.calls.append((query, limit))
        return self.records[:limit]


def test_provenance_shortlist_and_repeated_retrieval_are_idempotent(session) -> None:
    source = RepeatingSource()
    service = QualityDiscoveryService(source)
    job = ResearchJob(topic="AI-based fake news detection on social media")
    session.add(job)
    session.commit()

    first = service.discover(session, job, total_budget=30, shortlist_size=3)
    session.commit()
    assert len(first.queries) == 5
    assert source.calls and all(limit == 6 for _, limit in source.calls)
    assert first.retrieved_count == 15
    assert first.duplicate_count == 12
    assert len(first.shortlisted) == 3
    assert first.papers_created == 3
    assert first.associations_created == 3
    assert session.scalar(select(func.count()).select_from(SearchRun)) == 5
    assert session.scalar(select(func.count()).select_from(SearchResult)) == 15
    assert session.scalar(select(func.count()).select_from(PaperExternalIdentifier)) == 3
    assert session.scalar(select(func.count()).select_from(Paper)) == 3
    assert session.scalar(select(func.count()).select_from(ResearchJobPaper).where(ResearchJobPaper.included.is_(True))) == 3
    result = session.scalar(select(SearchResult).where(SearchResult.source_record_id == "W1"))
    assert result is not None and result.full_text_available is True
    assert result.selection_reason and "Topic concept coverage" in result.selection_reason

    second = service.discover(session, job, total_budget=30, shortlist_size=3)
    session.commit()
    assert second.papers_created == 0
    assert second.associations_created == 0
    assert session.scalar(select(func.count()).select_from(Paper)) == 3
    assert session.scalar(select(func.count()).select_from(ResearchJobPaper)) == 3
    assert session.scalar(select(func.count()).select_from(SearchRun)) == 10
    assert session.scalar(select(func.count()).select_from(SearchResult)) == 30
