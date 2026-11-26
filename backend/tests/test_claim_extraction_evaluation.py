import json
from pathlib import Path

import pytest

from app.models.research import EvidenceChunk, Paper, SourceDocument
from app.schemas.research import ClaimType
from app.services.claim_extraction import ClaimCandidate, ClaimExtractionResponse
from app.services.claim_extraction_evaluation import (
    EvaluationCase,
    ReferenceAnnotation,
    coverage_report,
    default_model_candidates,
    evaluate_cases,
    load_reference_annotations,
    validate_reference_provenance,
)
from app.services.claim_extraction_evaluation import NUMBER


def annotation(**changes):
    data = {
        "reference_id": "r1", "paper_id": "paper", "document_title": "Authentic local document", "source_document_id": "doc",
        "evidence_chunk_id": "chunk", "pdf_page_number": 2, "chunk_start_offset": 100,
        "expected_claim_meaning": "An author-stated numerical result.", "claim_category": "result",
        "supporting_quotation": "95% accuracy", "chunk_quote_start_offset": 4,
        "chunk_quote_end_offset": 16,
        "review_status": "draft", "dataset_split": "development",
    }
    data.update(changes)
    return ReferenceAnnotation.model_validate(data)


def candidate(**changes):
    data = {
        "claim_statement": "Accuracy was 95%.", "claim_category": "result", "source_document_id": "doc",
        "evidence_chunk_id": "chunk", "pdf_page_number": 2, "supporting_quotation": "95% accuracy",
        "chunk_quote_start_offset": 4, "chunk_quote_end_offset": 16,
        "page_quote_start_offset": 104, "page_quote_end_offset": 116,
    }
    data.update(changes)
    return ClaimCandidate.model_validate(data)


def test_reference_fixture_is_manually_reviewable_and_strict() -> None:
    fixture = Path(__file__).parent / "fixtures" / "claim_extraction_reference_annotations.json"
    annotations = load_reference_annotations(fixture)
    assert len(annotations) == 27
    assert {ClaimType.RESULT, ClaimType.OBJECTIVE, ClaimType.LIMITATION, ClaimType.METHODOLOGY, ClaimType.FUTURE_DIRECTION}.issubset({item.claim_category for item in annotations if not item.expected_abstention})
    assert any(item.expected_abstention for item in annotations)
    coverage = coverage_report(annotations)
    assert coverage.total == 27 and coverage.draft == 27 and coverage.development == 20 and coverage.held_out == 7 and coverage.abstentions == 4


def test_objective_metrics_keep_distinct_failure_modes() -> None:
    good = EvaluationCase(annotation=annotation(), response=ClaimExtractionResponse(claims=[candidate()]), source_text="xxx 95% accuracy yyy")
    abstain = EvaluationCase(annotation=annotation(reference_id="none", expected_abstention=True, claim_category=None, supporting_quotation=None, chunk_quote_start_offset=None, chunk_quote_end_offset=None), response=ClaimExtractionResponse(), source_text="caption fragment")
    invalid_quote = EvaluationCase(annotation=annotation(reference_id="bad"), response=ClaimExtractionResponse(claims=[candidate(supporting_quotation="invented text")]), source_text="xxx 95% accuracy yyy")
    failure = EvaluationCase(annotation=annotation(reference_id="failed"), response=None, source_text="xxx 95% accuracy yyy", inference_failed=True)
    metrics = evaluate_cases([good, abstain, invalid_quote, failure])
    assert metrics.schema_validity == type(metrics.schema_validity)(3, 4)
    assert metrics.exact_quotation_validity == type(metrics.exact_quotation_validity)(1, 2)
    assert metrics.reference_claim_precision == type(metrics.reference_claim_precision)(1, 2)
    assert metrics.reference_claim_recall == type(metrics.reference_claim_recall)(1, 3)
    assert metrics.numerical_value_fidelity.value == 1.0
    assert metrics.correct_abstention_rate.value == 1.0
    assert metrics.inference_failure_rate.value == 0.25


def test_model_candidates_do_not_assume_qwen_quantization() -> None:
    llama, qwen = default_model_candidates()
    assert llama.ready_for_live_evaluation and llama.verified_quantization == "Q4_K_M"
    assert not qwen.ready_for_live_evaluation and qwen.model_tag == "qwen3:4b"


def test_numbers_preserve_grouped_quantities() -> None:
    assert NUMBER.findall("10,700 data points; accuracy 0.985512; F1 0.985514") == ["10,700", "0.985512", "0.985514"]


def test_v2_reference_dataset_preserves_partition_and_page_coordinates() -> None:
    fixture = Path(__file__).parent / "fixtures" / "claim_extraction_reference_annotations_phase_4b_3a_v2.json"
    annotations = load_reference_annotations(fixture)
    coverage = coverage_report(annotations)
    assert (coverage.total, coverage.development, coverage.held_out, coverage.draft) == (27, 20, 7, 27)
    assert all(annotation.expected_abstention or annotation.page_quote_start_offset == annotation.chunk_start_offset + annotation.chunk_quote_start_offset for annotation in annotations)
    dataset_size = next(annotation for annotation in annotations if annotation.reference_id == "covid-dataset-size")
    assert NUMBER.findall(dataset_size.supporting_quotation or "") == ["10,700"]


def test_provenance_validation_and_missing_evidence_are_explicit(session) -> None:
    paper = Paper(title="Source", authors=[], open_access_metadata={})
    document = SourceDocument(paper=paper, document_type="pdf")
    chunk = EvidenceChunk(source_document=document, page_number=2, chunk_index=0, start_offset=10, end_offset=22, text="xxx 95% accuracy yyy")
    session.add_all([paper, document, chunk]); session.commit()
    good = annotation(paper_id=paper.id, source_document_id=document.id, evidence_chunk_id=chunk.id, chunk_start_offset=10)
    assert validate_reference_provenance(session, [good]).valid_count == 1
    missing = good.model_copy(update={"reference_id": "missing", "evidence_chunk_id": "not-a-chunk"})
    report = validate_reference_provenance(session, [missing])
    assert report.valid_count == 0 and "missing document or chunk" in report.errors[0]


def test_duplicate_annotations_and_incorrect_numbers_are_not_accepted(tmp_path: Path) -> None:
    item = annotation().model_dump(mode="json")
    path = tmp_path / "duplicates.json"
    path.write_text(json.dumps([item, item]), encoding="utf-8")
    with pytest.raises(ValueError, match="identifiers must be unique"):
        load_reference_annotations(path)
    bad_number = EvaluationCase(annotation=annotation(), response=ClaimExtractionResponse(claims=[candidate(supporting_quotation="94% accuracy")]), source_text="xxx 95% accuracy yyy")
    metrics = evaluate_cases([bad_number])
    assert metrics.exact_quotation_validity.value == 0.0
    assert metrics.reference_claim_precision.value == 0.0
