"""Strict offline tests for Gemini V3's compact plain-text response contract."""
import json

import pytest

from app.schemas.research import AgentRole
from app.services.claim_extraction_v3 import (
    ClaimExtractionResponseV3,
    extraction_prompt,
    parse_gemini_plain_text_response,
    run_development_extraction,
)
from app.services.cloud_inference import CloudResponseError
from app.services.model_config import AgentModelAssignment
from app.services.ollama_client import InferenceMetrics
from app.models.research import EvidenceChunk
from test_claim_extraction_v3 import setup


def parse(text):
    diagnostics = {}
    result = parse_gemini_plain_text_response(text, ClaimExtractionResponseV3, diagnostics)
    return result, diagnostics


def test_plain_text_contract_accepts_one_claim_and_abstention():
    result, diagnostics = parse('CLAIM: The experiment included 42 participants.\nEVIDENCE_ID: ev_fixture')
    assert result.claims[0].claim_statement == 'The experiment included 42 participants.'
    assert result.claims[0].evidence_ids == ['ev_fixture']
    assert result.claims[0].claim_category.value == 'result'
    assert diagnostics == {'plain_text_contract': 'valid', 'json': 'not_applicable', 'schema': 'valid'}
    abstention, _ = parse('ABSTAIN')
    assert abstention.claims == []


@pytest.mark.parametrize('text', [
    'CLAIM: Finding only.',
    'EVIDENCE_ID: ev_fixture',
    'CLAIM: First.\nCLAIM: Second.\nEVIDENCE_ID: ev_fixture',
    'CLAIM: Finding.\nEVIDENCE_ID: ev_fixture\nEVIDENCE_ID: ev_other',
    'CLAIM: Finding.\nEVIDENCE_ID: ev_fixture\nCommentary',
    '```text\nCLAIM: Finding.\nEVIDENCE_ID: ev_fixture\n```',
    'not a contract',
])
def test_plain_text_contract_rejects_missing_duplicate_multiple_and_extra_content(text):
    with pytest.raises(CloudResponseError) as exc:
        parse(text)
    assert exc.value.code == 'invalid_plain_text_contract'


class PlainTextRunner:
    def __init__(self, response_text):
        self.response_text = response_text

    def generate(self, _role, _prompt, schema):
        return parse_gemini_plain_text_response(self.response_text, schema, {}), InferenceMetrics(0.0, 0, None)


def assignment():
    return AgentModelAssignment(
        provider='gemini', model_tag='gemini-3.5-flash-lite', output_tokens=512,
        gemini_response_mode='plain_text', prompt_template_version='claim-extraction-v3',
    )


def test_plain_text_unknown_id_is_rejected_by_existing_v3_span_allowlist(session):
    ids, _ = setup(session, 'edge. The experiment included 42 participants. trailing edge')
    chunk = session.get(EvidenceChunk, ids[1])
    page = 'x' * 100 + chunk.text
    runner = PlainTextRunner('CLAIM: The experiment included 42 participants.\nEVIDENCE_ID: ev_not_supplied')
    session.commit()
    result = run_development_extraction(
        session, job_id=ids[0], chunk_id=ids[1], development_chunk_ids=frozenset([ids[1]]),
        assignment=assignment(), runner=runner, source_guard=lambda *_: page, select_complete_spans=True,
    )
    assert result.accepted_count == 0
    assert result.rejection_codes == ('unknown_evidence_id',)


def test_plain_text_prompt_has_no_json_abstention_or_category_instruction(session):
    ids, _ = setup(session, 'edge. The experiment included 42 participants. trailing edge')
    chunk = session.get(EvidenceChunk, ids[1])
    from app.services.claim_extraction_v3 import generate_evidence_spans
    prompt = extraction_prompt(generate_evidence_spans(chunk), plain_text_contract=True)
    assert 'CLAIM: <one concise factual claim supported by the supplied evidence>' in prompt
    assert 'return ABSTAIN' in prompt
    assert 'Return JSON matching the schema' not in prompt
