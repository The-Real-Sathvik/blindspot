from sqlalchemy import select

from app.models.research import EvidenceChunk, ExtractedClaim, Paper, ResearchJob, SourceDocument
from app.schemas.research import ExtractionOutcomeStatus
from app.services.claim_extraction import (
    ClaimCandidateV2,
    ClaimExtractionResponseV2,
    ClaimExtractionService,
    EvidenceLocationValidator,
    PROMPT_TEMPLATE_VERSION,
    claim_extraction_prompt,
    claim_extraction_prompt_v1,
)
from app.services.model_config import AgentModelAssignment
from app.services.ollama_client import InferenceMetrics


class V2Runner:
    def __init__(self, response): self.response, self.calls, self.schema = response, 0, None
    def generate(self, _role, _prompt, schema):
        self.calls += 1; self.schema = schema
        return self.response, InferenceMetrics(0.1, 10, 100.0)


def setup_v2(session, text="The β-value was 0.95.\nThis method improved accuracy."):
    paper = Paper(title="V2 source", authors=[], open_access_metadata={})
    document = SourceDocument(paper=paper, document_type="pdf")
    chunk = EvidenceChunk(source_document=document, page_number=4, chunk_index=0, start_offset=200, end_offset=200 + len(text), text=text)
    job = ResearchJob(topic="V2 fixture")
    session.add_all([paper, document, chunk, job]); session.commit()
    return paper, document, chunk, job


def v2_candidate(chunk, **changes):
    value = {"claim_statement": "The method improved accuracy.", "claim_category": "result",
             "evidence_chunk_id": chunk.id, "supporting_quotation": "This method improved accuracy.",
             "uncertainty_or_missing_information": "The evaluation dataset is not named."}
    value.update(changes)
    return ClaimCandidateV2(**value)


def test_v2_derives_exact_chunk_and_page_offsets_with_unicode_and_linebreaks(session) -> None:
    paper, _, chunk, _ = setup_v2(session)
    candidate = v2_candidate(chunk, claim_statement="The β-value was 0.95.", supporting_quotation="β-value was 0.95.\nThis")
    valid, reason = EvidenceLocationValidator().validate_v2(session, paper.id, chunk, candidate)
    assert reason is None and valid is not None
    assert valid.quote_start_offset == 4
    assert valid.quote_end_offset == 26
    assert chunk.start_offset + valid.quote_start_offset == 204


def test_v2_rejects_missing_repeated_cross_chunk_and_wrong_identifier_quotes(session) -> None:
    paper, document, chunk, _ = setup_v2(session, "repeat quote. repeat quote.")
    next_chunk = EvidenceChunk(source_document=document, page_number=4, chunk_index=1, start_offset=len(chunk.text), end_offset=len(chunk.text) + 11, text="other-chunk")
    session.add(next_chunk); session.commit()
    validator = EvidenceLocationValidator()
    assert validator.validate_v2(session, paper.id, chunk, v2_candidate(chunk, supporting_quotation="missing"))[1] == "quotation_not_found_in_selected_chunk"
    assert validator.validate_v2(session, paper.id, chunk, v2_candidate(chunk, supporting_quotation="repeat quote."))[1] == "ambiguous_repeated_quotation"
    assert validator.validate_v2(session, paper.id, chunk, v2_candidate(chunk, supporting_quotation="quote. other-chunk"))[1] == "quotation_not_found_in_selected_chunk"
    assert validator.validate_v2(session, paper.id, chunk, v2_candidate(chunk, evidence_chunk_id="other"))[1] == "unexpected_evidence_chunk_identifier"


def test_v2_prompt_contract_and_persistence_idempotency(session) -> None:
    paper, _, chunk, job = setup_v2(session)
    prompt = claim_extraction_prompt(chunk)
    assert "Do not calculate or return character offsets" in prompt and chunk.id in prompt
    assert "Offsets are zero-based" in claim_extraction_prompt_v1(chunk)
    runner = V2Runner(ClaimExtractionResponseV2(claims=[v2_candidate(chunk)]))
    service = ClaimExtractionService(runner)
    first = service.process_chunk(session, research_job=job, paper_id=paper.id, chunk=chunk, assignment=AgentModelAssignment())
    session.commit()
    claim = session.scalar(select(ExtractedClaim).where(ExtractedClaim.model_run_id == first.model_run_id))
    assert first.outcome is ExtractionOutcomeStatus.CLAIMS_EXTRACTED
    assert runner.schema is ClaimExtractionResponseV2
    assert claim and claim.evidence_links[0].quote_start_offset == chunk.text.index("This method")
    repeat = service.process_chunk(session, research_job=job, paper_id=paper.id, chunk=chunk, assignment=AgentModelAssignment())
    assert repeat.reused and runner.calls == 1


def test_v2_no_claim_abstention_and_legacy_records_remain_distinct(session) -> None:
    paper, _, chunk, job = setup_v2(session)
    result = ClaimExtractionService(V2Runner(ClaimExtractionResponseV2())).process_chunk(
        session, research_job=job, paper_id=paper.id, chunk=chunk, assignment=AgentModelAssignment()
    )
    assert result.outcome is ExtractionOutcomeStatus.NO_CLAIMS
    assert PROMPT_TEMPLATE_VERSION == "claim-extraction-v2"
