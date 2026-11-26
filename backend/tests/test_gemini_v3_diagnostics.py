"""Offline V3 request contract and sanitized Gemini error diagnostics."""
import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from app.schemas.research import AgentRole
from app.services.claim_extraction_v3 import ClaimExtractionResponseV3
from app.services.cloud_inference import CloudHTTPError, CloudResponseError, GeminiClient
from app.services.model_config import AgentModelAssignment
from app.services.model_runs import ModelRunRecorder


def test_v3_complete_gemini_request_and_local_validation():
    captured = []
    def send(body, headers):
        captured.append(body)
        return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps({'claims': [{
            'claim_statement': 'Fixture finding.', 'claim_category': 'result', 'evidence_ids': ['ev_fixture']}]})}]}}]}
    client = GeminiClient(api_key='test-key', request_fn=send)
    output, metrics = client.generate_structured('Synthetic evidence.', ClaimExtractionResponseV3,
        model='gemini-3.5-flash-lite', output_tokens=512)
    assert output.claims[0].evidence_ids == ['ev_fixture']
    assert metrics.diagnostics['schema'] == 'valid'
    body = captured[0]; config = body['generationConfig']; schema = config['responseJsonSchema']
    assert body['contents'] == [{'parts': [{'text': 'Synthetic evidence.'}]}]
    assert 'systemInstruction' not in body and config['responseMimeType'] == 'application/json'
    assert config['thinkingConfig'] == {'thinkingLevel': 'MINIMAL'} and config['maxOutputTokens'] == 512
    assert schema == ClaimExtractionResponseV3.model_json_schema()
    candidate = schema['$defs']['ClaimCandidateV3']
    assert set(candidate['required']) == {'claim_statement', 'claim_category', 'evidence_ids'}
    assert candidate['additionalProperties'] is False and candidate['properties']['evidence_ids']['minItems'] == 1
    assert candidate['properties']['uncertainty_or_missing_information']['anyOf'] == [{'type':'string'}, {'type':'null'}]


@pytest.mark.parametrize('claim', [
    {'claim_statement':'', 'claim_category':'result', 'evidence_ids':['ev_fixture']},
    {'claim_statement':'Fixture.', 'claim_category':'unknown', 'evidence_ids':['ev_fixture']},
    {'claim_statement':'Fixture.', 'claim_category':'result'},
    {'claim_statement':'Fixture.', 'claim_category':'result', 'evidence_ids':[]},
])
def test_v3_invalid_response_rejected_locally(claim):
    response = {'candidates': [{'finishReason':'STOP', 'content': {'parts':[{'text':json.dumps({'claims':[claim]})}]}}]}
    with pytest.raises(CloudResponseError):
        GeminiClient(api_key='test', request_fn=lambda *_: response).generate_structured(
            'Synthetic evidence.', ClaimExtractionResponseV3, model='gemini-3.5-flash-lite', output_tokens=512)


def test_gemini_400_structured_diagnostics_are_allowlisted(monkeypatch, session):
    secret = 'secret-key-and-private-passage'
    body = {'error': {'code': 400, 'status': 'INVALID_ARGUMENT', 'message': secret,
        'details': [
            {'@type':'type.googleapis.com/google.rpc.BadRequest', 'fieldViolations': [
                {'field':'generation_config.response_json_schema', 'description':secret},
                {'field':secret, 'description':secret}]},
            {'@type':'type.googleapis.com/google.rpc.ErrorInfo', 'reason':'SCHEMA_VALIDATION_FAILED', 'metadata':{'secret':secret}},
            {'@type':'type.googleapis.com/google.rpc.ErrorInfo', 'reason':secret}]}}
    def fail(*_args, **_kwargs):
        raise HTTPError('https://example.invalid', 400, secret, {}, BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr('app.services.cloud_inference.urlopen', fail)
    with pytest.raises(CloudHTTPError) as exc:
        GeminiClient(api_key=secret).generate_structured('private source', ClaimExtractionResponseV3,
            model='gemini-3.5-flash-lite', output_tokens=512)
    assert exc.value.http_diagnostics == {'http_status':400, 'provider_status':'INVALID_ARGUMENT',
        'invalid_fields':['generation_config.response_json_schema'], 'provider_reasons':['SCHEMA_VALIDATION_FAILED']}
    run = ModelRunRecorder().start(session, research_job=None, role=AgentRole.CLAIM_EXTRACTION,
        assignment=AgentModelAssignment(provider='gemini', model_tag='gemini-3.5-flash-lite'), input_reference='hash')
    ModelRunRecorder().fail(run, exc.value)
    assert secret not in json.dumps(run.model_config) and secret not in run.error_summary
    assert run.model_config['http_diagnostics'] == exc.value.http_diagnostics


@pytest.mark.parametrize('body', [b'not json', b'x' * 16386, b'{"error":{"status":"sensitive text"}}'])
def test_malformed_large_and_unknown_errors_keep_only_status(monkeypatch, body):
    def fail(*_args, **_kwargs): raise HTTPError('https://example.invalid', 400, 'private', {}, BytesIO(body))
    monkeypatch.setattr('app.services.cloud_inference.urlopen', fail)
    with pytest.raises(CloudHTTPError) as exc:
        GeminiClient(api_key='test').generate_structured('private', ClaimExtractionResponseV3,
            model='gemini-3.5-flash-lite', output_tokens=512)
    assert exc.value.http_diagnostics == {'http_status':400}
