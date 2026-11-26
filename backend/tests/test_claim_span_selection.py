import json

import pytest
from sqlalchemy import select

from app.models.research import EvidenceChunk, ExtractedClaim
from app.schemas.research import AgentRole
from app.services.claim_extraction_v3 import (ClaimExtractionResponseV3, NoAdmissiblePassage,
    extraction_prompt, generate_evidence_spans, run_development_extraction, select_evidence_spans)
from app.services.cloud_inference import GeminiClient, CloudBudget, cloud_request_size
from app.services.model_config import AgentModelAssignment
from app.services.ollama_client import InferenceMetrics
from test_claim_extraction_v3 import setup


def assignment(**kwargs):
    return AgentModelAssignment(provider='gemini', model_tag='gemini-3.5-flash-lite', **kwargs)


def test_complete_interior_offsets_stability_and_budget(session):
    ids, _ = setup(session, 'fragment starts here. The study included 42 participants. Another method used three datasets. trailing fragment')
    chunk = session.get(EvidenceChunk, ids[1]); page = 'x' * 100 + chunk.text
    spans = select_evidence_spans(chunk, page, assignment())
    assert len(spans) == 2 and spans == select_evidence_spans(chunk, page, assignment())
    assert all(not s.boundary_uncertain and page[s.page_start:s.page_end] == s.text for s in spans)
    assert all(s.text == chunk.text[s.chunk_start:s.chunk_end] and s.page_number == 3 for s in spans)
    assert cloud_request_size(assignment(), extraction_prompt(spans), ClaimExtractionResponseV3) <= 3840
    assert len({s.evidence_id for s in spans}) == len(spans)


def test_no_complete_or_budget_admissible_span_and_bad_page(session):
    ids, _ = setup(session, 'incomplete. A complete sentence reports results. incomplete')
    chunk = session.get(EvidenceChunk, ids[1]); page = 'x' * 100 + chunk.text
    with pytest.raises(NoAdmissiblePassage): select_evidence_spans(chunk, page, assignment(input_tokens=1))
    with pytest.raises(ValueError, match='verified source page'): select_evidence_spans(chunk, 'wrong', assignment())
    chunk.text = 'no complete sentence'; chunk.end_offset = chunk.start_offset + len(chunk.text)
    with pytest.raises(NoAdmissiblePassage): select_evidence_spans(chunk, 'x' * 100 + chunk.text, assignment())


def test_exact_provider_serialization_and_offline_persistence(session):
    ids, _ = setup(session, 'fragment. The study included 42 participants. Another method used three datasets. fragment')
    chunk = session.get(EvidenceChunk, ids[1]); page = 'x' * 100 + chunk.text
    selected = select_evidence_spans(chunk, page, assignment()); captured = []
    def transport(payload, headers):
        captured.append(payload)
        return {'candidates':[{'finishReason':'STOP', 'content':{'parts':[{'text':json.dumps({'claims':[]})}]}}]}
    client = GeminiClient(api_key='test-key', budget=CloudBudget(max_requests=1), request_fn=transport)
    client.generate_structured(extraction_prompt(selected), ClaimExtractionResponseV3, model='gemini-3.5-flash-lite', output_tokens=256)
    assert len(json.dumps(captured[0]).encode()) == cloud_request_size(assignment(), extraction_prompt(selected), ClaimExtractionResponseV3)
    class Runner:
        def generate(self, role, prompt, schema):
            assert role == AgentRole.CLAIM_EXTRACTION
            entries = json.loads(prompt.rsplit('\n', 1)[1]); assert len(entries) == 2
            return schema(claims=[{'claim_statement':'The study included 42 participants.', 'claim_category':'dataset', 'evidence_ids':[entries[0]['evidence_id']]}]), InferenceMetrics(0.1, 1, None)
    session.commit()
    result = run_development_extraction(session, job_id=ids[0], chunk_id=ids[1], development_chunk_ids=frozenset([ids[1]]), assignment=assignment(), runner=Runner(), source_guard=lambda *_: page, select_complete_spans=True)
    session.commit()
    claim = session.scalar(select(ExtractedClaim).where(ExtractedClaim.model_run_id == result.model_run_id))
    assert result.accepted_count == 1 and claim.verification_result is None
    link = claim.evidence_links[0]
    assert link.quotation_text == selected[0].text == chunk.text[link.quote_start_offset:link.quote_end_offset]
    assert page[selected[0].page_start:selected[0].page_end] == link.quotation_text
    session.commit()
    again = run_development_extraction(session, job_id=ids[0], chunk_id=ids[1], development_chunk_ids=frozenset([ids[1]]), assignment=assignment(), runner=Runner(), source_guard=lambda *_: page, select_complete_spans=True)
    assert again.reused and again.model_run_id == result.model_run_id


def test_abbreviation_fragments_and_duplicate_candidates_are_excluded(session, monkeypatch):
    import app.services.claim_extraction_v3 as v3
    ids, _ = setup(session, 'edge. During the 2016 U.S. Election the study found a result. The experiment used three datasets. edge')
    chunk = session.get(EvidenceChunk, ids[1]); page = 'x' * 100 + chunk.text
    original = v3.generate_evidence_spans(chunk)
    monkeypatch.setattr(v3, 'generate_evidence_spans', lambda _: original + original)
    spans = select_evidence_spans(chunk, page, assignment())
    assert [s.text for s in spans] == ['The experiment used three datasets.']


def test_page_mismatch_and_unsupplied_evidence_rejected(session):
    ids, _ = setup(session, 'edge. The study included 42 participants. edge')
    chunk = session.get(EvidenceChunk, ids[1]); page = 'x' * 100 + chunk.text
    with pytest.raises(ValueError, match='verified source page'):
        select_evidence_spans(chunk, page[:100] + 'altered' + page[107:], assignment())
    session.commit()
    class Runner:
        def generate(self, role, prompt, schema):
            return schema(claims=[{'claim_statement':'Unsupported.', 'claim_category':'result', 'evidence_ids':['ev_fabricated']}]), InferenceMetrics(0.1, 1, None)
    result = run_development_extraction(session, job_id=ids[0], chunk_id=ids[1], development_chunk_ids=frozenset([ids[1]]), assignment=assignment(), runner=Runner(), source_guard=lambda *_: page, select_complete_spans=True)
    assert result.accepted_count == 0 and result.rejection_codes == ('unknown_evidence_id',)


def test_selection_requires_guard_and_development_permission(session):
    ids, _ = setup(session)
    class Runner:
        def generate(self, *args): raise AssertionError('must not call provider')
    with pytest.raises(ValueError, match='unapproved'):
        run_development_extraction(session, job_id=ids[0], chunk_id=ids[1], development_chunk_ids=frozenset(), assignment=assignment(), runner=Runner(), select_complete_spans=True)
    session.rollback()
    with pytest.raises(ValueError, match='verified source page'):
        run_development_extraction(session, job_id=ids[0], chunk_id=ids[1], development_chunk_ids=frozenset([ids[1]]), assignment=assignment(), runner=Runner(), select_complete_spans=True)
