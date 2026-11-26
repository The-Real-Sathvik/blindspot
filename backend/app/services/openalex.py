"""OpenAlex work discovery with safe authentication, retries, and persistence."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import json
import re
import time
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import OpenAlexSettings
from app.models.research import Paper, ResearchJob, ResearchJobPaper
from app.schemas.research import JobPaperStatus


WORKS_ENDPOINT = "https://api.openalex.org/works"
Transport = Callable[[str, Mapping[str, str], float], tuple[int, Mapping[str, str], bytes]]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class OpenAlexError(RuntimeError):
    """Base error that deliberately contains no credential or request URL."""


class OpenAlexResponseError(OpenAlexError):
    """The endpoint returned a malformed response."""


class OpenAlexRequestError(OpenAlexError):
    """A request could not be completed after bounded retries."""


@dataclass(frozen=True)
class NormalizedWork:
    openalex_id: str
    title: str
    doi: Optional[str]
    publication_year: Optional[int]
    authors: list[dict[str, Optional[str]]]
    abstract: Optional[str]
    landing_page_url: Optional[str]
    open_access_metadata: dict[str, Any]
    publication_type: Optional[str] = None


@dataclass(frozen=True)
class RelevanceDecision:
    score: float
    matched_terms: tuple[str, ...]
    reason: str


@dataclass
class DiscoveryResult:
    api_result_count: int = 0
    normalized_count: int = 0
    malformed_count: int = 0
    duplicate_count: int = 0
    papers_created: int = 0
    existing_papers: int = 0
    associations_created: int = 0
    decisions: list[RelevanceDecision] = field(default_factory=list)
    works: list[NormalizedWork] = field(default_factory=list)


def _default_transport(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, Mapping[str, str], bytes]:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310: fixed HTTPS endpoint
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers.items()) if error.headers else {}, error.read()
    except URLError as error:
        raise OpenAlexRequestError("OpenAlex network request failed.") from error


class OpenAlexClient:
    """Small, dependency-free adapter for OpenAlex's /works endpoint."""

    def __init__(
        self,
        settings: OpenAlexSettings,
        *,
        transport: Transport = _default_transport,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = time.sleep,
        cache_ttl_seconds: float = 300.0,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._clock = clock
        self._sleeper = sleeper
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
        self._last_request_at: Optional[float] = None

    def search_works(self, topic: str, *, per_page: int = 10) -> dict[str, Any]:
        topic = topic.strip()
        if not topic:
            raise ValueError("A non-empty research topic is required.")
        if not 1 <= per_page <= 100:
            raise ValueError("per_page must be between 1 and 100.")

        cache_key = (topic.casefold(), per_page)
        cached = self._cache.get(cache_key)
        if cached and self._clock() - cached[0] < self._cache_ttl_seconds:
            return cached[1]

        params = urlencode({"search": topic, "per-page": per_page})
        payload = self._get_json(f"{WORKS_ENDPOINT}?{params}")
        self._cache[cache_key] = (self._clock(), payload)
        return payload

    def _get_json(self, url: str) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._settings.api_key}",
            "User-Agent": "Blindspot/0.1 (local academic research assistant)",
        }
        attempts = self._settings.max_retries + 1
        for attempt in range(attempts):
            self._wait_for_rate_limit()
            try:
                status, response_headers, body = self._transport(url, headers, self._settings.timeout_seconds)
            except OpenAlexRequestError:
                if attempt == attempts - 1:
                    raise
                self._sleeper(self._retry_delay(attempt, {}))
                continue

            if status == 200:
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise OpenAlexResponseError("OpenAlex returned invalid JSON.") from error
                if not isinstance(payload, dict):
                    raise OpenAlexResponseError("OpenAlex returned an unexpected response shape.")
                return payload

            if status == 429 or 500 <= status < 600:
                if attempt < attempts - 1:
                    self._sleeper(self._retry_delay(attempt, response_headers))
                    continue
            raise OpenAlexRequestError(f"OpenAlex request failed with HTTP status {status}.")
        raise OpenAlexRequestError("OpenAlex request retry limit reached.")

    def _wait_for_rate_limit(self) -> None:
        if self._last_request_at is None:
            self._last_request_at = self._clock()
            return
        minimum_interval = 1 / self._settings.requests_per_second
        elapsed = self._clock() - self._last_request_at
        if elapsed < minimum_interval:
            self._sleeper(minimum_interval - elapsed)
        self._last_request_at = self._clock()

    @staticmethod
    def _retry_delay(attempt: int, headers: Mapping[str, str]) -> float:
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 30.0)
            except ValueError:
                pass
        return min(0.5 * (2**attempt), 8.0)


def normalize_work(raw_work: Mapping[str, Any]) -> Optional[NormalizedWork]:
    """Convert a variable OpenAlex record to the subset Blindspot persists."""
    raw_id = raw_work.get("id")
    title = raw_work.get("title")
    if not isinstance(raw_id, str) or not raw_id.strip() or not isinstance(title, str) or not title.strip():
        return None

    publication_year = raw_work.get("publication_year")
    if not isinstance(publication_year, int) or not 1600 <= publication_year <= 2100:
        publication_year = None

    authors: list[dict[str, Optional[str]]] = []
    raw_authorships = raw_work.get("authorships")
    if isinstance(raw_authorships, list):
        for authorship in raw_authorships:
            if not isinstance(authorship, Mapping):
                continue
            author = authorship.get("author")
            if not isinstance(author, Mapping):
                continue
            name = author.get("display_name")
            if isinstance(name, str) and name.strip():
                authors.append({
                    "name": name.strip(),
                    "openalex_author_id": _clean_optional_string(author.get("id")),
                    "orcid": _clean_optional_string(author.get("orcid")),
                })

    primary_location = raw_work.get("primary_location")
    landing_page_url = None
    if isinstance(primary_location, Mapping):
        landing_page_url = _clean_optional_string(primary_location.get("landing_page_url"))

    raw_oa = raw_work.get("open_access")
    open_access_metadata: dict[str, Any] = {}
    if isinstance(raw_oa, Mapping):
        for key in ("is_oa", "oa_status", "oa_url", "any_repository_has_fulltext"):
            value = raw_oa.get(key)
            if isinstance(value, (str, bool)) or value is None:
                open_access_metadata[key] = value

    # Retain only compact, explicitly open PDF locations, never raw responses.
    locations = [raw_work.get("best_oa_location"), primary_location]
    if isinstance(raw_work.get("locations"), list):
        locations.extend(raw_work["locations"][:50])
    pdf_locations = []
    for location in locations:
        if not isinstance(location, Mapping) or location.get("is_oa") is not True:
            continue
        url = _clean_optional_string(location.get("pdf_url"))
        source = location.get("source")
        kind = source.get("type") if isinstance(source, Mapping) else None
        if url and len(url) <= 2048 and kind in {"repository", "journal"}:
            item = {"pdf_url": url, "source_type": kind, "is_oa": True}
            if item not in pdf_locations:
                pdf_locations.append(item)
        if len(pdf_locations) == 20:
            break
    if pdf_locations:
        open_access_metadata["pdf_locations"] = pdf_locations

    return NormalizedWork(
        openalex_id=raw_id.strip(),
        title=title.strip(),
        doi=_normalize_doi(raw_work.get("doi")),
        publication_year=publication_year,
        authors=authors,
        abstract=_reconstruct_abstract(raw_work.get("abstract_inverted_index")),
        landing_page_url=landing_page_url,
        open_access_metadata=open_access_metadata,
        publication_type=_clean_optional_string(raw_work.get("type")),
    )


def deduplicate_works(works: Sequence[NormalizedWork]) -> tuple[list[NormalizedWork], int]:
    unique: list[NormalizedWork] = []
    duplicates = 0
    for work in works:
        if any(_same_work(work, candidate) for candidate in unique):
            duplicates += 1
        else:
            unique.append(work)
    return unique, duplicates


def score_relevance(topic: str, work: NormalizedWork) -> RelevanceDecision:
    topic_terms = _meaningful_terms(topic)
    searchable = f"{work.title} {work.abstract or ''}".casefold()
    matched = tuple(term for term in topic_terms if term in searchable)
    score = round(len(matched) / len(topic_terms), 3) if topic_terms else 0.0
    reason = (
        f"Matched {len(matched)} of {len(topic_terms)} meaningful topic terms: "
        f"{', '.join(matched) if matched else 'none'}.")
    return RelevanceDecision(score=score, matched_terms=matched, reason=reason)


class OpenAlexDiscoveryService:
    """Coordinates retrieval, deterministic screening, deduplication, and persistence."""

    def __init__(self, client: OpenAlexClient) -> None:
        self._client = client

    def discover(self, session: Session, research_job: ResearchJob, *, limit: int = 10) -> DiscoveryResult:
        payload = self._client.search_works(research_job.topic, per_page=limit)
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise OpenAlexResponseError("OpenAlex response does not contain a results list.")

        result = DiscoveryResult(api_result_count=len(raw_results))
        normalized: list[NormalizedWork] = []
        for raw_work in raw_results:
            if not isinstance(raw_work, Mapping):
                result.malformed_count += 1
                continue
            work = normalize_work(raw_work)
            if work is None:
                result.malformed_count += 1
                continue
            normalized.append(work)
        result.normalized_count = len(normalized)
        unique_works, result.duplicate_count = deduplicate_works(normalized)
        result.works = unique_works

        for work in unique_works:
            decision = score_relevance(research_job.topic, work)
            result.decisions.append(decision)
            paper = self._find_existing_paper(session, work)
            if paper is None:
                paper = Paper(
                    title=work.title,
                    doi=work.doi,
                    openalex_id=work.openalex_id,
                    publication_year=work.publication_year,
                    authors=work.authors,
                    abstract=work.abstract,
                    landing_page_url=work.landing_page_url,
                    open_access_metadata=work.open_access_metadata,
                    publication_type=work.publication_type,
                    publication_type_source="openalex" if work.publication_type else None,
                )
                session.add(paper)
                session.flush()
                result.papers_created += 1
            else:
                result.existing_papers += 1

            association = session.scalar(select(ResearchJobPaper).where(
                ResearchJobPaper.research_job_id == research_job.id,
                ResearchJobPaper.paper_id == paper.id,
            ))
            if association is None:
                session.add(ResearchJobPaper(
                    research_job_id=research_job.id,
                    paper_id=paper.id,
                    status=JobPaperStatus.DISCOVERED,
                    included=decision.score >= 0.5,
                    relevance_score=decision.score,
                    decision_reason=decision.reason,
                ))
                result.associations_created += 1
        session.flush()
        return result

    @staticmethod
    def _find_existing_paper(session: Session, work: NormalizedWork) -> Optional[Paper]:
        stable_conditions = [Paper.openalex_id == work.openalex_id]
        if work.doi:
            stable_conditions.append(Paper.doi == work.doi)
        paper = session.scalar(select(Paper).where(or_(*stable_conditions)))
        if paper is not None:
            return paper

        if work.publication_year is None:
            return None
        candidates = session.scalars(select(Paper).where(Paper.publication_year == work.publication_year)).all()
        return next((candidate for candidate in candidates if _title_key(candidate.title) == _title_key(work.title)), None)


def _clean_optional_string(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalize_doi(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    doi = value.strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi or None


def _reconstruct_abstract(value: Any) -> Optional[str]:
    if not isinstance(value, Mapping):
        return None
    positions: list[tuple[int, str]] = []
    for word, raw_positions in value.items():
        if not isinstance(word, str) or not isinstance(raw_positions, list):
            continue
        for position in raw_positions:
            if isinstance(position, int) and position >= 0:
                positions.append((position, word))
    if not positions:
        return None
    positions.sort()
    return " ".join(word for _, word in positions)


def _title_key(title: str) -> str:
    return re.sub(r"\W+", "", title.casefold())


def _same_work(left: NormalizedWork, right: NormalizedWork) -> bool:
    if left.openalex_id == right.openalex_id:
        return True
    if left.doi and left.doi == right.doi:
        return True
    return (
        left.publication_year is not None
        and left.publication_year == right.publication_year
        and _title_key(left.title) == _title_key(right.title)
    )


def _meaningful_terms(text: str) -> tuple[str, ...]:
    stopwords = {"a", "an", "and", "based", "for", "in", "of", "on", "the", "to", "with"}
    return tuple(dict.fromkeys(
        term for term in re.findall(r"[a-z0-9]{2,}", text.casefold()) if term not in stopwords
    ))
