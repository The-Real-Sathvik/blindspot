import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text

from app.models.research import CandidateGap, ClaimEvidence, EvidenceChunk, ExtractedClaim, ModelRun, Paper, ResearchJob, ResearchJobPaper, SourceDocument, VerificationResult
from app.schemas.research import AgentRole, CitationCheckStatus, ClaimType, SemanticSupportStatus
from app.services.gap_detection import GapResponse, prepare_packet, run_gap_detection
from app.services.model_config import AgentModelAssignment, BlindspotModelConfig, InferenceProvider
from app.services.ollama_client import InferenceMetrics
from app.services.model_runs import ModelRunRecorder
from app.services.human_semantic_review import HumanReviewRequest, VERSION, review_envelope


@pytest.fixture(autouse=True)
def synthetic_pdf_guard(monkeypatch):
    # These unit fixtures have no PDF; the golden-path integration test uses the real guard.
    monkeypatch.setattr('app.services.gap_detection.ensure_processed_source', lambda *_args: None)


class FakeRunner:
    def __init__(self, responses): self.responses, self.calls, self.prompt = responses, 0, None
    def generate(self, role, prompt, schema):
        self.calls += 1; self.prompt = prompt
        assert role == AgentRole.GAP_DETECTION
        return schema(gaps=self.responses), InferenceMetrics(0.1, 10, None)


def setup_records(session, statuses=(SemanticSupportStatus.SUPPORTED, SemanticSupportStatus.SUPPORTED)):
    job = ResearchJob(topic="Fixture job")
    run = ModelRunRecorder().start(session,research_job=job,role=AgentRole.CLAIM_EXTRACTION,assignment=AgentModelAssignment(),input_reference='SYNTHETIC TEST ONLY')
    review_targets=[]
    for index, status in enumerate(statuses):
        paper = Paper(title=f"Paper {index}", authors=[], open_access_metadata={})
        doc = SourceDocument(paper=paper, document_type="pdf")
        source = f"Method {index} was evaluated."
        chunk = EvidenceChunk(source_document=doc, page_number=index + 1, chunk_index=0, start_offset=0, end_offset=len(source), text=source)
        claim = ExtractedClaim(paper=paper, model_run=run, claim_type=ClaimType.RESULT, claim_text=f"Method {index} was evaluated.")
        evidence = ClaimEvidence(claim=claim, evidence_chunk=chunk, quotation_text=source, quote_start_offset=0, quote_end_offset=len(source), quotation_status=CitationCheckStatus.VALID, source_location_status=CitationCheckStatus.VALID)
        session.add_all([ResearchJobPaper(research_job=job, paper=paper), evidence])
        review_targets.append((claim,status))
    session.flush()
    for claim,status in review_targets:
        body=HumanReviewRequest(semantic_status=status, reviewer='SYNTHETIC TEST ONLY', rationale='Synthetic fixture; not a real scientific review.', reviewed_evidence=True)
        session.add(VerificationResult(claim=claim, semantic_status=status, rationale=json.dumps(review_envelope(claim,body)), verifier_version=VERSION))
    session.commit()
    job_id = job.id
    allowed = frozenset(session.scalars(select(EvidenceChunk.id)).all())
    packet = prepare_packet(session, job_id, allowed_chunk_ids=allowed)
    session.commit()
    return job_id, allowed, packet


def candidate(packet, **changes):
    refs = [{"paper_id": p["paper_id"], "claim_id": p["claim_id"], "evidence_id": p["evidence_id"], "role": "supporting"} for p in packet if p["verification"] == "supported"][:2]
    value = {"statement": "A direct comparison may be missing from these papers.", "category": "underexplored_comparison", "references": refs,
             "explanation": "The records concern different methods.", "counterevidence": "Other studies were not searched.",
             "uncertainty": "The available evidence is limited.", "validation_status": "not_yet_validated"}
    value.update(changes)
    return value


def invoke(session, job_id, allowed, runner):
    return run_gap_detection(session, job_id=job_id, allowed_chunk_ids=allowed, assignment=BlindspotModelConfig().gap_detection, runner=runner)


def test_valid_gap_persistence_provenance_and_repeat(session):
    job, allowed, packet = setup_records(session)
    runner = FakeRunner([candidate(packet)])
    result = invoke(session, job, allowed, runner); session.commit()
    gap = session.scalar(select(CandidateGap))
    assert result.outcome == "candidates" and gap.validation_status == "not_yet_validated"
    assert len(gap.references) == 2 and len({r.paper_id for r in gap.references}) == 2
    run = session.get(ModelRun, result.model_run_id)
    assert run.model_config["provider"] == "groq" and run.prompt_template_version == "gap-detection-v1"
    assert '"verification":"supported"' in runner.prompt
    session.commit()
    assert invoke(session, job, allowed, runner).reused and runner.calls == 1


def test_invented_and_duplicate_references_and_novelty_rejected(session):
    job, allowed, packet = setup_records(session)
    good = candidate(packet)
    bad = candidate(packet, statement="Different statement", references=[good["references"][0], {**good["references"][1], "claim_id": "invented"}])
    novel = candidate(packet, statement="This is the first ever study of the topic.")
    result = invoke(session, job, allowed, FakeRunner([good, good, bad, novel])); session.commit()
    assert (result.accepted_count, result.rejected_count) == (1, 3)
    assert set(result.rejection_codes) == {"duplicate_candidate", "invalid_reference", "unsupported_novelty_assertion"}


def test_single_paper_and_unverified_claims_abstain_before_inference(session):
    job, allowed, packet = setup_records(session, (SemanticSupportStatus.SUPPORTED, SemanticSupportStatus.INSUFFICIENT_EVIDENCE))
    runner = FakeRunner([])
    result = invoke(session, job, allowed, runner)
    assert result.outcome == "insufficient_evidence" and runner.calls == 0


def test_legacy_and_stale_reviews_cannot_support_a_gap(session):
    job, allowed, packet = setup_records(session)
    claims=session.scalars(select(ExtractedClaim).order_by(ExtractedClaim.id)).all()
    claims[0].verification_result.verifier_version=None
    claims[1].claim_text='Changed since the synthetic review.'
    session.flush()
    assert prepare_packet(session,job,allowed_chunk_ids=allowed)==[]


def test_claims_from_another_job_are_not_gap_inputs(session):
    job, allowed, packet=setup_records(session)
    other=ResearchJob(topic='SYNTHETIC OTHER JOB')
    for paper in session.scalars(select(Paper)).all(): session.add(ResearchJobPaper(research_job=other,paper=paper))
    session.commit()
    assert prepare_packet(session,other.id,allowed_chunk_ids=allowed)==[]


def test_contradiction_context_not_treated_as_supported(session):
    job, allowed, packet = setup_records(session, (SemanticSupportStatus.SUPPORTED, SemanticSupportStatus.SUPPORTED, SemanticSupportStatus.CONTRADICTED))
    contradicted = next(p for p in packet if p["verification"] == "contradicted")
    bad_refs = candidate(packet)["references"] + [{"paper_id": contradicted["paper_id"], "claim_id": contradicted["claim_id"], "evidence_id": contradicted["evidence_id"], "role": "supporting"}]
    runner = FakeRunner([candidate(packet, references=bad_refs)])
    result = invoke(session, job, allowed, runner); session.commit()
    assert result.rejection_codes == ("reference_not_supported",)
    assert '"verification":"contradicted"' in runner.prompt


def test_contradicted_reference_can_be_cited_as_counterevidence(session):
    job, allowed, packet = setup_records(session, (SemanticSupportStatus.SUPPORTED, SemanticSupportStatus.SUPPORTED, SemanticSupportStatus.CONTRADICTED))
    conflict = next(p for p in packet if p["verification"] == "contradicted")
    refs = candidate(packet)["references"] + [{"paper_id": conflict["paper_id"], "claim_id": conflict["claim_id"], "evidence_id": conflict["evidence_id"], "role": "counterevidence"}]
    result = invoke(session, job, allowed, FakeRunner([candidate(packet, references=refs)])); session.commit()
    assert result.outcome == "candidates"
    assert {ref.role for ref in session.scalar(select(CandidateGap)).references} == {"supporting", "counterevidence"}


def test_abstention_and_provider_assignment(session):
    job, allowed, packet = setup_records(session)
    result = invoke(session, job, allowed, FakeRunner([])); session.commit()
    assert result.outcome == "abstained" and not session.scalars(select(CandidateGap)).all()
    assert BlindspotModelConfig().gap_detection.provider == InferenceProvider.GROQ
    assert AgentModelAssignment(provider=InferenceProvider.GEMINI, model_tag="gemini-3.5-flash-lite").provider == InferenceProvider.GEMINI


def test_migration_upgrade_downgrade_preserves_prior_rows(tmp_path):
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    db_path = tmp_path / "migration.db"
    config.set_main_option("sqlalchemy.url", "sqlite:///" + db_path.as_posix())
    command.upgrade(config, "20261124_0007")
    engine = create_engine("sqlite:///" + db_path.as_posix())
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO research_jobs (id, topic, status) VALUES ('saved', 'existing', 'PENDING')"))
    command.upgrade(config, "20261125_0008")
    assert "candidate_gaps" in inspect(engine).get_table_names()
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT topic FROM research_jobs WHERE id='saved'")) == "existing"
    command.downgrade(config, "20261124_0007")
    assert "candidate_gaps" not in inspect(engine).get_table_names()
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT topic FROM research_jobs WHERE id='saved'")) == "existing"
    engine.dispose()
