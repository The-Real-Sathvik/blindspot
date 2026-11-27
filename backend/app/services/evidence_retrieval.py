"""Deterministic Pass A evidence retrieval over persisted EvidenceChunks.

This module only *orders* a paper's existing chunk IDs for claim extraction.
It never edits chunk text, offsets, pages, or provenance; never writes to the
database; and never calls a model or the network. Exact-source checks remain
the responsibility of ``ensure_processed_source`` and Claim Extraction V3.

Scoring is limited to the complete interior spans V3 could actually send for
a chunk (``is_complete_interior_span``), so a chunk cannot rank highly because
of text in fragments that V3 would discard.

Section labels are inferred heuristically from heading lines that survive
PyMuPDF text normalisation. They are approximate and are not persisted.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import math
import re
from typing import Iterable, Sequence

from app.services.claim_extraction_v3 import generate_evidence_spans, is_complete_interior_span

RETRIEVAL_VERSION = "evidence-retrieval-v1"
V3_SPANS_PER_CALL = 3  # select_evidence_spans default; the spans one V3 call receives.

# --- Section inference -----------------------------------------------------

_SECTION_WORDS: tuple[tuple[str, str], ...] = (
    ("abstract", r"abstract"),
    ("introduction", r"introduction"),
    ("related_work", r"related work|background|literature review|prior work|preliminaries"),
    ("method", r"methods?|methodology|approach|proposed (?:method|approach|model|framework|system)|model architecture|system (?:design|overview|architecture)|framework"),
    ("data", r"datasets?|data(?: collection| sets?)?|corpus"),
    ("experiments", r"experiments?|experimental (?:setup|settings?|design|results)|evaluation|implementation(?: details)?"),
    ("results", r"results?(?: and (?:discussion|analysis))?|findings|performance (?:evaluation|analysis)"),
    ("discussion", r"discussion"),
    ("limitations", r"limitations?|threats to validity|limitations and future work"),
    ("conclusion", r"conclusions?(?: and future work)?|concluding remarks|summary"),
    ("future_work", r"future (?:work|directions|research)|outlook|open (?:problems|challenges|questions)"),
    ("acknowledgments", r"acknowledge?ments?"),
    ("references", r"references|bibliography"),
    ("appendix", r"appendix(?: [a-z])?|supplementary material"),
    ("checklist", r"(?:neurips |icml |iclr )?paper checklist|reproducibility checklist|checklist"),
)
# Optional numbering such as "5", "5.", "5.1", "IV.", "A." precedes a heading word;
# the heading must be the whole (short) line.
_HEADING = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+)*|[IVX]+|[A-H])\.?\s+)?(?P<word>" + "|".join(f"(?:{p})" for _, p in _SECTION_WORDS) + r")\s*:?\s*$",
    re.IGNORECASE,
)
_SECTION_LOOKUP = tuple((name, re.compile(rf"^(?:{p})$", re.IGNORECASE)) for name, p in _SECTION_WORDS)

EXCLUDED_SECTIONS = frozenset({"references", "acknowledgments", "checklist"})
SECTION_PRIOR = {
    "results": 1.0, "discussion": 1.0, "limitations": 1.2, "conclusion": 0.9, "future_work": 1.0,
    "experiments": 0.6, "method": 0.4, "data": 0.5, "abstract": 0.2,
    "introduction": -0.4, "related_work": -0.6, "front_matter": -1.0,
    # Material after the reference list (appendices, supplements) is usable but
    # should not outrank the main text's own results and limitations.
    "appendix": -0.8, "back_matter": -0.8,
}
BACK_MATTER_KEEP = frozenset({"appendix", "checklist", "references", "acknowledgments"})


def _heading_section(line: str) -> str | None:
    if len(line) > 60:
        return None
    match = _HEADING.match(line)
    if match is None:
        return None
    word = match.group("word").strip()
    for name, pattern in _SECTION_LOOKUP:
        if pattern.match(word):
            return name
    return None


@dataclass(frozen=True)
class ChunkSections:
    start: str          # section in force at the first character of the chunk
    present: tuple[str, ...]  # start section plus any headings inside, in order
    headings: tuple[tuple[int, str], ...] = ()  # (chunk-relative offset, section) of each heading

    def at(self, offset: int) -> str:
        """Section in force at a chunk-relative character offset."""
        section = self.start
        for position, name in self.headings:
            if position > offset:
                break
            section = name
        return section


def infer_sections(chunks: Sequence) -> dict[str, ChunkSections]:
    """Carry heading-derived labels forward through one document in chunk order.

    ``chunks`` must belong to one source document. Text before the first
    recognised heading is ``front_matter``. Overlapping chunk text can repeat
    a heading; that only re-asserts the same label.
    """
    ordered = sorted(chunks, key=lambda c: (c.chunk_index, c.page_number, c.id))
    current = "front_matter"
    after_references = False
    result: dict[str, ChunkSections] = {}
    for chunk in ordered:
        start, present, headings = current, [current], []
        offset = 0
        for line in chunk.text.split("\n"):
            line_start, offset = offset, offset + len(line) + 1
            section = _heading_section(line)
            if section is not None:
                if section == "references":
                    after_references = True
                elif after_references and section not in BACK_MATTER_KEEP:
                    # A "Discussion" heading inside an appendix is still back matter.
                    section = "back_matter"
                current = section
                headings.append((line_start, section))
                if present[-1] != section:
                    present.append(section)
        result[chunk.id] = ChunkSections(start, tuple(present), tuple(headings))
    return result


# --- Lexical facets ---------------------------------------------------------

FACET_CUES: dict[str, re.Pattern] = {
    "result": re.compile(
        r"\b(?:outperform\w*|achiev\w*|improv\w*|accuracy|precision|recall|f1|auc|"
        r"reduc\w*|increas\w*|significant\w*|compared (?:to|with)|baselines?|state[- ]of[- ]the[- ]art|"
        r"\d+(?:\.\d+)?\s?%|\d+\.\d+)",
        re.IGNORECASE),
    "limitation": re.compile(
        r"\b(?:limitations?|limited|however|fail\w*|cannot|unable|does not|do not|did not|"
        r"not (?:yet|able|clear)|drawbacks?|shortcomings?|restrict\w*|remains? (?:open|unclear|unsolved|challenging)|"
        r"insufficient|lack(?:s|ing)?|bias(?:ed|es)?|threats?)\b",
        re.IGNORECASE),
    "future_work": re.compile(
        r"\b(?:future (?:work|research|studies|directions)|further (?:research|work|investigation|study|studies)|"
        r"we plan|plan to|open (?:questions?|problems?|challenges?)|remains? to be)\b",
        re.IGNORECASE),
    "method": re.compile(
        r"\b(?:we propose|proposed|our (?:method|approach|model|framework|system)|architecture|algorithm|"
        r"trained|training|fine-?tun\w*|we (?:use|apply|adopt|design|develop|introduce))\b",
        re.IGNORECASE),
    "dataset": re.compile(
        r"\b(?:datasets?|data sets?|corpus|corpora|benchmarks?|collected|samples|participants|annotated)\b",
        re.IGNORECASE),
}
_NOISE = re.compile(
    r"@|https?://|www\.|\bdoi\b|^(?:fig(?:ure)?|table)\s*\d|"
    r"^(?:acknowledge?ments?|references|bibliography)\b|"
    r"\banswer:\s*\[(?:yes|no|n/?a)\]|\bjustification:|\bguidelines:|the answer n/?a means",
    re.IGNORECASE)
_CITATION_BRACKET = re.compile(r"\[\d+(?:[,–-]\s*\d+)*\]")


def span_facets(text: str) -> frozenset[str]:
    return frozenset(name for name, pattern in FACET_CUES.items() if pattern.search(text))


# --- BM25 over a paper's chunks ---------------------------------------------

_STOPWORDS = frozenset("""
a an and are as at be by for from has have in into is it its of on or that the their this to was were will with
use using used based via towards toward study studies approach approaches role impact new
""".split())
_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens with light plural folding; stopwords removed."""
    tokens = []
    for token in _TOKEN.findall(text.lower().replace("-\n", "")):
        if token in _STOPWORDS or len(token) < 2:
            continue
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        tokens.append(token)
    return tokens


def bm25_scores(query: Iterable[str], documents: Sequence[list[str]], *, k1: float = 1.2, b: float = 0.75) -> list[float]:
    terms = list(dict.fromkeys(query))
    n = len(documents)
    if not n or not terms:
        return [0.0] * n
    avgdl = sum(len(d) for d in documents) / n or 1.0
    df = Counter(term for d in documents for term in set(d) if term in terms)
    scores = []
    for doc in documents:
        tf = Counter(doc)
        score = 0.0
        for term in terms:
            if not tf[term]:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(doc) / avgdl))
        scores.append(score)
    return scores


# --- Ranking -----------------------------------------------------------------

@dataclass(frozen=True)
class RankedChunk:
    chunk_id: str
    score: float
    section: str
    page_number: int
    facets: frozenset[str]
    topic_score: float
    span_texts: tuple[str, ...] = field(default=())


def candidate_spans(chunk, limit: int = V3_SPANS_PER_CALL) -> list:
    """The first complete interior spans in reading order, as V3 selects them
    (before V3's request-size admission, which only ever removes spans)."""
    try:
        spans = generate_evidence_spans(chunk)
    except ValueError:
        return []
    selected, seen = [], set()
    for index, span in enumerate(spans):
        if is_complete_interior_span(spans, index) and span.evidence_id not in seen:
            selected.append(span)
            seen.add(span.evidence_id)
            if len(selected) == limit:
                break
    return selected


def rank_chunks(chunks: Sequence, topic: str) -> list[RankedChunk]:
    """Order one document's chunks by expected value for V3 claim extraction.

    Score = normalised topic BM25 + distinct substantive facets in the spans V3
    would receive + a heuristic section prior - noise penalties. Chunks with no
    admissible span, or inside references/acknowledgments, are omitted.
    Ties fall back to reading order, so the result is deterministic.
    """
    if not chunks:
        return []
    sections = infer_sections(chunks)
    query = tokenize(topic)
    span_lists = {c.id: candidate_spans(c) for c in chunks}
    texts = {c.id: " ".join(s.text for s in span_lists[c.id]) for c in chunks}
    ordered = sorted(chunks, key=lambda c: (c.chunk_index, c.page_number, c.id))
    raw = bm25_scores(query, [tokenize(texts[c.id]) for c in ordered])
    top = max(raw) if raw and max(raw) > 0 else 1.0
    ranked = []
    for position, (chunk, topic_raw) in enumerate(zip(ordered, raw)):
        spans = span_lists[chunk.id]
        info = sections[chunk.id]
        # Sections are judged where the spans V3 would send actually sit, so a
        # chunk that starts in Results but whose spans follow a References
        # heading is not credited with Results.
        span_sections = [info.at(s.chunk_start) for s in spans]
        usable = [s for s in span_sections if s not in EXCLUDED_SECTIONS]
        if not spans or not usable:
            continue
        section = max(usable, key=lambda s: SECTION_PRIOR.get(s, 0.0))
        clean = [s for s, name in zip(spans, span_sections) if name not in EXCLUDED_SECTIONS]
        facets = frozenset().union(*(span_facets(s.text) for s in clean))
        substantive = len(facets & {"result", "limitation", "future_work", "dataset"}) + 0.5 * ("method" in facets)
        noise = (sum(bool(_NOISE.search(s.text)) for s in spans)
                 + 0.5 * sum(len(_CITATION_BRACKET.findall(s.text)) >= 2 for s in spans)
                 + (len(spans) - len(clean)))  # spans V3 would send from references/checklists
        topic_score = topic_raw / top
        score = topic_score + substantive + SECTION_PRIOR.get(section, 0.0) - noise
        ranked.append((-score, position, RankedChunk(chunk.id, round(score, 6), section, chunk.page_number, facets,
                                                     round(topic_score, 6), tuple(s.text for s in spans))))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ranked]
