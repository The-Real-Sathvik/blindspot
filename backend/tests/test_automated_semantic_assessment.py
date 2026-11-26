from types import SimpleNamespace

import pytest

from app.models.research import (AutomatedSemanticAssessment, ClaimEvidence, EvidenceChunk, ExtractedClaim, ModelRun,
    Paper, ResearchJob, ResearchJobPaper, SourceDocument, VerificationResult)
from app.schemas.research import AgentRole, CitationCheckStatus, ClaimType, ModelRunStatus, SemanticSupportStatus
from app.services.automated_semantic_assessment import AssessmentResponse, VERSION, assess
from app.services.automated_provisional_gaps import ProvisionalGapResponse, run as run_provisional_gap
from app.services.model_config import AgentModelAssignment
from app.services.ollama_client import InferenceMetrics


def fixture(session):
    job=ResearchJob(topic='Assessment fixture'); paper=Paper(title='Paper',authors=[],openalex_id='W-assess',open_access_metadata={})
    run=ModelRun(research_job=job,agent_role=AgentRole.CLAIM_EXTRACTION,model_tag='gemini-3.5-flash-lite',model_config={},prompt_template_version='claim-extraction-v3',status=ModelRunStatus.SUCCESS)
    doc=SourceDocument(paper=paper,document_type='pdf',status='available',content_hash='x')
    text='The measured result is 42.'
    chunk=EvidenceChunk(source_document=doc,page_number=1,chunk_index=0,start_offset=0,end_offset=len(text),text=text)
    claim=ExtractedClaim(paper=paper,model_run=run,claim_type=ClaimType.RESULT,claim_text='The measured result is 42.')
    evidence=ClaimEvidence(claim=claim,evidence_chunk=chunk,quotation_text=chunk.text,quote_start_offset=0,quote_end_offset=len(text),quotation_status=CitationCheckStatus.VALID,source_location_status=CitationCheckStatus.VALID)
    session.add_all([job,ResearchJobPaper(research_job=job,paper=paper,included=True),evidence]); session.commit()
    return job,claim


def assignment():
    return AgentModelAssignment(provider='gemini',model_tag='gemini-3.5-flash-lite',output_tokens=512,gemini_response_mode='prompt_json',prompt_template_version='temporary')


def test_automated_assessment_is_separate_and_idempotent(session, monkeypatch):
    job, claim=fixture(session)
    job_id, claim_id = job.id, claim.id
    monkeypatch.setattr('app.services.automated_semantic_assessment.ensure_processed_source',lambda *_: 'page')
    calls=[]
    class Runner:
        def generate(self,*args):
            calls.append(args[0]); return AssessmentResponse(assessment_status='supported',rationale='The exact quotation states the claimed value.',uncertainty=None),InferenceMetrics(0.1,12,120)
    result=assess(session,job_id=job_id,claim_id=claim_id,assignment=assignment(),runner=Runner())
    session.commit()
    assert result.outcome=='assessed' and calls==[AgentRole.SEMANTIC_REVIEW]
    assert claim.verification_result is None and len(claim.automated_assessments)==1
    session.rollback()
    second=assess(session,job_id=job_id,claim_id=claim_id,assignment=assignment(),runner=Runner())
    assert second.reused is True and len(calls)==1


def test_mismatched_current_evidence_blocks_assessment(session, monkeypatch):
    job, claim=fixture(session)
    monkeypatch.setattr('app.services.automated_semantic_assessment.ensure_processed_source',lambda *_: (_ for _ in ()).throw(ValueError('mismatch')))
    with pytest.raises(ValueError):
        assess(session,job_id=job.id,claim_id=claim.id,assignment=assignment(),runner=SimpleNamespace())
    assert claim.verification_result is None and not claim.automated_assessments


def test_provisional_gap_requires_two_current_supported_papers_and_persists(session, monkeypatch):
    job=ResearchJob(topic='Two paper gap')
    session.add(job); session.flush(); records=[]
    for index in range(2):
        paper=Paper(title=f'Paper {index}',authors=[],openalex_id=f'W-gap-{index}',open_access_metadata={})
        claim_run=ModelRun(research_job=job,agent_role=AgentRole.CLAIM_EXTRACTION,model_tag='gemini-3.5-flash-lite',model_config={},prompt_template_version='v3',status=ModelRunStatus.SUCCESS)
        assessment_run=ModelRun(research_job=job,agent_role=AgentRole.SEMANTIC_REVIEW,model_tag='gemini-3.5-flash-lite',model_config={},prompt_template_version='assessment',status=ModelRunStatus.SUCCESS)
        doc=SourceDocument(paper=paper,document_type='pdf',status='available',content_hash=f'x{index}')
        text=f'Paper {index} reports a measured result.'
        chunk=EvidenceChunk(source_document=doc,page_number=1,chunk_index=0,start_offset=0,end_offset=len(text),text=text)
        claim=ExtractedClaim(paper=paper,model_run=claim_run,claim_type=ClaimType.RESULT,claim_text=text)
        evidence=ClaimEvidence(claim=claim,evidence_chunk=chunk,quotation_text=text,quote_start_offset=0,quote_end_offset=len(text),quotation_status=CitationCheckStatus.VALID,source_location_status=CitationCheckStatus.VALID)
        session.add_all([ResearchJobPaper(research_job=job,paper=paper,included=True), claim_run, assessment_run, evidence]); session.flush()
        snapshot='snapshot-'+str(index)
        session.add(AutomatedSemanticAssessment(claim=claim,model_run_id=assessment_run.id,evidence_snapshot=snapshot,assessment_status=SemanticSupportStatus.SUPPORTED,rationale='Exact text supports it.',uncertainty=None,assessment_version=VERSION,assessed_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc)))
        records.append((paper,claim,evidence,chunk,snapshot))
    session.commit()
    monkeypatch.setattr('app.services.automated_provisional_gaps.eligibility', lambda claim: next(row[4] for row in records if row[1].id == claim.id))
    refs=[{'paper_id':p.id,'claim_id':c.id,'assessment_id':c.automated_assessments[0].id,'evidence_id':e.id,'role':'supporting'} for p,c,e,_chunk,_snapshot in records]
    class Runner:
        def generate(self,*_args):
            return ProvisionalGapResponse(statement='A bounded comparison remains needed.', rationale='The two supported findings identify different conditions.', uncertainty='Two papers are insufficient for novelty.', references=refs), InferenceMetrics(0.1,20,200)
    gap,outcome=run_provisional_gap(session,job_id=job.id,allowed_chunk_ids=frozenset(row[3].id for row in records),assignment=assignment(),runner=Runner())
    assert outcome=='generated' and gap.validation_status=='provisional_not_globally_validated' and len(gap.references)==2
