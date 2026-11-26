"""Deterministic multi-query discovery, ranking, provenance, and shortlisting."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import re
from typing import Optional, Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.research import (
    Paper,
    PaperExternalIdentifier,
    ResearchJob,
    ResearchJobPaper,
    SearchResult,
    SearchRun,
)
from app.schemas.research import JobPaperStatus
from app.services.openalex import NormalizedWork, OpenAlexClient, normalize_work
from app.services.shortlist_suitability import assess_suitability, select_shortlist


@dataclass(frozen=True)
class SourceRecord:
    work: NormalizedWork
    source_rank: int


class ScholarlySource(Protocol):
    """Minimal source-independent contract for future API adapters."""

    source_name: str

    def search(self, query: str, *, limit: int) -> list[SourceRecord]: ...


class OpenAlexSourceAdapter:
    """Adapts the existing OpenAlex client to the source-independent contract."""

    source_name = "openalex"

    def __init__(self, client: OpenAlexClient) -> None:
        self._client = client

    def search(self, query: str, *, limit: int) -> list[SourceRecord]:
        payload = self._client.search_works(query, per_page=limit)
        results = payload.get("results")
        if not isinstance(results, list):
            return []
        records: list[SourceRecord] = []
        for index, raw in enumerate(results, start=1):
            work = normalize_work(raw) if isinstance(raw, dict) else None
            if work is not None:
                records.append(SourceRecord(work=work, source_rank=index))
        return records


class QueryExpander:
    """Small deterministic expansion: no LLMs, external services, or randomness."""

    def expand(self, topic: str, *, max_queries: int = 5) -> list[str]:
        cleaned = " ".join(topic.split())
        if not cleaned:
            raise ValueError("A non-empty research topic is required.")
        if not 3 <= max_queries <= 5:
            raise ValueError("max_queries must be between 3 and 5.")
        lowered = cleaned.casefold()
        candidates = [cleaned]
        if "fake news" in lowered or "misinformation" in lowered:
            candidates.extend([
                "machine learning fake news detection",
                "social media misinformation classification",
                "deep learning misinformation detection",
                "transformer fake news detection",
                "cross-domain fake news classification",
            ])
        else:
            terms = _terms(cleaned)
            if terms:
                phrase = " ".join(terms[:4])
                candidates.extend([
                    f"{phrase} classification",
                    f"{phrase} machine learning",
                    f"{phrase} evaluation",
                    f"{phrase} generalization",
                ])
        unique: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = _query_key(candidate)
            if key and key not in seen:
                seen.add(key)
                unique.append(candidate)
            if len(unique) == max_queries:
                break
        return unique


@dataclass(frozen=True)
class RankedCandidate:
    work: NormalizedWork
    score: float
    reason: str
    title_concepts: tuple[str, ...]
    abstract_concepts: tuple[str, ...]


def rank_candidates(topic: str, works: Sequence[NormalizedWork]) -> list[RankedCandidate]:
    concepts = _topic_concepts(topic)
    ranked: list[RankedCandidate] = []
    for work in works:
        title_matches = tuple(name for name, aliases in concepts if _contains_alias(work.title, aliases))
        abstract_matches = tuple(name for name, aliases in concepts if _contains_alias(work.abstract or "", aliases))
        all_matches = set(title_matches) | set(abstract_matches)
        total = len(concepts) or 1
        title_coverage = len(title_matches) / total
        abstract_coverage = len(abstract_matches) / total
        combined_coverage = len(all_matches) / total
        if work.abstract:
            score = 0.55 * title_coverage + 0.25 * abstract_coverage + 0.20 * combined_coverage
            abstract_note = f"abstract concepts: {', '.join(abstract_matches) or 'none'}"
        else:
            score = 0.75 * title_coverage + 0.25 * combined_coverage
            abstract_note = "abstract unavailable; title evidence retained"
        reason = (
            f"Topic concept coverage {len(all_matches)}/{total}; title concepts: "
            f"{', '.join(title_matches) or 'none'}; {abstract_note}.")
        ranked.append(RankedCandidate(
            work=work,
            score=round(score, 4),
            reason=reason,
            title_concepts=title_matches,
            abstract_concepts=abstract_matches,
        ))
    return sorted(ranked, key=lambda item: (-item.score, item.work.title.casefold(), item.work.openalex_id))


@dataclass
class QualityDiscoveryResult:
    queries: list[str]
    retrieved_count: int = 0
    duplicate_count: int = 0
    shortlisted: list[RankedCandidate] = field(default_factory=list)
    papers_created: int = 0
    associations_created: int = 0


class QualityDiscoveryService:
    """Runs expanded retrievals and persists both search provenance and selections."""

    def __init__(self, source: ScholarlySource, expander: Optional[QueryExpander] = None) -> None:
        self._source = source
        self._expander = expander or QueryExpander()

    def discover(
        self,
        session: Session,
        research_job: ResearchJob,
        *,
        total_budget: int = 30,
        shortlist_size: int = 5,
    ) -> QualityDiscoveryResult:
        if not 1 <= total_budget <= 30:
            raise ValueError("total_budget must be between 1 and 30.")
        if not 3 <= shortlist_size <= 5:
            raise ValueError("shortlist_size must be between 3 and 5.")
        queries = self._expander.expand(research_job.topic)
        per_query = max(1, total_budget // len(queries))
        collected: list[tuple[SearchRun, SourceRecord]] = []
        for query in queries:
            run = SearchRun(research_job=research_job, source_name=self._source.source_name, query_text=query, requested_limit=per_query)
            session.add(run)
            session.flush()
            records = self._source.search(query, limit=per_query)
            run.retrieved_count = len(records)
            collected.extend((run, record) for record in records)

        unique: list[NormalizedWork] = []
        canonical: dict[str, NormalizedWork] = {}
        duplicate_count = 0
        for _, record in collected:
            existing = next((work for work in unique if _same_work(work, record.work)), None)
            if existing is None:
                unique.append(record.work)
                canonical[record.work.openalex_id] = record.work
            else:
                canonical[record.work.openalex_id] = existing
                duplicate_count += 1

        rankings = rank_candidates(research_job.topic, unique)
        suitability = assess_suitability(research_job.topic, rankings)
        shortlisted_suitability = select_shortlist(suitability, shortlist_size=shortlist_size)
        selected_ids = {candidate.ranked.work.openalex_id for candidate in shortlisted_suitability}
        suitability_by_id = {candidate.ranked.work.openalex_id: candidate for candidate in suitability}
        papers: dict[str, Paper] = {}
        result = QualityDiscoveryResult(queries=queries, retrieved_count=len(collected), duplicate_count=duplicate_count, shortlisted=[candidate.ranked for candidate in shortlisted_suitability])
        for work in unique:
            paper = self._find_or_create_paper(session, work, result)
            papers[work.openalex_id] = paper
            self._ensure_identifier(session, paper, work.openalex_id)
            candidate = suitability_by_id[work.openalex_id]
            link = session.scalar(select(ResearchJobPaper).where(
                ResearchJobPaper.research_job_id == research_job.id,
                ResearchJobPaper.paper_id == paper.id,
            ))
            included = work.openalex_id in selected_ids
            if link is None:
                link = ResearchJobPaper(research_job=research_job, paper=paper)
                session.add(link)
                result.associations_created += 1
            link.status = JobPaperStatus.SCREENED
            link.included = included
            link.relevance_score = candidate.ranked.score
            link.decision_reason = ("Selected for prototype shortlist. " if included else "Not selected after topical suitability and tie-level diversity review. ") + candidate.reason

        for run, record in collected:
            work = canonical[record.work.openalex_id]
            paper = papers[work.openalex_id]
            candidate = suitability_by_id[work.openalex_id]
            oa_url = work.open_access_metadata.get("oa_url")
            session.add(SearchResult(
                search_run=run,
                paper=paper,
                source_record_id=record.work.openalex_id,
                source_rank=record.source_rank,
                relevance_score=candidate.ranked.score,
                selection_reason=candidate.reason,
                included=work.openalex_id in selected_ids,
                full_text_url=oa_url if isinstance(oa_url, str) else None,
                full_text_available=bool(oa_url),
                has_abstract=bool(work.abstract),
                has_landing_page=bool(work.landing_page_url),
                metadata_completeness=_metadata_completeness(work),
            ))
        session.flush()
        return result

    @staticmethod
    def _find_or_create_paper(session: Session, work: NormalizedWork, result: QualityDiscoveryResult) -> Paper:
        conditions = [Paper.openalex_id == work.openalex_id]
        if work.doi:
            conditions.append(Paper.doi == work.doi)
        paper = session.scalar(select(Paper).where(or_(*conditions)))
        if paper is None and work.publication_year is not None:
            paper = next((candidate for candidate in session.scalars(select(Paper).where(Paper.publication_year == work.publication_year)) if _title_key(candidate.title) == _title_key(work.title)), None)
        if paper is not None:
            # Refresh compact OA metadata only for the same stable OpenAlex ID.
            # Preserve other source-specific metadata already stored on the paper.
            if paper.openalex_id == work.openalex_id and work.open_access_metadata:
                paper.open_access_metadata = {**(paper.open_access_metadata or {}), **work.open_access_metadata}
            return paper
        paper = Paper(title=work.title, doi=work.doi, openalex_id=work.openalex_id, publication_year=work.publication_year, authors=work.authors, abstract=work.abstract, landing_page_url=work.landing_page_url, open_access_metadata=work.open_access_metadata, publication_type=work.publication_type, publication_type_source="openalex" if work.publication_type else None)
        session.add(paper)
        session.flush()
        result.papers_created += 1
        return paper

    @staticmethod
    def _ensure_identifier(session: Session, paper: Paper, identifier: str) -> None:
        existing = session.scalar(select(PaperExternalIdentifier).where(PaperExternalIdentifier.source_name == "openalex", PaperExternalIdentifier.identifier == identifier))
        if existing is None:
            session.add(PaperExternalIdentifier(paper=paper, source_name="openalex", identifier=identifier))


def _topic_concepts(topic: str) -> list[tuple[str, tuple[str, ...]]]:
    lowered = topic.casefold()
    concepts: list[tuple[str, tuple[str, ...]]] = []
    mappings = [
        ("fake news", ("fake news", "misinformation", "disinformation")),
        ("detection", ("detection", "detect", "classification", "classifier")),
        ("social media", ("social media", "social network", "twitter", "online platform")),
        ("AI methods", ("artificial intelligence", "machine learning", "deep learning", "transformer", "neural network", " ai ")),
    ]
    for name, aliases in mappings:
        if name == "AI methods":
            present = bool(re.search(r"\bai\b", lowered)) or any(alias in lowered for alias in aliases[:-1])
        else:
            present = any(alias in lowered for alias in aliases)
        if present:
            concepts.append((name, aliases))
    if not concepts:
        concepts = [(term, (term,)) for term in _terms(topic)[:5]]
    return concepts


def _contains_alias(text: str, aliases: tuple[str, ...]) -> bool:
    lowered = f" {text.casefold()} "
    return any(f" {alias} " in lowered or alias in lowered for alias in aliases)


def _same_work(left: NormalizedWork, right: NormalizedWork) -> bool:
    if left.openalex_id == right.openalex_id or (left.doi and left.doi == right.doi):
        return True
    return left.publication_year is not None and left.publication_year == right.publication_year and _title_key(left.title) == _title_key(right.title)


def _title_key(title: str) -> str:
    return re.sub(r"\W+", "", title.casefold())


def _terms(text: str) -> list[str]:
    stopwords = {"a", "an", "and", "based", "for", "in", "of", "on", "the", "to", "with"}
    return [term for term in re.findall(r"[a-z0-9]{2,}", text.casefold()) if term not in stopwords]


def _query_key(query: str) -> str:
    return " ".join(_terms(query))


def _metadata_completeness(work: NormalizedWork) -> float:
    """Fraction of three source-provided discovery fields; full text is separate."""
    available = (bool(work.abstract), bool(work.landing_page_url), bool(work.publication_type))
    return round(sum(available) / len(available), 3)
