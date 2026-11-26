from pydantic import ValidationError
from sqlalchemy import select

from app.models.research import EvidenceChunk, ExtractedClaim, Paper, ResearchJob, SourceDocument
from app.schemas.research import AgentRole, ExtractionOutcomeStatus, ModelRunStatus
from app.services.claim_extraction import ClaimExtractionResponse, ClaimExtractionService, EvidenceLocationValidator, claim_extraction_prompt
from app.services.model_config import AgentModelAssignment
from app.services.ollama_client import InferenceMetrics


class FakeRunner:
    def __init__(self, response): self.response, self.calls = response, 0
    def generate(self, *_args): self.calls += 1; return self.response, InferenceMetrics(0.1, 10, 100.0)


def setup_chunk(session):
    paper = Paper(title="Claim fixture", authors=[], open_access_metadata={})
    document = SourceDocument(paper=paper, document_type="pdf")
    chunk = EvidenceChunk(source_document=document, page_number=3, chunk_index=0, start_offset=100, end_offset=153, text="The method achieved 95% accuracy on 100 test samples.")
    job = ResearchJob(topic="Fixture")
    session.add_all([paper, document, chunk, job]); session.commit()
    return paper, document, chunk, job


def candidate(document, chunk, **changes):
    value = {
        "claim_statement": "The method achieved 95% accuracy on 100 test samples.",
        "claim_category": "result", "source_document_id": document.id,
        "evidence_chunk_id": chunk.id, "pdf_page_number": 3,
        "supporting_quotation": "95% accuracy on 100 test samples",
        "chunk_quote_start_offset": 20, "chunk_quote_end_offset": 52,
        "page_quote_start_offset": 120, "page_quote_end_offset": 152,
        "uncertainty_or_missing_information": "Dataset identity is not stated in this chunk.",
    }
    value.update(changes); return value


def test_prompt_and_deterministic_location_validation(session) -> None:
    paper, document, chunk, _ = setup_chunk(session)
    prompt = claim_extraction_prompt(chunk)
    assert chunk.id in prompt and "untrusted source passage" in prompt
    response = ClaimExtractionResponse(claims=[candidate(document, chunk)])
    valid, reason = EvidenceLocationValidator().validate(session, paper.id, response.claims[0])
    assert valid and reason is None
    invalid = response.claims[0].model_copy(update={"pdf_page_number": 4})
    assert EvidenceLocationValidator().validate(session, paper.id, invalid)[1] == "pdf_page_mismatch"


def test_valid_no_claim_partial_and_idempotent_persistence(session) -> None:
    paper, document, chunk, job = setup_chunk(session)
    valid_response = ClaimExtractionResponse(claims=[candidate(document, chunk)])
    runner = FakeRunner(valid_response)
    service = ClaimExtractionService(runner)
    assignment = AgentModelAssignment()
    first = service.process_chunk(session, research_job=job, paper_id=paper.id, chunk=chunk, assignment=assignment); session.commit()
    assert first.outcome is ExtractionOutcomeStatus.CLAIMS_EXTRACTED and first.accepted_count == 1
    claim = session.scalar(select(ExtractedClaim).where(ExtractedClaim.model_run_id == first.model_run_id))
    assert claim and claim.uncertainty_or_missing_information and claim.verification_result is None
    repeat = service.process_chunk(session, research_job=job, paper_id=paper.id, chunk=chunk, assignment=assignment)
    assert repeat.reused and runner.calls == 1

    no_claim = ClaimExtractionService(FakeRunner(ClaimExtractionResponse()))
    other = EvidenceChunk(source_document=document, page_number=4, chunk_index=1, start_offset=0, end_offset=10, text="Background only.")
    session.add(other); session.commit()
    abstention = no_claim.process_chunk(session, research_job=job, paper_id=paper.id, chunk=other, assignment=assignment)
    assert abstention.outcome is ExtractionOutcomeStatus.NO_CLAIMS

    bad = candidate(document, chunk, supporting_quotation="invented text", chunk_quote_start_offset=0, chunk_quote_end_offset=13, page_quote_start_offset=100, page_quote_end_offset=113)
    partial = ClaimExtractionService(FakeRunner(ClaimExtractionResponse(claims=[candidate(document, chunk), bad]))).process_chunk(session, research_job=job, paper_id=paper.id, chunk=chunk, assignment=AgentModelAssignment(prompt_template_version="v2"))
    session.commit()
    assert partial.outcome is ExtractionOutcomeStatus.PARTIAL_ERROR and partial.accepted_count == 1 and partial.rejected_count == 1


def test_failed_inference_preserves_failed_outcome(session) -> None:
    paper, _, chunk, job = setup_chunk(session)
    class FailingRunner:
        def generate(self, *_args): raise RuntimeError("mock failure")
    result = ClaimExtractionService(FailingRunner()).process_chunk(session, research_job=job, paper_id=paper.id, chunk=chunk, assignment=AgentModelAssignment())
    assert result.outcome is ExtractionOutcomeStatus.FAILED_INFERENCE


def test_strict_schema_rejects_incomplete_or_unexpected_candidates() -> None:
    incomplete = {"claims": [{"claim_statement": "A stated result."}]}
    unexpected = {"claims": [], "raw_response": "must not persist"}
    for payload in (incomplete, unexpected):
        try:
            ClaimExtractionResponse.model_validate(payload)
        except ValidationError:
            pass
        else:
            raise AssertionError("strict extraction schema accepted invalid model output")


def test_contradictory_passage_remains_unverified_evidence(session) -> None:
    paper, document, _, job = setup_chunk(session)
    text = "Method A improved accuracy. However, Method A reduced accuracy."
    chunk = EvidenceChunk(source_document=document, page_number=5, chunk_index=2, start_offset=0, end_offset=len(text), text=text)
    session.add(chunk); session.commit()
    first_quote, second_quote = "Method A improved accuracy.", "Method A reduced accuracy."
    response = ClaimExtractionResponse(claims=[
        candidate(document, chunk, claim_statement=first_quote, supporting_quotation=first_quote,
                  chunk_quote_start_offset=0, chunk_quote_end_offset=len(first_quote),
                  page_quote_start_offset=0, page_quote_end_offset=len(first_quote), pdf_page_number=5),
        candidate(document, chunk, claim_statement=second_quote, supporting_quotation=second_quote,
                  chunk_quote_start_offset=text.index(second_quote), chunk_quote_end_offset=text.index(second_quote) + len(second_quote),
                  page_quote_start_offset=text.index(second_quote), page_quote_end_offset=text.index(second_quote) + len(second_quote), pdf_page_number=5),
    ])
    result = ClaimExtractionService(FakeRunner(response)).process_chunk(
        session, research_job=job, paper_id=paper.id, chunk=chunk, assignment=AgentModelAssignment()
    )
    session.commit()
    claims = session.scalars(select(ExtractedClaim).where(ExtractedClaim.model_run_id == result.model_run_id)).all()
    assert result.outcome is ExtractionOutcomeStatus.CLAIMS_EXTRACTED
    assert len(claims) == 2 and all(claim.verification_result is None for claim in claims)
