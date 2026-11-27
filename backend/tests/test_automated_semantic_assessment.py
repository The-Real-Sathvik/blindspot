from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.config import CloudInferenceSettings
from app.models.research import (AutomatedSemanticAssessment, ClaimEvidence, EvidenceChunk, ExtractedClaim, ModelRun,
    Paper, ResearchJob, ResearchJobPaper, SourceDocument, VerificationResult)
from app.schemas.research import AgentRole, CitationCheckStatus, ClaimType, ModelRunStatus, SemanticSupportStatus
from app.services.automated_semantic_assessment import (AssessmentResponse, VERSION, assess,
    parse_gemini_assessment_response)
from app.services.automated_provisional_gaps import (ProvisionalGapResponse, parse_gemini_gap_response, persist,
    run as run_provisional_gap)
from app.services.cloud_inference import CloudInferenceError, CloudInferenceRouter, CloudResponseError, GeminiClient
from app.services.model_config import AgentModelAssignment, BlindspotModelConfig
from app.services.ollama_client import InferenceMetrics
from app.services.job_state import project as project_job_state
from app.services.research_report import build_research_report


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
    return AgentModelAssignment(provider='gemini',model_tag='gemini-3.5-flash-lite',output_tokens=512,gemini_response_mode='plain_text',prompt_template_version='temporary')


def assessment_assignment():
    return AgentModelAssignment(provider='gemini',model_tag='gemini-3.5-flash-lite',output_tokens=512,gemini_response_mode='plain_text',prompt_template_version=VERSION)


@pytest.mark.parametrize('status', [
    SemanticSupportStatus.SUPPORTED,
    SemanticSupportStatus.PARTIALLY_SUPPORTED,
    SemanticSupportStatus.CONTRADICTED,
    SemanticSupportStatus.INSUFFICIENT_EVIDENCE,
])
def test_gemini_assessment_plain_text_parses_all_supported_statuses(status):
    diagnostics={}
    content=f'STATUS: {status.value}\nRATIONALE: The quotation was evaluated directly.\nUNCERTAINTY: NONE'
    result=parse_gemini_assessment_response(content,AssessmentResponse,diagnostics)
    assert result.assessment_status is status
    assert result.rationale=='The quotation was evaluated directly.'
    assert result.uncertainty is None
    assert diagnostics['plain_text_contract']=='valid'


def test_gemini_assessment_abstain_fails_closed_to_insufficient_evidence():
    diagnostics={}
    result=parse_gemini_assessment_response('ABSTAIN',AssessmentResponse,diagnostics)
    assert result.assessment_status is SemanticSupportStatus.INSUFFICIENT_EVIDENCE
    assert result.uncertainty
    assert diagnostics['plain_text_contract']=='valid'


@pytest.mark.parametrize('content', [
    '{"assessment_status":"supported","rationale":"Looks good.","uncertainty":null}',
    'STATUS: unsupported\nRATIONALE: No support.\nUNCERTAINTY: NONE',
    'The answer is:\nSTATUS: supported\nRATIONALE: Direct support.\nUNCERTAINTY: NONE',
    'STATUS: supported\nRATIONALE: Direct support.\nUNCERTAINTY: NONE\nEXTRA: text',
])
def test_gemini_assessment_malformed_or_unsupported_response_is_rejected(content):
    diagnostics={}
    with pytest.raises(CloudResponseError,match='invalid_plain_text_contract'):
        parse_gemini_assessment_response(content,AssessmentResponse,diagnostics)
    assert diagnostics['plain_text_contract']=='invalid'


def test_gemini_assessment_router_persists_plain_text_and_keeps_human_review_separate(session,monkeypatch):
    job,claim=fixture(session)
    monkeypatch.setattr('app.services.automated_semantic_assessment.ensure_processed_source',lambda *_:'page')
    calls=[]
    content='STATUS: insufficient_evidence\nRATIONALE: The quotation does not establish the full claim.\nUNCERTAINTY: The supplied evidence is limited.'
    def request(payload,headers):
        calls.append((payload,headers))
        return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':content}]}}],
                'usageMetadata':{'promptTokenCount':12,'candidatesTokenCount':18}}
    assignment=assessment_assignment()
    runner=CloudInferenceRouter(
        BlindspotModelConfig(semantic_review=assignment),
        CloudInferenceSettings(gemini_api_key='test-gemini',groq_api_key='test-groq'),
        gemini=GeminiClient(api_key='test-gemini',request_fn=request),
    )

    result=assess(session,job_id=job.id,claim_id=claim.id,assignment=assignment,runner=runner)

    saved=session.get(AutomatedSemanticAssessment,result.assessment_id)
    model_run=session.get(ModelRun,result.model_run_id)
    assert result.outcome=='assessed' and saved.assessment_status is SemanticSupportStatus.INSUFFICIENT_EVIDENCE
    assert claim.verification_result is None
    assert model_run.prompt_template_version==VERSION
    assert model_run.model_config['response_diagnostics']['plain_text_contract']=='valid'
    assert len(calls)==1
    assert 'responseMimeType' not in calls[0][0]['generationConfig']
    assert calls[0][0]['generationConfig']['maxOutputTokens']==512


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


def test_failed_semantic_assessment_is_not_retried_for_same_evidence(session, monkeypatch):
    job, claim = fixture(session)
    monkeypatch.setattr('app.services.automated_semantic_assessment.ensure_processed_source', lambda *_: 'page')

    class TruncatedRunner:
        calls = 0

        def generate(self, *_args):
            self.calls += 1
            raise CloudResponseError('output_truncated', {'finish_reason': 'MAX_TOKENS', 'output_tokens': 508})

    runner = TruncatedRunner()
    first = assess(session, job_id=job.id, claim_id=claim.id, assignment=assessment_assignment(), runner=runner)
    second = assess(session, job_id=job.id, claim_id=claim.id, assignment=assessment_assignment(), runner=runner)
    saved_run = session.get(ModelRun, first.model_run_id)
    assert first.outcome == second.outcome == 'failed'
    assert second.reused and runner.calls == 1
    assert saved_run.error_category == 'output_truncated'
    assert saved_run.model_config['response_diagnostics']['finish_reason'] == 'MAX_TOKENS'


def test_provisional_gap_strict_plain_text_contract_and_abstention():
    first, second, counter = (str(uuid4()) for _ in range(3))
    content = (f'GAP: A bounded comparison remains needed.\nSUPPORTING: {first},{second}\n'
               f'COUNTEREVIDENCE: {counter}\nRATIONALE: The supported findings study different conditions.\n'
               'UNCERTAINTY: Two papers cannot establish global novelty.')
    diagnostics = {}
    response = parse_gemini_gap_response(content, ProvisionalGapResponse, diagnostics)
    assert response.supporting_evidence_ids == [first, second]
    assert response.counterevidence_ids == [counter]
    assert diagnostics['plain_text_contract'] == 'valid'
    abstain_diagnostics = {}
    abstention = parse_gemini_gap_response('ABSTAIN', ProvisionalGapResponse, abstain_diagnostics)
    assert abstention.abstained and abstention.supporting_evidence_ids == []
    assert abstain_diagnostics['plain_text_contract'] == 'valid'


def test_agent_two_router_uses_plain_text_parser_without_prompt_json_schema():
    first, second = str(uuid4()), str(uuid4())
    content = (f'GAP: A bounded comparison remains needed.\nSUPPORTING: {first},{second}\n'
               'COUNTEREVIDENCE: NONE\nRATIONALE: Findings differ.\nUNCERTAINTY: The corpus is limited.')
    requests = []

    def request(payload, _headers):
        requests.append(payload)
        return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': content}]}}],
                'usageMetadata': {'promptTokenCount': 12, 'candidatesTokenCount': 72}}

    gap_assignment = assignment()
    runner = CloudInferenceRouter(
        BlindspotModelConfig(gap_detection=gap_assignment),
        CloudInferenceSettings(gemini_api_key='test-gemini', groq_api_key='test-groq'),
        gemini=GeminiClient(api_key='test-gemini', request_fn=request),
    )
    response, metrics = runner.generate(AgentRole.GAP_DETECTION, 'compact prompt', ProvisionalGapResponse)
    assert response.supporting_evidence_ids == [first, second]
    assert metrics.diagnostics['plain_text_contract'] == 'valid'
    assert 'responseMimeType' not in requests[0]['generationConfig']
    assert 'responseJsonSchema' not in requests[0]['generationConfig']


@pytest.mark.parametrize('content', [
    '{"statement":"gap","references":[]}',
    'GAP: A gap\nSUPPORTING: not-an-id,not-an-id\nCOUNTEREVIDENCE: NONE\nRATIONALE: Reason\nUNCERTAINTY: Limited.',
    'GAP: A gap\nSUPPORTING: 00000000-0000-0000-0000-000000000001,00000000-0000-0000-0000-000000000001\nCOUNTEREVIDENCE: NONE\nRATIONALE: Reason\nUNCERTAINTY: Limited.',
    'GAP: A gap\nSUPPORTING: 00000000-0000-0000-0000-000000000001,00000000-0000-0000-0000-000000000002\nCOUNTEREVIDENCE: NONE\nRATIONALE: Reason\nUNCERTAINTY: Limited.\nEXTRA: text',
    'GAP: A truncated response',
])
def test_provisional_gap_malformed_response_is_rejected(content):
    diagnostics = {}
    with pytest.raises(CloudResponseError, match='invalid_plain_text_contract'):
        parse_gemini_gap_response(content, ProvisionalGapResponse, diagnostics)
    assert diagnostics['plain_text_contract'] == 'invalid'


def test_provisional_gap_requires_two_current_supported_papers_and_persists(session, monkeypatch):
    job=ResearchJob(topic='Two paper gap')
    session.add(job); session.flush(); records=[]
    for index in range(3):
        paper=Paper(title=f'Paper {index}',authors=[],openalex_id=f'W-gap-{index}',open_access_metadata={})
        claim_run=ModelRun(research_job=job,agent_role=AgentRole.CLAIM_EXTRACTION,model_tag='gemini-3.5-flash-lite',model_config={},prompt_template_version='v3',status=ModelRunStatus.SUCCESS)
        assessment_run=ModelRun(research_job=job,agent_role=AgentRole.SEMANTIC_REVIEW,model_tag='gemini-3.5-flash-lite',model_config={},prompt_template_version='assessment',status=ModelRunStatus.SUCCESS)
        doc=SourceDocument(paper=paper,document_type='pdf',status='available',content_hash=f'x{index}')
        text=f'Paper {index} reports a measured result.'
        chunk=EvidenceChunk(source_document=doc,page_number=1,chunk_index=0,start_offset=0,end_offset=len(text),text=text)
        claim=ExtractedClaim(paper=paper,model_run=claim_run,claim_type=ClaimType.RESULT,claim_text=text)
        evidence=ClaimEvidence(claim=claim,evidence_chunk=chunk,quotation_text=text,quote_start_offset=0,quote_end_offset=len(text),quotation_status=CitationCheckStatus.VALID,source_location_status=CitationCheckStatus.VALID)
        session.add_all([ResearchJobPaper(research_job=job,paper=paper,included=True,analysis_selected=True), claim_run, assessment_run, evidence]); session.flush()
        snapshot='snapshot-'+str(index)
        status = SemanticSupportStatus.PARTIALLY_SUPPORTED if index == 2 else SemanticSupportStatus.SUPPORTED
        session.add(AutomatedSemanticAssessment(claim=claim,model_run_id=assessment_run.id,evidence_snapshot=snapshot,assessment_status=status,rationale='Exact text supports it.',uncertainty=None,assessment_version=VERSION,assessed_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc)))
        records.append((paper,claim,evidence,chunk,snapshot))
    session.commit()
    monkeypatch.setattr('app.services.automated_provisional_gaps.eligibility', lambda claim: next(row[4] for row in records if row[1].id == claim.id))
    supporting_ids = [records[index][2].id for index in range(2)]
    counterevidence_id = records[2][2].id
    class Runner:
        calls = 0

        def generate(self,*_args):
            self.calls += 1
            return ProvisionalGapResponse(statement='A bounded comparison remains needed.',
                rationale='The two supported findings identify different conditions.',
                uncertainty='Two papers are insufficient for novelty.',
                supporting_evidence_ids=supporting_ids, counterevidence_ids=[counterevidence_id]), InferenceMetrics(0.1,20,200)
    runner = Runner()
    allowed = frozenset(row[3].id for row in records)
    gap,outcome=run_provisional_gap(session,job_id=job.id,allowed_chunk_ids=allowed,assignment=assignment(),runner=runner)
    assert outcome=='generated' and gap.validation_status=='provisional_not_globally_validated' and len(gap.references)==3
    assert sum(reference.role == 'counterevidence' for reference in gap.references) == 1
    reused, repeat_outcome = run_provisional_gap(session,job_id=job.id,allowed_chunk_ids=allowed,assignment=assignment(),runner=runner)
    assert reused.id == gap.id and repeat_outcome == 'reused' and runner.calls == 1


def test_failed_or_unauthorized_gap_response_is_persisted_as_terminal_failure(session, monkeypatch):
    job=ResearchJob(topic='No automatic gap retry')
    session.add(job); session.flush(); records=[]
    for index in range(2):
        paper=Paper(title=f'Paper {index}',authors=[],openalex_id=f'W-terminal-{index}',open_access_metadata={})
        claim_run=ModelRun(research_job=job,agent_role=AgentRole.CLAIM_EXTRACTION,model_tag='gemini-3.5-flash-lite',model_config={},prompt_template_version='v3',status=ModelRunStatus.SUCCESS)
        assessment_run=ModelRun(research_job=job,agent_role=AgentRole.SEMANTIC_REVIEW,model_tag='gemini-3.5-flash-lite',model_config={},prompt_template_version='assessment',status=ModelRunStatus.SUCCESS)
        doc=SourceDocument(paper=paper,document_type='pdf',status='available',content_hash=f'terminal-{index}')
        text=f'Paper {index} reports a supported result.'
        chunk=EvidenceChunk(source_document=doc,page_number=1,chunk_index=0,start_offset=0,end_offset=len(text),text=text)
        claim=ExtractedClaim(paper=paper,model_run=claim_run,claim_type=ClaimType.RESULT,claim_text=text)
        evidence=ClaimEvidence(claim=claim,evidence_chunk=chunk,quotation_text=text,quote_start_offset=0,quote_end_offset=len(text),quotation_status=CitationCheckStatus.VALID,source_location_status=CitationCheckStatus.VALID)
        session.add_all([ResearchJobPaper(research_job=job,paper=paper,included=True,analysis_selected=True),claim_run,assessment_run,evidence]);session.flush()
        snapshot=f'terminal-snapshot-{index}'
        assessment=AutomatedSemanticAssessment(claim=claim,model_run_id=assessment_run.id,evidence_snapshot=snapshot,assessment_status=SemanticSupportStatus.SUPPORTED,rationale='Supported.',uncertainty=None,assessment_version=VERSION,assessed_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc))
        session.add(assessment);session.flush()
        records.append((claim,chunk,snapshot))
    session.commit()
    monkeypatch.setattr('app.services.automated_provisional_gaps.eligibility', lambda claim: next(row[2] for row in records if row[0].id == claim.id))

    class UnauthorizedRunner:
        calls = 0

        def generate(self, *_args):
            self.calls += 1
            return ProvisionalGapResponse(statement='A comparison may be needed.', rationale='The evidence differs.',
                uncertainty='The corpus is small.', supporting_evidence_ids=[str(uuid4()), str(uuid4())]), InferenceMetrics(0.1,20,200)

    runner = UnauthorizedRunner()
    allowed = frozenset(row[1].id for row in records)
    _, first_outcome = run_provisional_gap(session,job_id=job.id,allowed_chunk_ids=allowed,assignment=assignment(),runner=runner)
    _, second_outcome = run_provisional_gap(session,job_id=job.id,allowed_chunk_ids=allowed,assignment=assignment(),runner=runner)
    latest_run = session.scalar(select(ModelRun).where(ModelRun.agent_role == AgentRole.GAP_DETECTION,
        ModelRun.research_job_id == job.id).order_by(ModelRun.created_at.desc()))
    assert first_outcome == 'failed' and second_outcome == 'already_attempted'
    assert runner.calls == 1 and latest_run.error_category == 'invalid_gap_response'
    workflow = project_job_state(session, job.id)
    assert workflow['stages']['gaps']['processing'] == 'failed'
    assert 'malformed or unauthorized response' in workflow['stages']['gaps']['detail']
    report = build_research_report(session, job.id)
    assert 'No candidate research gaps were saved.' in report
    assert 'malformed or unauthorized response' in report


def test_provisional_gap_persistence_rejects_same_job_non_selected_paper(session, monkeypatch):
    job=ResearchJob(topic='Non-selected gap evidence')
    session.add(job); session.flush()
    records=[]
    for index in range(3):
        paper=Paper(title=f'Paper {index}',authors=[],openalex_id=f'W-selected-{index}',open_access_metadata={})
        claim_run=ModelRun(research_job=job,agent_role=AgentRole.CLAIM_EXTRACTION,model_tag='gemini-3.5-flash-lite',model_config={},prompt_template_version='v3',status=ModelRunStatus.SUCCESS)
        assessment_run=ModelRun(research_job=job,agent_role=AgentRole.SEMANTIC_REVIEW,model_tag='gemini-3.5-flash-lite',model_config={},prompt_template_version='assessment',status=ModelRunStatus.SUCCESS)
        gap_run=ModelRun(research_job=job,agent_role=AgentRole.GAP_DETECTION,model_tag='gemini-3.5-flash-lite',model_config={},prompt_template_version='gap',status=ModelRunStatus.SUCCESS)
        doc=SourceDocument(paper=paper,document_type='pdf',status='available',content_hash=f'selected-x{index}')
        text=f'Paper {index} reports a supported result.'
        chunk=EvidenceChunk(source_document=doc,page_number=1,chunk_index=0,start_offset=0,end_offset=len(text),text=text)
        claim=ExtractedClaim(paper=paper,model_run=claim_run,claim_type=ClaimType.RESULT,claim_text=text)
        evidence=ClaimEvidence(claim=claim,evidence_chunk=chunk,quotation_text=text,quote_start_offset=0,quote_end_offset=len(text),quotation_status=CitationCheckStatus.VALID,source_location_status=CitationCheckStatus.VALID)
        link=ResearchJobPaper(research_job=job,paper=paper,included=True,analysis_selected=index < 2)
        session.add_all([link,claim_run,assessment_run,gap_run,evidence]); session.flush()
        snapshot=f'snapshot-{index}'
        assessment=AutomatedSemanticAssessment(claim=claim,model_run_id=assessment_run.id,evidence_snapshot=snapshot,assessment_status=SemanticSupportStatus.SUPPORTED,rationale='Exact text supports it.',uncertainty=None,assessment_version=VERSION,assessed_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc))
        session.add(assessment); session.flush()
        records.append((paper,claim,evidence,chunk,snapshot,gap_run))
    session.commit()
    monkeypatch.setattr('app.services.automated_provisional_gaps.eligibility', lambda claim: next(row[4] for row in records if row[1].id == claim.id))

    selected_ids=frozenset(row[3].id for row in records[:2])
    selected_refs=[
        {'paper_id':records[index][0].id,'claim_id':records[index][1].id,'assessment_id':records[index][1].automated_assessments[0].id,'evidence_id':records[index][2].id,'role':'supporting'}
        for index in range(2)
    ]
    persist(session,job_id=job.id,model_run_id=records[0][5].id,allowed_chunk_ids=selected_ids,
            statement='A bounded comparison remains needed.',rationale='Selected findings differ.',uncertainty='Limited two-paper evidence.',
            references=selected_refs)
    session.commit()

    non_selected=records[2]
    non_selected_ref={'paper_id':non_selected[0].id,'claim_id':non_selected[1].id,'assessment_id':non_selected[1].automated_assessments[0].id,'evidence_id':non_selected[2].id,'role':'supporting'}
    with pytest.raises(ValueError, match='Gap references are not current automated evidence'):
        persist(session,job_id=job.id,model_run_id=records[0][5].id,
                allowed_chunk_ids=selected_ids,
                statement='A prohibited non-selected reference.',rationale='The third paper is not analyzed.',uncertainty='Not eligible.',
                references=selected_refs+[non_selected_ref])
    with pytest.raises(ValueError, match='outside the finalized analysis paper allowlist'):
        persist(session,job_id=job.id,model_run_id=records[0][5].id,
                allowed_chunk_ids=frozenset([non_selected[3].id]),
                statement='A prohibited non-selected comparison.',rationale='The third paper is not analyzed.',uncertainty='Not eligible.',
                references=selected_refs+[non_selected_ref])
