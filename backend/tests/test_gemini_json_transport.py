"""Offline-only transport comparison and common V3 evidence gates."""
import json
from io import BytesIO
from urllib.error import HTTPError

import pytest
from sqlalchemy import select

from app.core.config import CloudInferenceSettings
from app.models.research import EvidenceChunk, ExtractedClaim
from app.schemas.research import AgentRole
from app.services.claim_extraction_v3 import ClaimExtractionResponseV3, run_development_extraction
from app.services.cloud_inference import (CloudConfigurationError, CloudHTTPError, CloudInferenceRouter,
    CloudResponseError, GeminiClient, SyntheticGeminiResponseCapture, cloud_request_size,
    synthetic_prompt_json_diagnostic_prompt)
from app.services.model_config import AgentModelAssignment, BlindspotModelConfig
from test_claim_extraction_v3 import setup


def assignment(mode):
    return AgentModelAssignment(provider='gemini', model_tag='gemini-3.5-flash-lite', gemini_response_mode=mode, prompt_template_version='claim-extraction-v3')


@pytest.mark.parametrize('mode', ['native_schema', 'prompt_json'])
def test_both_transports_use_identical_evidence_gate_and_persistence(session, mode):
    ids, _ = setup(session, 'edge. The experiment included 42 participants. trailing edge')
    chunk = session.get(EvidenceChunk, ids[1]); page = 'x' * 100 + chunk.text
    seen = []
    def transport(body, headers):
        seen.append(body)
        prompt = body['contents'][0]['parts'][0]['text']
        evidence_line = next(line for line in prompt.splitlines() if line.startswith('[{"evidence_id"'))
        evidence_id = json.loads(evidence_line)[0]['evidence_id']
        value = {'claims':[{'claim_statement':'The experiment included 42 participants.', 'claim_category':'dataset', 'evidence_ids':[evidence_id]}, {'claim_statement':'Invented reference.', 'claim_category':'result', 'evidence_ids':['ev_invented']}]}
        return {'candidates':[{'finishReason':'STOP', 'content':{'parts':[{'text':json.dumps(value)}]}}]}
    a = assignment(mode); client = GeminiClient(api_key='fixture', request_fn=transport)
    router = CloudInferenceRouter(BlindspotModelConfig(claim_extraction=a), CloudInferenceSettings(gemini_api_key='fixture',groq_api_key='fixture'), gemini=client)
    session.commit()
    result = run_development_extraction(session, job_id=ids[0], chunk_id=ids[1], development_chunk_ids=frozenset([ids[1]]), assignment=a, runner=router, source_guard=lambda *_:page, select_complete_spans=True)
    session.commit()
    assert (result.accepted_count,result.rejected_count,result.rejection_codes) == (1,1,('unknown_evidence_id',))
    claim=session.scalar(select(ExtractedClaim).where(ExtractedClaim.model_run_id==result.model_run_id))
    assert claim.verification_result is None and claim.evidence_links[0].quotation_text=='The experiment included 42 participants.'
    config=seen[0]['generationConfig']; assert ('responseJsonSchema' in config)==(mode=='native_schema')
    assert ('responseMimeType' in config)==(mode=='native_schema')
    assert claim.model_run.model_config['gemini_response_mode']==mode


@pytest.mark.parametrize('mode', ['native_schema', 'prompt_json'])
@pytest.mark.parametrize('text,finish,code', [
    ('not json','STOP','invalid_json'), ('','STOP','empty_response'),
    ('{"claims":[{"claim_statement":"x","claim_category":"result"}]}','STOP','missing_required_fields'),
    ('{"claims":[{"claim_statement":"x","claim_category":"unknown","evidence_ids":["ev_x"]}]}','STOP','invalid_field_value'),
    ('{"claims":[]}','MAX_TOKENS','output_truncated'),
    ('{"claims":[],"claims":[]}','STOP','invalid_json'), ('{"claims":NaN}','STOP','invalid_json'),
])
def test_local_schema_and_json_checks_both_modes(mode,text,finish,code):
    client=GeminiClient(api_key='fixture',request_fn=lambda *_:{'candidates':[{'finishReason':finish,'content':{'parts':[{'text':text}]}}]})
    with pytest.raises(CloudResponseError) as exc:
        client.generate_structured('synthetic',ClaimExtractionResponseV3,model='gemini-3.5-flash-lite',output_tokens=256,response_mode=mode)
    assert exc.value.code==code


@pytest.mark.parametrize('mode',['native_schema','prompt_json'])
@pytest.mark.parametrize('status',[400,503])
def test_opt_in_error_hints_are_fixed_and_never_persisted(monkeypatch,status,mode):
    secret='SECRET-AND-PRIVATE-SOURCE'
    body=json.dumps({'error':{'status':'INVALID_ARGUMENT','message':'Unsupported argument in schema '+secret}}).encode()
    monkeypatch.setattr('app.services.cloud_inference.urlopen',lambda *_a,**_kw: (_ for _ in ()).throw(HTTPError('https://invalid',status,secret,{},BytesIO(body))))
    seen=[]
    with pytest.raises(CloudHTTPError) as exc:
        GeminiClient(api_key='fixture',diagnostic_callback=seen.append).generate_structured('synthetic',ClaimExtractionResponseV3,model='gemini-3.5-flash-lite',output_tokens=256,response_mode=mode)
    assert seen==[('unsupported_argument_mentioned',)]
    assert secret not in str(exc.value) + json.dumps(exc.value.http_diagnostics) + str(seen)
    assert exc.value.http_diagnostics['http_status']==status


def test_payload_budget_measures_prompt_mode_schema_as_sent():
    a=assignment('prompt_json'); seen=[]
    def send(payload,_headers):
        seen.append(payload)
        return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{"claims":[]}'}]}}]}
    client=GeminiClient(api_key='fixture',request_fn=send)
    client.generate_structured('synthetic',ClaimExtractionResponseV3,model=a.model_tag,output_tokens=256,response_mode='prompt_json')
    assert len(json.dumps(seen[0]).encode())==cloud_request_size(a,'synthetic',ClaimExtractionResponseV3)


@pytest.mark.parametrize('text,expected,json_status,schema_status', [
    ('```json\n{"claims":[]}\n```', 'invalid_json', 'invalid', 'not_checked'),
    ('Here is the result: {"claims":[]}', 'invalid_json', 'invalid', 'not_checked'),
    ('{"claims":[]} This is the result.', 'invalid_json', 'invalid', 'not_checked'),
    ('{"claims": [', 'invalid_json', 'invalid', 'not_checked'),
    ('  ', 'empty_response', 'not_checked', 'not_checked'),
    ('[]', 'invalid_structure', 'valid', 'invalid'),
    ('{"claims":[{"claim_statement":"Synthetic","claim_category":"result"}]}', 'missing_required_fields', 'valid', 'invalid'),
])
def test_prompt_json_rejects_malformed_or_nonconforming_response(text, expected, json_status, schema_status):
    response={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':text}]}}], 'unused_envelope_field':'not JSON text'}
    client=GeminiClient(api_key='fixture',request_fn=lambda *_:response)
    with pytest.raises(CloudResponseError) as exc:
        client.generate_structured('synthetic evidence',ClaimExtractionResponseV3,model='gemini-3.5-flash-lite',output_tokens=256,response_mode='prompt_json')
    assert exc.value.code==expected
    assert exc.value.diagnostics['json']==json_status and exc.value.diagnostics['schema']==schema_status


def test_prompt_json_accepts_only_generated_text_as_valid_top_level_object():
    response={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{"claims":[]}'}]}}], 'unused_envelope_field':'not JSON text'}
    client=GeminiClient(api_key='fixture',request_fn=lambda *_:response)
    result, metrics=client.generate_structured('synthetic evidence',ClaimExtractionResponseV3,model='gemini-3.5-flash-lite',output_tokens=256,response_mode='prompt_json')
    assert result.claims==[] and metrics.diagnostics['json']=='valid' and metrics.diagnostics['schema']=='valid'


def test_prompt_json_assembles_ordered_text_parts_from_one_candidate_only():
    first = {'claims': [{'claim_statement': 'The fixture reports 42 participants.', 'claim_category': 'dataset', 'evidence_ids': ['ev_fixture']}]}
    encoded = json.dumps(first)
    response = {
        'candidates': [
            {'finishReason': 'STOP', 'content': {'parts': [{'text': encoded[:20]}, {'text': encoded[20:]}]}},
            {'finishReason': 'STOP', 'content': {'parts': [{'text': 'not merged into the first candidate'}]}},
        ],
    }
    result, metrics = GeminiClient(api_key='fixture', request_fn=lambda *_: response).generate_structured(
        'synthetic evidence', ClaimExtractionResponseV3, model='gemini-3.5-flash-lite', output_tokens=256, response_mode='prompt_json')
    assert result.claims[0].evidence_ids == ['ev_fixture']
    assert metrics.diagnostics['candidate_count'] == 2
    assert metrics.diagnostics['candidate_part_count'] == 2
    assert metrics.diagnostics['candidate_part_types'] == {'text': 2}


def test_prompt_json_rejects_malformed_assembled_text_parts():
    response = {
        'candidates': [
            {'finishReason': 'STOP', 'content': {'parts': [{'text': '{"claims":['}, {'text': '}'}]}},
        ],
    }
    with pytest.raises(CloudResponseError) as exc:
        GeminiClient(api_key='fixture', request_fn=lambda *_: response).generate_structured(
            'synthetic evidence', ClaimExtractionResponseV3, model='gemini-3.5-flash-lite', output_tokens=256, response_mode='prompt_json')
    assert exc.value.code == 'invalid_json'


def test_synthetic_capture_is_explicit_gitignored_and_records_only_candidate_text(monkeypatch, tmp_path):
    import app.services.cloud_inference as cloud
    monkeypatch.setattr(cloud, '_SYNTHETIC_CAPTURE_ROOT', tmp_path)
    capture = SyntheticGeminiResponseCapture('synthetic-json-v1', directory=tmp_path)
    response = {'candidates':[{'finishReason':'STOP', 'content':{'parts':[
        {'text':'{"claims":'}, {'text':'[]}'}]}}]}
    result, metrics = GeminiClient(api_key='fixture', request_fn=lambda *_:response).generate_structured(
        synthetic_prompt_json_diagnostic_prompt(), ClaimExtractionResponseV3,
        model='gemini-3.5-flash-lite', output_tokens=256, response_mode='prompt_json', synthetic_capture=capture)
    path = tmp_path / 'synthetic-json-v1.candidate.txt'
    assert result.claims == [] and path.read_text(encoding='utf-8') == '{"claims":[]}'
    assert metrics.diagnostics['candidate_part_count'] == 2
    assert metrics.diagnostics['candidate_part_types'] == {'text':2}
    assert 'candidate text' not in json.dumps(metrics.diagnostics) and 'synthetic_capture_path' in metrics.diagnostics
    with pytest.raises(CloudConfigurationError, match='already used'):
        capture.capture('{"claims":[]}')


@pytest.mark.parametrize('prompt,mode', [
    ('real evidence must not be captured', 'prompt_json'),
    (synthetic_prompt_json_diagnostic_prompt(), 'native_schema'),
])
def test_synthetic_capture_is_prohibited_outside_marked_prompt_json(monkeypatch, tmp_path, prompt, mode):
    import app.services.cloud_inference as cloud
    monkeypatch.setattr(cloud, '_SYNTHETIC_CAPTURE_ROOT', tmp_path)
    capture = SyntheticGeminiResponseCapture('blocked-capture', directory=tmp_path)
    client = GeminiClient(api_key='fixture', request_fn=lambda *_: pytest.fail('transport must not run'))
    with pytest.raises(CloudConfigurationError, match='prohibited'):
        client.generate_structured(prompt, ClaimExtractionResponseV3, model='gemini-3.5-flash-lite', output_tokens=256, response_mode=mode, synthetic_capture=capture)
    assert not list(tmp_path.iterdir())


def test_multipart_nontext_is_rejected_without_repair_or_capture(monkeypatch, tmp_path):
    import app.services.cloud_inference as cloud
    monkeypatch.setattr(cloud, '_SYNTHETIC_CAPTURE_ROOT', tmp_path)
    capture = SyntheticGeminiResponseCapture('multipart-nontext', directory=tmp_path)
    response = {'candidates':[{'finishReason':'STOP', 'content':{'parts':[
        {'functionCall':{'name':'not_text'}}, {'text':'{"claims":[]}'}]}}]}
    with pytest.raises(CloudResponseError) as exc:
        GeminiClient(api_key='fixture', request_fn=lambda *_:response).generate_structured(
            synthetic_prompt_json_diagnostic_prompt(), ClaimExtractionResponseV3,
            model='gemini-3.5-flash-lite', output_tokens=256, response_mode='prompt_json', synthetic_capture=capture)
    assert exc.value.code == 'unsupported_provider_response'
    assert exc.value.diagnostics['candidate_part_count'] == 2
    assert exc.value.diagnostics['candidate_part_types'] == {'text':1, 'non_text':1}
    assert not list(tmp_path.iterdir())
