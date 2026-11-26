"""Deterministic, reference-annotation evaluation for claim extraction.

This module scores structural and provenance facts only.  It deliberately does
not infer whether a model's paraphrase is scientifically faithful.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import re
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.models.research import EvidenceChunk, SourceDocument
from app.schemas.research import ClaimType
from app.services.claim_extraction import ClaimCandidate, ClaimExtractionResponse
from app.services.ollama_benchmark import available_memory_mb


NUMBER = re.compile(r"(?<![\w.])-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?![\w.])")
MIN_AVAILABLE_MEMORY_MB = 6000.0


class ReferenceAnnotation(BaseModel):
    """A human-reviewed claim instance; source text is deliberately not a prompt."""
    model_config = ConfigDict(extra="forbid")
    reference_id: str = Field(min_length=1)
    paper_id: str = Field(min_length=1)
    document_title: str = Field(min_length=1)
    source_document_id: str = Field(min_length=1)
    evidence_chunk_id: str = Field(min_length=1)
    pdf_page_number: int = Field(ge=1)
    chunk_start_offset: int = Field(ge=0)
    expected_claim_meaning: str = Field(min_length=1)
    claim_category: ClaimType | None = None
    supporting_quotation: str | None = None
    chunk_quote_start_offset: int | None = Field(default=None, ge=0)
    chunk_quote_end_offset: int | None = Field(default=None, ge=0)
    page_quote_start_offset: int | None = Field(default=None, ge=0)
    page_quote_end_offset: int | None = Field(default=None, ge=0)
    uncertainty_or_missing_information: str | None = None
    expected_abstention: bool = False
    coverage_note: str | None = None
    review_status: "AnnotationReviewStatus"
    dataset_split: "DatasetSplit"
    review_note: str | None = None

    @model_validator(mode="after")
    def require_complete_location_or_abstention(self) -> "ReferenceAnnotation":
        if self.expected_abstention:
            if self.claim_category is not None or self.supporting_quotation is not None:
                raise ValueError("an abstention annotation must not contain a reference claim")
            return self
        if None in (self.claim_category, self.supporting_quotation, self.chunk_quote_start_offset, self.chunk_quote_end_offset):
            raise ValueError("a reference claim requires category, quotation, and offsets")
        if self.page_quote_start_offset is not None or self.page_quote_end_offset is not None:
            expected_start = self.chunk_start_offset + self.chunk_quote_start_offset
            expected_end = self.chunk_start_offset + self.chunk_quote_end_offset
            if (self.page_quote_start_offset, self.page_quote_end_offset) != (expected_start, expected_end):
                raise ValueError("page-relative offsets must equal chunk_start_offset plus chunk-relative offsets")
        return self


class AnnotationReviewStatus(str, Enum):
    DRAFT = "draft"
    HUMAN_REVIEWED = "human_reviewed"


class DatasetSplit(str, Enum):
    DEVELOPMENT = "development"
    HELD_OUT = "held_out"


class EvaluationModelCandidate(BaseModel):
    """Model candidate metadata; an unverified quantization cannot be evaluated."""
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1)
    model_tag: str = Field(min_length=1)
    verified_quantization: str | None = None
    output_tokens: int = Field(default=256, ge=1, le=512)

    @property
    def ready_for_live_evaluation(self) -> bool:
        return self.verified_quantization is not None


def default_model_candidates() -> tuple[EvaluationModelCandidate, EvaluationModelCandidate]:
    return (
        EvaluationModelCandidate(label="Llama 3.1 8B baseline", model_tag="llama3.1:8b-instruct-q4_K_M", verified_quantization="Q4_K_M"),
        EvaluationModelCandidate(label="Qwen3 4B candidate", model_tag="qwen3:4b"),
    )


@dataclass(frozen=True)
class Metric:
    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        return round(self.numerator / self.denominator, 4) if self.denominator else None


@dataclass(frozen=True)
class EvaluationMetrics:
    schema_validity: Metric
    exact_quotation_validity: Metric
    location_correctness: Metric
    reference_claim_precision: Metric
    reference_claim_recall: Metric
    numerical_value_fidelity: Metric
    claim_category_accuracy: Metric
    unsupported_claim_rate: Metric
    correct_abstention_rate: Metric
    inference_failure_rate: Metric


@dataclass(frozen=True)
class EvaluationCase:
    annotation: ReferenceAnnotation
    response: ClaimExtractionResponse | None
    source_text: str
    inference_failed: bool = False


@dataclass(frozen=True)
class MemorySafetyResult:
    available_memory_mb: float
    required_memory_mb: float
    safe_to_infer: bool
    reason: str


@dataclass(frozen=True)
class CoverageReport:
    total: int
    development: int
    held_out: int
    draft: int
    human_reviewed: int
    abstentions: int
    by_category: dict[str, int]


@dataclass(frozen=True)
class ProvenanceValidationReport:
    valid_count: int
    errors: tuple[str, ...]


def load_reference_annotations(path: Path) -> list[ReferenceAnnotation]:
    values = json.loads(path.read_text(encoding="utf-8"))
    annotations = [ReferenceAnnotation.model_validate(value) for value in values]
    ids = [annotation.reference_id for annotation in annotations]
    if len(ids) != len(set(ids)):
        raise ValueError("reference annotation identifiers must be unique")
    locations = [(annotation.evidence_chunk_id, annotation.chunk_quote_start_offset, annotation.chunk_quote_end_offset,
                  annotation.claim_category, annotation.expected_abstention) for annotation in annotations]
    if len(locations) != len(set(locations)):
        raise ValueError("reference annotations must not duplicate the same labelled location")
    return annotations


def coverage_report(annotations: Iterable[ReferenceAnnotation]) -> CoverageReport:
    annotations = list(annotations)
    categories = Counter(annotation.claim_category.value for annotation in annotations if annotation.claim_category)
    return CoverageReport(
        total=len(annotations),
        development=sum(annotation.dataset_split is DatasetSplit.DEVELOPMENT for annotation in annotations),
        held_out=sum(annotation.dataset_split is DatasetSplit.HELD_OUT for annotation in annotations),
        draft=sum(annotation.review_status is AnnotationReviewStatus.DRAFT for annotation in annotations),
        human_reviewed=sum(annotation.review_status is AnnotationReviewStatus.HUMAN_REVIEWED for annotation in annotations),
        abstentions=sum(annotation.expected_abstention for annotation in annotations),
        by_category=dict(sorted(categories.items())),
    )


def validate_reference_provenance(session: Session, annotations: Iterable[ReferenceAnnotation]) -> ProvenanceValidationReport:
    errors: list[str] = []; valid_count = 0
    for annotation in annotations:
        chunk = session.get(EvidenceChunk, annotation.evidence_chunk_id)
        document = session.get(SourceDocument, annotation.source_document_id)
        if document is None or chunk is None:
            errors.append(f"{annotation.reference_id}: missing document or chunk"); continue
        if document.paper_id != annotation.paper_id or chunk.source_document_id != document.id:
            errors.append(f"{annotation.reference_id}: paper/document/chunk association mismatch"); continue
        if chunk.page_number != annotation.pdf_page_number or chunk.start_offset != annotation.chunk_start_offset:
            errors.append(f"{annotation.reference_id}: page or chunk start mismatch"); continue
        if not annotation.expected_abstention:
            start, end = annotation.chunk_quote_start_offset, annotation.chunk_quote_end_offset
            if chunk.text[start:end] != annotation.supporting_quotation:
                errors.append(f"{annotation.reference_id}: quotation or offsets mismatch"); continue
            if annotation.page_quote_start_offset is not None and (annotation.page_quote_start_offset, annotation.page_quote_end_offset) != (chunk.start_offset + start, chunk.start_offset + end):
                errors.append(f"{annotation.reference_id}: page-relative offsets mismatch"); continue
        valid_count += 1
    return ProvenanceValidationReport(valid_count, tuple(errors))


def check_memory_safety(minimum_available_mb: float = MIN_AVAILABLE_MEMORY_MB) -> MemorySafetyResult:
    available = available_memory_mb()
    safe = available >= minimum_available_mb
    return MemorySafetyResult(available, minimum_available_mb, safe,
        "sufficient available physical memory" if safe else "available physical memory is below the configured safety floor")


def _candidate_has_exact_quote(candidate: ClaimCandidate, source_text: str) -> bool:
    return (0 <= candidate.chunk_quote_start_offset <= candidate.chunk_quote_end_offset <= len(source_text)
            and source_text[candidate.chunk_quote_start_offset:candidate.chunk_quote_end_offset] == candidate.supporting_quotation)


def _matches_reference(candidate: ClaimCandidate, annotation: ReferenceAnnotation) -> bool:
    return (not annotation.expected_abstention
            and candidate.source_document_id == annotation.source_document_id
            and candidate.evidence_chunk_id == annotation.evidence_chunk_id
            and candidate.pdf_page_number == annotation.pdf_page_number
            and candidate.supporting_quotation == annotation.supporting_quotation
            and candidate.chunk_quote_start_offset == annotation.chunk_quote_start_offset
            and candidate.chunk_quote_end_offset == annotation.chunk_quote_end_offset
            and candidate.page_quote_start_offset == annotation.chunk_start_offset + (annotation.chunk_quote_start_offset or 0)
            and candidate.page_quote_end_offset == annotation.chunk_start_offset + (annotation.chunk_quote_end_offset or 0))


def evaluate_cases(cases: Iterable[EvaluationCase]) -> EvaluationMetrics:
    """Aggregate objective checks; human review owns meaning and nuanced completeness."""
    cases = list(cases)
    outputs = [candidate for case in cases if case.response for candidate in case.response.claims]
    quote_valid = sum(_candidate_has_exact_quote(candidate, case.source_text)
                      for case in cases if case.response for candidate in case.response.claims)
    location_valid = sum(_matches_reference(candidate, case.annotation)
                         for case in cases if case.response for candidate in case.response.claims)
    matched = [(candidate, case.annotation) for case in cases if case.response for candidate in case.response.claims
               if _matches_reference(candidate, case.annotation)]
    expected_claims = [case.annotation for case in cases if not case.annotation.expected_abstention]
    category_correct = sum(candidate.claim_category == annotation.claim_category for candidate, annotation in matched)
    numeric_matches = [(candidate, annotation) for candidate, annotation in matched
                       if NUMBER.search(annotation.supporting_quotation or "")]
    numeric_correct = sum(NUMBER.findall(candidate.supporting_quotation) == NUMBER.findall(annotation.supporting_quotation or "")
                          for candidate, annotation in numeric_matches)
    abstentions = [case for case in cases if case.annotation.expected_abstention]
    correct_abstentions = sum(case.response is not None and not case.response.claims for case in abstentions)
    schema_valid = sum(case.response is not None for case in cases)
    failures = sum(case.inference_failed for case in cases)
    return EvaluationMetrics(
        schema_validity=Metric(schema_valid, len(cases)),
        exact_quotation_validity=Metric(quote_valid, len(outputs)),
        location_correctness=Metric(location_valid, len(outputs)),
        reference_claim_precision=Metric(len(matched), len(outputs)),
        reference_claim_recall=Metric(len({annotation.reference_id for _, annotation in matched}), len(expected_claims)),
        numerical_value_fidelity=Metric(numeric_correct, len(numeric_matches)),
        claim_category_accuracy=Metric(category_correct, len(matched)),
        unsupported_claim_rate=Metric(len(outputs) - len(matched), len(outputs)),
        correct_abstention_rate=Metric(correct_abstentions, len(abstentions)),
        inference_failure_rate=Metric(failures, len(cases)),
    )
