import pytest
from sqlalchemy import select

from app.models.research import Paper, PaperExternalIdentifier, ResearchJob, ResearchJobPaper, SearchResult
from app.schemas.research import MatchStatus
from app.services.arxiv import ArxivClient, ArxivRequestError, ArxivResponseError, canonical_arxiv_id, parse_atom_feed
from app.services.arxiv_discovery import ArxivDiscoveryService, CrossSourceMatcher, validate_arxiv_metadata


ATOM = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry><id>https://arxiv.org/abs/2401.12345v2</id><title> Fake News Detection on Social Media </title><summary>Transformer misinformation classification.</summary><published>2024-01-02T00:00:00Z</published><updated>2024-02-03T00:00:00Z</updated><author><name>Ada Researcher</name></author><category term="cs.CL"/><arxiv:doi>https://doi.org/10.1000/test</arxiv:doi><link rel="alternate" href="https://arxiv.org/abs/2401.12345v2"/><link title="pdf" href="https://arxiv.org/pdf/2401.12345v2" type="application/pdf"/></entry></feed>'''


def test_atom_xml_normalization_namespaces_versions_and_missing_fields() -> None:
    work = parse_atom_feed(ATOM)[0]
    assert (work.canonical_id, work.version, work.doi) == ("2401.12345", "v2", "10.1000/test")
    assert work.categories == ["cs.CL"] and work.pdf_url.endswith("v2")
    assert canonical_arxiv_id("hep-th/9901001v3") == ("hep-th/9901001", "v3")
    assert parse_atom_feed(b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>') == []
    with pytest.raises(ArxivResponseError): parse_atom_feed(b'<feed')


def test_client_retries_429_and_handles_server_failure() -> None:
    responses = iter([(429, {"Retry-After": "3"}, b""), (200, {}, ATOM)])
    waits = []
    client = ArxivClient(transport=lambda *_: next(responses), clock=lambda: 100.0, sleeper=waits.append)
    assert len(client.search("fake news", limit=1)) == 1
    assert waits == [3.0, 3.0]
    failing = ArxivClient(transport=lambda *_: (500, {}, b""), clock=lambda: 100.0, sleeper=lambda _: None)
    with pytest.raises(ArxivRequestError): failing.search("fake news", limit=1)


def test_metadata_validation_and_cross_source_match_statuses(session) -> None:
    work = parse_atom_feed(ATOM)[0]
    existing = Paper(title="Published equivalent", doi="10.1000/test", authors=[], open_access_metadata={})
    session.add(existing); session.commit()
    decision = CrossSourceMatcher().match_arxiv(session, work)
    assert decision.status is MatchStatus.MATCHED_DOI and decision.paper == existing
    assert validate_arxiv_metadata(work.source_metadata)["version"] == "v2"
    with pytest.raises(ValueError): validate_arxiv_metadata({"categories": ["cs.CL"]})


def test_ambiguous_title_author_stays_separate_and_repeated_search_is_idempotent(session) -> None:
    existing = Paper(title="Fake News Detection on Social Media", publication_year=2024, authors=[{"name": "Ada Researcher"}], open_access_metadata={})
    session.add(existing); session.commit()
    class Client:
        def search(self, *_args, **_kwargs): return parse_atom_feed(ATOM)
    job = ResearchJob(topic="AI-based fake news detection on social media")
    session.add(job); session.commit()
    service = ArxivDiscoveryService(Client())
    service.discover(session, job, total_budget=5, shortlist_size=3, expand_queries=False); session.commit()
    result = session.scalar(select(SearchResult).where(SearchResult.search_run.has(research_job_id=job.id)))
    assert result.match_status is MatchStatus.AMBIGUOUS
    assert session.scalar(select(PaperExternalIdentifier).where(PaperExternalIdentifier.source_name == "arxiv")).identifier == "2401.12345"
    service.discover(session, job, total_budget=5, shortlist_size=3, expand_queries=False); session.commit()
    assert len(job.papers) == 1


def test_cached_openalex_attachment_produces_combined_provenance_and_shortlist(session) -> None:
    paper = Paper(title="Transformer fake news detection on social media", openalex_id="W-combined", publication_year=2024, authors=[], abstract="Machine learning misinformation classification.", open_access_metadata={})
    source_job = ResearchJob(topic="AI-based fake news detection on social media")
    target_job = ResearchJob(topic="AI-based fake news detection on social media")
    source_job.papers.append(ResearchJobPaper(paper=paper))
    session.add_all([source_job, target_job]); session.commit()
    attached = ArxivDiscoveryService.attach_cached_openalex_records(session, target_job, source_job, shortlist_size=3)
    session.commit()
    assert attached == 1
    result = session.scalar(select(SearchResult).where(SearchResult.search_run.has(research_job_id=target_job.id)))
    assert result.match_status is MatchStatus.NOT_CHECKED
    assert target_job.papers[0].included is True
