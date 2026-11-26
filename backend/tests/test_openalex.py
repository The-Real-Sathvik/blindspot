import json

import pytest
from sqlalchemy import func, select

from app.core.config import OpenAlexSettings
from app.models.research import Paper, ResearchJob, ResearchJobPaper
from app.services.openalex import (
    OpenAlexClient,
    OpenAlexDiscoveryService,
    OpenAlexRequestError,
    OpenAlexResponseError,
    normalize_work,
)


def test_normalize_retains_only_explicit_open_pdf_locations():
    record = {'id': 'https://openalex.org/W1', 'title': 'Research', 'open_access': {'is_oa': True},
              'best_oa_location': {'is_oa': True, 'pdf_url': 'https://pmc.ncbi.nlm.nih.gov/articles/PMC1/pdf/a.pdf', 'source': {'type': 'repository'}},
              'locations': [{'is_oa': False, 'pdf_url': 'https://example.com/private.pdf', 'source': {'type': 'journal'}},
                            {'is_oa': True, 'landing_page_url': 'https://example.com/landing', 'source': {'type': 'journal'}}]}
    metadata = normalize_work(record).open_access_metadata
    assert metadata['pdf_locations'] == [{'is_oa': True, 'source_type': 'repository', 'pdf_url': 'https://pmc.ncbi.nlm.nih.gov/articles/PMC1/pdf/a.pdf'}]


def work_record(
    work_id: str = "https://openalex.org/W1",
    *,
    doi: str = "https://doi.org/10.1000/example",
    title: str = "AI fake news detection on social media",
) -> dict:
    return {
        "id": work_id,
        "title": title,
        "doi": doi,
        "publication_year": 2024,
        "authorships": [{"author": {"display_name": "Ada Researcher", "id": "https://openalex.org/A1", "orcid": "https://orcid.org/0000-0001"}}],
        "abstract_inverted_index": {"Fake": [0], "news": [1], "detection": [2]},
        "primary_location": {"landing_page_url": "https://example.org/work"},
        "open_access": {"is_oa": True, "oa_status": "gold", "oa_url": "https://example.org/open.pdf"},
    }


def client_for(responses: list[tuple[int, dict, bytes]], *, sleeper_calls: list[float] | None = None) -> OpenAlexClient:
    calls = iter(responses)

    def transport(url, headers, timeout):
        assert "api_key" not in url
        assert headers["Authorization"] == "Bearer test-key"
        assert timeout == 15.0
        return next(calls)

    return OpenAlexClient(
        OpenAlexSettings(api_key="test-key"),
        transport=transport,
        clock=lambda: 100.0,
        sleeper=(sleeper_calls.append if sleeper_calls is not None else lambda _seconds: None),
    )


def test_search_success_and_cache_do_not_repeat_request() -> None:
    client = client_for([(200, {}, json.dumps({"results": [work_record()]}).encode())])
    first = client.search_works("fake news", per_page=10)
    second = client.search_works("fake news", per_page=10)
    assert first == second
    assert first["results"][0]["id"] == "https://openalex.org/W1"


def test_rate_limit_response_is_retried_with_retry_after() -> None:
    sleeps: list[float] = []
    client = client_for([
        (429, {"Retry-After": "2"}, b'{"error":"slow down"}'),
        (200, {}, json.dumps({"results": []}).encode()),
    ], sleeper_calls=sleeps)
    assert client.search_works("fake news") == {"results": []}
    assert sleeps == [2.0, 0.04]


def test_api_failure_and_malformed_response_are_reported_without_secrets() -> None:
    failing = client_for([(503, {}, b"temporary") for _ in range(4)])
    with pytest.raises(OpenAlexRequestError, match="HTTP status 503") as error:
        failing.search_works("fake news")
    assert "test-key" not in str(error.value)

    malformed = client_for([(200, {}, b"not-json")])
    with pytest.raises(OpenAlexResponseError, match="invalid JSON"):
        malformed.search_works("fake news")


def test_normalize_missing_metadata_and_malformed_work() -> None:
    normalized = normalize_work({"id": "W2", "title": "Sparse record", "publication_year": "unknown"})
    assert normalized is not None
    assert normalized.doi is None
    assert normalized.abstract is None
    assert normalized.authors == []
    assert normalized.open_access_metadata == {}
    assert normalize_work({"id": "W3"}) is None


def test_discovery_deduplicates_screens_and_persists_idempotently(session) -> None:
    payload = {"results": [
        work_record(),
        work_record("https://openalex.org/W2"),  # Same DOI: duplicate.
        work_record(
            "https://openalex.org/W3",
            doi="https://doi.org/10.1000/unique",
            title="Social-media misinformation benchmark evaluation",
        ),
        {"id": "W-bad"},  # Missing title: malformed.
    ]}
    client = client_for([(200, {}, json.dumps(payload).encode())])
    service = OpenAlexDiscoveryService(client)
    job = ResearchJob(topic="AI-based fake news detection on social media")
    session.add(job)
    session.commit()

    first = service.discover(session, job, limit=10)
    session.commit()
    assert first.api_result_count == 4
    assert first.normalized_count == 3
    assert first.malformed_count == 1
    assert first.duplicate_count == 1
    assert first.papers_created == 2
    assert first.associations_created == 2
    assert first.decisions[0].score > 0
    assert "Matched" in first.decisions[0].reason
    assert session.scalar(select(func.count()).select_from(Paper)) == 2
    assert session.scalar(select(func.count()).select_from(ResearchJobPaper)) == 2

    second = service.discover(session, job, limit=10)
    session.commit()
    assert second.papers_created == 0
    assert second.existing_papers == 2
    assert second.associations_created == 0
    assert session.scalar(select(func.count()).select_from(Paper)) == 2
    assert session.scalar(select(func.count()).select_from(ResearchJobPaper)) == 2
