"""arXiv persistence and conservative cross-source matching."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.research import Paper, PaperExternalIdentifier, ResearchJob, ResearchJobPaper, SearchResult, SearchRun
from app.schemas.research import JobPaperStatus, MatchStatus
from app.services.arxiv import ArxivClient, ArxivWork
from app.services.discovery_quality import QueryExpander, rank_candidates
from app.services.openalex import NormalizedWork
from app.services.shortlist_suitability import assess_suitability, select_shortlist


@dataclass(frozen=True)
class MatchDecision:
    status: MatchStatus
    paper: Optional[Paper]
    reason: str


def validate_arxiv_metadata(metadata: dict) -> dict:
    allowed = {"canonical_arxiv_id", "version", "published_at", "updated_at", "categories", "abstract_url", "pdf_url", "source_doi"}
    compact = {key: value for key, value in metadata.items() if key in allowed and value not in (None, [], "")}
    if "canonical_arxiv_id" not in compact or not isinstance(compact["canonical_arxiv_id"], str):
        raise ValueError("source metadata requires canonical_arxiv_id")
    if "categories" in compact and (not isinstance(compact["categories"], list) or not all(isinstance(item, str) for item in compact["categories"])):
        raise ValueError("categories must be a list of strings")
    return compact


class CrossSourceMatcher:
    def match_arxiv(self, session: Session, work: ArxivWork) -> MatchDecision:
        if work.doi:
            paper = session.scalar(select(Paper).where(Paper.doi == work.doi))
            if paper:
                return MatchDecision(MatchStatus.MATCHED_DOI, paper, "Canonical DOI matched an existing paper.")
        paper = session.scalar(select(Paper).where(Paper.arxiv_id == work.canonical_id))
        if paper:
            return MatchDecision(MatchStatus.MATCHED_ARXIV_ID, paper, "Canonical arXiv ID matched an existing paper.")
        identifier = session.scalar(select(PaperExternalIdentifier).where(PaperExternalIdentifier.source_name == "arxiv", PaperExternalIdentifier.identifier == work.canonical_id))
        if identifier:
            return MatchDecision(MatchStatus.MATCHED_ARXIV_ID, identifier.paper, "Existing arXiv external identifier matched.")
        candidates = session.scalars(select(Paper).where(Paper.publication_year == _year(work.published_at))).all() if _year(work.published_at) else []
        for candidate in candidates:
            if _title_key(candidate.title) == _title_key(work.title) and _author_overlap(candidate.authors, work.authors):
                return MatchDecision(MatchStatus.AMBIGUOUS, None, "Exact normalized title and author overlap found without a stable identifier; record preserved separately.")
        return MatchDecision(MatchStatus.NEW, None, "No stable identifier or unambiguous cross-source match found.")


class ArxivDiscoveryService:
    source_name = "arxiv"

    def __init__(self, client: ArxivClient, matcher: Optional[CrossSourceMatcher] = None, expander: Optional[QueryExpander] = None) -> None:
        self._client, self._matcher, self._expander = client, matcher or CrossSourceMatcher(), expander or QueryExpander()

    def discover(self, session: Session, job: ResearchJob, *, total_budget: int = 10, shortlist_size: int = 5, expand_queries: bool = True) -> list[NormalizedWork]:
        queries = self._expander.expand(job.topic) if expand_queries else [job.topic]
        per_query = max(1, total_budget // len(queries))
        works: list[NormalizedWork] = []
        for query in queries:
            run = SearchRun(research_job=job, source_name="arxiv", query_text=query, requested_limit=per_query)
            session.add(run); session.flush()
            try:
                records = self._client.search(query, limit=per_query)
                run.retrieved_count = len(records)
            except Exception as error:
                run.error_info = type(error).__name__
                continue
            for rank, record in enumerate(records, start=1):
                decision = self._matcher.match_arxiv(session, record)
                paper = decision.paper or self._new_paper(record)
                if decision.paper is None:
                    session.add(paper); session.flush()
                metadata = validate_arxiv_metadata(record.source_metadata)
                external = session.scalar(select(PaperExternalIdentifier).where(PaperExternalIdentifier.source_name == "arxiv", PaperExternalIdentifier.identifier == record.canonical_id))
                if external is None:
                    session.add(PaperExternalIdentifier(paper=paper, source_name="arxiv", identifier=record.canonical_id, source_metadata=metadata))
                link = session.scalar(select(ResearchJobPaper).where(ResearchJobPaper.research_job_id == job.id, ResearchJobPaper.paper_id == paper.id))
                if link is None:
                    link = ResearchJobPaper(research_job=job, paper=paper, status=JobPaperStatus.DISCOVERED)
                    session.add(link)
                session.add(SearchResult(search_run=run, paper=paper, source_record_id=record.canonical_id, source_rank=rank, full_text_url=record.pdf_url, full_text_available=bool(record.pdf_url), has_abstract=bool(record.abstract), has_landing_page=bool(record.abstract_url), metadata_completeness=round(sum((bool(record.abstract), bool(record.abstract_url), True)) / 3, 3), match_status=decision.status, match_reason=decision.reason))
                works.append(_normalized(record, paper))
        session.flush()
        self._rerank_job(session, job, shortlist_size)
        return works

    @staticmethod
    def attach_cached_openalex_records(session: Session, target_job: ResearchJob, source_job: ResearchJob, *, shortlist_size: int = 5) -> int:
        """Reuse previously persisted OpenAlex metadata without another API request."""
        run = SearchRun(research_job=target_job, source_name="openalex", query_text=f"cached provenance from job {source_job.id}", requested_limit=max(1, len(source_job.papers)), retrieved_count=0)
        session.add(run); session.flush()
        attached = 0
        seen: set[str] = set()
        for source_link in source_job.papers:
            paper = source_link.paper
            record_id = paper.openalex_id or paper.id
            if record_id in seen:
                continue
            seen.add(record_id)
            link = session.scalar(select(ResearchJobPaper).where(ResearchJobPaper.research_job_id == target_job.id, ResearchJobPaper.paper_id == paper.id))
            if link is None:
                session.add(ResearchJobPaper(research_job=target_job, paper=paper, status=JobPaperStatus.DISCOVERED))
            oa_url = paper.open_access_metadata.get("oa_url") if isinstance(paper.open_access_metadata, dict) else None
            session.add(SearchResult(search_run=run, paper=paper, source_record_id=record_id, source_rank=len(seen), full_text_url=oa_url if isinstance(oa_url, str) else None, full_text_available=bool(oa_url), has_abstract=bool(paper.abstract), has_landing_page=bool(paper.landing_page_url), metadata_completeness=round(sum((bool(paper.abstract), bool(paper.landing_page_url), bool(paper.publication_type))) / 3, 3), match_status=MatchStatus.NOT_CHECKED, match_reason="Reused persisted OpenAlex metadata; no new API request."))
            attached += 1
        run.retrieved_count = attached
        session.flush()
        ArxivDiscoveryService._rerank_job(session, target_job, shortlist_size)
        return attached

    @staticmethod
    def _new_paper(work: ArxivWork) -> Paper:
        return Paper(title=work.title, doi=work.doi, arxiv_id=work.canonical_id, publication_year=_year(work.published_at), authors=work.authors, abstract=work.abstract, landing_page_url=work.abstract_url, open_access_metadata={"arxiv_pdf_link": work.pdf_url, "pdf_link_is_metadata_only": True}, publication_type="preprint", publication_type_source="arxiv")

    @staticmethod
    def _rerank_job(session: Session, job: ResearchJob, shortlist_size: int) -> None:
        papers = [link.paper for link in job.papers]
        normalized = [NormalizedWork(paper.openalex_id or f"arxiv:{paper.arxiv_id or paper.id}", paper.title, paper.doi, paper.publication_year, paper.authors, paper.abstract, paper.landing_page_url, paper.open_access_metadata, paper.publication_type) for paper in papers]
        selected = select_shortlist(assess_suitability(job.topic, rank_candidates(job.topic, normalized)), shortlist_size=shortlist_size)
        selected_ids = {item.ranked.work.openalex_id for item in selected}
        details = {item.ranked.work.openalex_id: item for item in assess_suitability(job.topic, rank_candidates(job.topic, normalized))}
        for paper in papers:
            key = paper.openalex_id or f"arxiv:{paper.arxiv_id or paper.id}"
            link = next(item for item in job.papers if item.paper_id == paper.id)
            item = details[key]
            link.included = key in selected_ids
            link.status = JobPaperStatus.SCREENED
            link.relevance_score = item.ranked.score
            link.decision_reason = ("Selected for combined shortlist. " if link.included else "Not selected after topical suitability review. ") + item.reason


def _normalized(work: ArxivWork, paper: Paper) -> NormalizedWork:
    return NormalizedWork(f"arxiv:{work.canonical_id}", work.title, work.doi, _year(work.published_at), work.authors, work.abstract, work.abstract_url, {"oa_url": work.pdf_url, "pdf_link_is_metadata_only": True}, "preprint")


def _year(value: Optional[str]) -> Optional[int]:
    return int(value[:4]) if value and value[:4].isdigit() else None


def _title_key(value: str) -> str:
    return re.sub(r"\W+", "", value.casefold())


def _author_overlap(left: list[dict], right: list[dict[str, str]]) -> bool:
    left_names = {str(item.get("name", "")).casefold().split()[-1] for item in left if item.get("name")}
    right_names = {item["name"].casefold().split()[-1] for item in right if item.get("name")}
    return bool(left_names & right_names)
