from sqlalchemy import select

from app.models.research import ClaimEvidence, EvidenceChunk, ExtractedClaim, ModelRun, Paper, ResearchJob, ResearchJobPaper, SourceDocument
from app.schemas.research import ExtractionOutcomeStatus
from app.services.claim_extraction_v3 import ClaimCandidateV3, ClaimExtractionResponseV3, PROMPT_VERSION, generate_evidence_spans, run_development_extraction
from app.services.model_config import AgentModelAssignment
from app.services.ollama_client import InferenceMetrics


class Runner:
    def __init__(self, candidates): self.candidates, self.calls, self.prompt = candidates, 0, None
    def generate(self, role, prompt, schema):
        self.calls += 1; self.prompt = prompt
        return schema(claims=self.candidates), InferenceMetrics(0.1, 12, None)


def setup(session, text="First result was 10,700. Second result was 95%. Incomplete"):
    paper = Paper(title="V3 fixture", authors=[], open_access_metadata={})
    document = SourceDocument(paper=paper, document_type="pdf")
    chunk = EvidenceChunk(source_document=document, page_number=3, chunk_index=0, start_offset=100, end_offset=100 + len(text), text=text)
    job = ResearchJob(topic="V3 test")
    session.add_all([chunk, ResearchJobPaper(research_job=job, paper=paper)]); session.commit()
    spans = generate_evidence_spans(chunk)
    ids = (job.id, chunk.id, paper.id)
    return ids, spans


def candidate(ids, statement="A result was 10,700."):
    return ClaimCandidateV3(claim_statement=statement, claim_category="result", evidence_ids=ids, uncertainty_or_missing_information="Fixture")


def invoke(session, ids, runner):
    return run_development_extraction(session, job_id=ids[0], chunk_id=ids[1], development_chunk_ids=frozenset([ids[1]]), assignment=AgentModelAssignment(), runner=runner)


def test_spans_stable_exact_page_offsets_and_edge_uncertainty(session):
    ids, spans = setup(session)
    chunk = session.get(EvidenceChunk, ids[1])
    assert spans == generate_evidence_spans(chunk)
    assert len(spans) == 3 and spans[0].boundary_uncertain and not spans[1].boundary_uncertain and spans[-1].boundary_uncertain
    for span in spans:
        assert span.text == chunk.text[span.chunk_start:span.chunk_end]
        assert (span.page_start, span.page_end) == (100 + span.chunk_start, 100 + span.chunk_end)
        assert span.page_number == 3 and span.source_document_id == chunk.source_document_id


def test_multiple_spans_provenance_idempotency_and_semantic_nonverification(session):
    ids, spans = setup(session)
    # Deliberately misleading: location validation must not claim semantic support.
    runner = Runner([candidate([spans[0].evidence_id, spans[1].evidence_id], "The experiment proves every method is perfect.")])
    result = invoke(session, ids, runner); session.commit()
    claim = session.scalar(select(ExtractedClaim).where(ExtractedClaim.model_run_id == result.model_run_id))
    assert result.outcome == "claims_extracted" and len(claim.evidence_links) == 2
    assert claim.verification_result is None and claim.model_run.prompt_template_version == PROMPT_VERSION
    assert all(e.quotation_text == spans[i].text for i, e in enumerate(claim.evidence_links))
    assert all(span.evidence_id in runner.prompt for span in spans)
    session.commit()
    again = invoke(session, ids, runner)
    assert again.reused and runner.calls == 1 and again.model_run_id == result.model_run_id


def test_invalid_and_duplicate_ids_and_candidates_are_audited(session):
    ids, spans = setup(session)
    good = candidate([spans[0].evidence_id])
    runner = Runner([good, good, candidate(["ev_invented"]), candidate([spans[1].evidence_id] * 2)])
    result = invoke(session, ids, runner); session.commit()
    assert (result.accepted_count, result.rejected_count) == (1, 3)
    assert result.rejection_codes == ("duplicate_candidate", "unknown_evidence_id", "duplicate_evidence_id")
    run = session.get(ModelRun, result.model_run_id)
    assert run.outcome_status is ExtractionOutcomeStatus.PARTIAL_ERROR and '"rejected":3' in run.error_summary
    assert len(session.scalars(select(ClaimEvidence)).all()) == 1


def test_abstention_and_heldout_rejection(session):
    import pytest
    ids, spans = setup(session)
    runner = Runner([])
    with pytest.raises(ValueError, match="unapproved"):
        run_development_extraction(session, job_id=ids[0], chunk_id=ids[1], development_chunk_ids=frozenset(), assignment=AgentModelAssignment(), runner=runner)
    session.rollback()
    result = invoke(session, ids, runner); session.commit()
    assert result.outcome == "no_claims" and not session.scalars(select(ExtractedClaim)).all()
    assert runner.calls == 1


def test_provider_failure_persisted_without_sensitive_message(session):
    ids, spans = setup(session)
    class Failing:
        def generate(self, *args): raise RuntimeError("private source and secret")
    result = invoke(session, ids, Failing()); session.commit()
    run = session.get(ModelRun, result.model_run_id)
    assert result.outcome == "failed_inference" and "secret" not in run.error_summary


def test_partial_run_retries_do_not_duplicate_accepted_claims(session):
    ids, spans = setup(session)
    runner = Runner([candidate([spans[0].evidence_id]), candidate(["ev_invented"])])
    first = invoke(session, ids, runner); session.commit()
    second = invoke(session, ids, runner); session.commit()
    assert first.outcome == "partial_error" and second.outcome == "partial_error" and runner.calls == 2
    assert "duplicate_persisted_claim" in second.rejection_codes
    assert len(session.scalars(select(ExtractedClaim)).all()) == 1
