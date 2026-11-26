from urllib.error import HTTPError
from io import BytesIO
import json

import pytest
from pydantic import BaseModel, ValidationError

from app.core.config import CloudInferenceSettings, ConfigurationError, load_cloud_inference_settings
from app.schemas.research import AgentRole
from app.services.cloud_inference import (
    CloudAuthenticationError,
    CloudBudget,
    CloudConfigurationError,
    CloudInferenceRouter,
    CloudInputLimitError,
    CloudHTTPError,
    CloudQuotaError,
    CloudResponseError,
    CloudTimeoutError,
    GeminiClient,
    GroqClient,
    OpenRouterClient,
)
from app.services.model_config import AgentModelAssignment, BlindspotModelConfig, InferenceProvider
from app.services.model_runs import ModelRunRecorder
from app.models.research import ResearchJob
from app.services.claim_extraction_v3 import ClaimExtractionResponseV3
from app.services.cloud_inference import cloud_request_payload


class StructuredReply(BaseModel):
    finding: str


def settings() -> CloudInferenceSettings:
    return CloudInferenceSettings(gemini_api_key="test-gemini", groq_api_key="test-groq", openrouter_api_key="test-openrouter")


def openrouter_free_metadata(model: str) -> dict:
    return {"id": model, "pricing": {"prompt": "0", "completion": "0"}, "supported_parameters": ["response_format"]}


def test_gemini_structured_json_and_bounds() -> None:
    seen = {}
    def request(payload, headers):
        seen.update(payload=payload, headers=headers)
        return {"candidates": [{"content": {"parts": [{"text": '{"finding":"quoted"}'}]}}], "usageMetadata": {"candidatesTokenCount": 7}}
    result, metrics = GeminiClient(api_key="test-gemini", request_fn=request).generate_structured("source only", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=64)
    assert result.finding == "quoted" and metrics.output_tokens == 7
    assert seen["payload"]["generationConfig"]["maxOutputTokens"] == 64
    assert seen["payload"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "MINIMAL"}
    assert seen["payload"]["generationConfig"]["responseJsonSchema"] == StructuredReply.model_json_schema()
    assert seen["payload"]["generationConfig"]["responseMimeType"] == "application/json"
    assert seen["headers"]["x-goog-api-key"] == "test-gemini"


def test_groq_structured_json_and_provider_model() -> None:
    seen = {}
    def request(payload, headers):
        seen.update(payload=payload, headers=headers)
        return {"choices": [{"message": {"content": '{"finding":"grounded"}'}}], "usage": {"completion_tokens": 3}}
    result, metrics = GroqClient(api_key="test-groq", request_fn=request).generate_structured("source only", StructuredReply, model="openai/gpt-oss-120b", output_tokens=32)
    assert result.finding == "grounded" and metrics.output_tokens == 3
    assert seen["payload"]["model"] == "openai/gpt-oss-120b"
    assert seen["headers"]["Authorization"] == "Bearer test-groq"
    assert seen["headers"]["Content-Type"] == "application/json"


def test_openrouter_pinned_free_json_request_and_provenance(session) -> None:
    seen = {}
    def request(payload, headers):
        seen.update(payload=payload, headers=headers)
        return {"choices": [{"finish_reason": "stop", "message": {"content": '{"finding":"grounded"}'}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}
    assignment = AgentModelAssignment(provider=InferenceProvider.OPENROUTER, model_tag="inclusionai/ling-3.2-flash:free", output_tokens=512)
    client = OpenRouterClient(api_key="test-openrouter", request_fn=request, metadata_fn=openrouter_free_metadata)
    result, metrics = CloudInferenceRouter(BlindspotModelConfig(claim_extraction=assignment), settings(), openrouter=client).generate(AgentRole.CLAIM_EXTRACTION, "source only", StructuredReply)
    assert result.finding == "grounded" and metrics.output_tokens == 3
    assert seen["payload"] == {"model": "inclusionai/ling-3.2-flash:free", "messages": [{"role": "user", "content": "source only"}], "max_tokens": 512, "response_format": {"type": "json_object"}}
    assert seen["headers"] == {"Content-Type": "application/json", "Authorization": "Bearer test-openrouter"}
    assert "route" not in seen["payload"] and "provider" not in seen["payload"]
    job = ResearchJob(topic="OpenRouter provenance"); session.add(job); session.commit()
    run = ModelRunRecorder().start(session, research_job=job, role=AgentRole.CLAIM_EXTRACTION, assignment=assignment, input_reference="chunk")
    assert assignment.model_tag == seen["payload"]["model"] == run.model_tag == "inclusionai/ling-3.2-flash:free"
    assert run.model_config["provider"] == "openrouter"


def test_openrouter_v3_requests_one_compact_claim_without_schema_change() -> None:
    assignment = AgentModelAssignment(provider=InferenceProvider.OPENROUTER, model_tag="inclusionai/ling-3.2-flash:free", output_tokens=512)
    payload = cloud_request_payload(assignment, "V3 source spans", ClaimExtractionResponseV3)
    content = payload["messages"][0]["content"]
    assert payload["max_tokens"] == 512 and payload["response_format"] == {"type": "json_object"}
    assert "at most one" in content and '{"claims":[]}' in content
    assert ClaimExtractionResponseV3.model_json_schema()["properties"]["claims"]["maxItems"] == 20


@pytest.mark.parametrize("metadata", [None, {"id": "inclusionai/ling-3.2-flash:free", "pricing": {"prompt": "0.01", "completion": "0"}, "supported_parameters": ["response_format"]}, {"id": "inclusionai/ling-3.2-flash:free", "pricing": {"prompt": "0", "completion": "0"}, "supported_parameters": []}])
def test_openrouter_fails_closed_without_current_free_json_metadata(metadata) -> None:
    client = OpenRouterClient(api_key="test-openrouter", request_fn=lambda *_: pytest.fail("generation must not be sent"), metadata_fn=lambda _model: metadata)
    with pytest.raises(CloudConfigurationError):
        client.generate_structured("source only", StructuredReply, model="inclusionai/ling-3.2-flash:free", output_tokens=32)


def test_root_dotenv_resolution_and_process_precedence(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('GROQ_API_KEY="root-test"\nGEMINI_API_KEY=gemini-test\nOPENROUTER_API_KEY=openrouter-test\nBLINDSPOT_CLOUD_FREE_TIER_CONFIRMED=true\n', encoding="utf-8")
    monkeypatch.setattr("app.core.config.ENV_FILE", env_file)
    for name in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "BLINDSPOT_CLOUD_FREE_TIER_CONFIRMED"):
        monkeypatch.delenv(name, raising=False)
    assert load_cloud_inference_settings().groq_api_key == "root-test"
    assert load_cloud_inference_settings().openrouter_api_key == "openrouter-test"
    monkeypatch.setenv("GROQ_API_KEY", "process-test")
    assert load_cloud_inference_settings().groq_api_key == "process-test"


@pytest.mark.parametrize("key", ["", " ", "bad key", "'quoted'", '"quoted"', "bad\nkey"])
def test_malformed_cloud_keys_fail_without_disclosure(key) -> None:
    with pytest.raises(CloudConfigurationError) as exc:
        GroqClient(api_key=key)
    assert "quoted" not in str(exc.value) and "bad key" not in str(exc.value)


@pytest.mark.parametrize("status,code", [(401, "authentication_invalid"), (403, "permission_denied")])
def test_groq_auth_status_is_sanitized_and_recorded(monkeypatch, session, status, code) -> None:
    def fail(*_args, **_kwargs):
        raise HTTPError("https://example.invalid/secret-test", status, "secret-test", {}, None)
    monkeypatch.setattr("app.services.cloud_inference.urlopen", fail)
    with pytest.raises(CloudAuthenticationError) as exc:
        GroqClient(api_key="secret-test").generate_structured("private", StructuredReply, model="openai/gpt-oss-120b", output_tokens=32)
    assert exc.value.code == code and str(exc.value) == code
    run = ModelRunRecorder().start(session, research_job=None, role=AgentRole.CLAIM_EXTRACTION, assignment=AgentModelAssignment(provider=InferenceProvider.GROQ, model_tag="openai/gpt-oss-120b"), input_reference="chunk")
    ModelRunRecorder().fail(run, exc.value)
    assert run.error_category == code and "secret-test" not in run.error_summary


def test_groq_auth_failure_never_falls_back() -> None:
    attempts = []
    def fail(*_args):
        attempts.append("groq")
        raise CloudAuthenticationError("authentication_invalid")
    config = BlindspotModelConfig(claim_extraction=AgentModelAssignment(provider=InferenceProvider.GROQ, model_tag="openai/gpt-oss-120b"))
    router = CloudInferenceRouter(config, settings(), groq=GroqClient(api_key="test", request_fn=fail), gemini=GeminiClient(api_key="test", request_fn=lambda *_: pytest.fail("fallback")))
    with pytest.raises(CloudAuthenticationError):
        router.generate(AgentRole.CLAIM_EXTRACTION, "x", StructuredReply)
    assert attempts == ["groq"]


@pytest.mark.parametrize("status,category", [(400, "invalid_request"), (401, "authentication_invalid"), (403, "permission_denied"), (422, "unprocessable_request")])
def test_http_error_diagnostics_are_bounded_and_sanitized(monkeypatch, session, status, category) -> None:
    body = json.dumps({"error": {"code": "model_permission_blocked_project", "message": "private prompt secret-test"}}).encode()
    def fail(*_args, **_kwargs):
        raise HTTPError("https://example.invalid", status, "private", {}, BytesIO(body))
    monkeypatch.setattr("app.services.cloud_inference.urlopen", fail)
    with pytest.raises((CloudAuthenticationError, CloudHTTPError)) as exc:
        GroqClient(api_key="secret-test").generate_structured("private prompt", StructuredReply, model="openai/gpt-oss-120b", output_tokens=32)
    run = ModelRunRecorder().start(session, research_job=None, role=AgentRole.CLAIM_EXTRACTION, assignment=AgentModelAssignment(provider=InferenceProvider.GROQ, model_tag="openai/gpt-oss-120b"), input_reference="chunk")
    ModelRunRecorder().fail(run, exc.value)
    assert run.error_category == category
    assert run.model_config["http_diagnostics"] == {"http_status": status, "provider_error_code": "model_permission_blocked_project"}
    assert "secret-test" not in json.dumps(run.model_config) + run.error_summary
    assert "private prompt" not in json.dumps(run.model_config) + run.error_summary


@pytest.mark.parametrize("body", [b'not json', b'{"error":{"code":"secret-test"}}', b'x' * 16385])
def test_http_error_unrecognized_or_oversized_body_is_not_recorded(monkeypatch, body) -> None:
    def fail(*_args, **_kwargs):
        raise HTTPError("https://example.invalid", 403, "private", {}, BytesIO(body))
    monkeypatch.setattr("app.services.cloud_inference.urlopen", fail)
    with pytest.raises(CloudAuthenticationError) as exc:
        GroqClient(api_key="secret-test").generate_structured("private", StructuredReply, model="openai/gpt-oss-120b", output_tokens=32)
    assert exc.value.http_diagnostics == {"http_status": 403}


def test_invalid_json_and_disallowed_configuration_are_rejected() -> None:
    client = GeminiClient(api_key="test", request_fn=lambda *_: {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]})
    with pytest.raises(CloudResponseError):
        client.generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=1)
    wrong_shape = GeminiClient(api_key="test", request_fn=lambda *_: {"candidates": [{"content": {"parts": [{"text": '{"other":"field"}'}]}}]})
    with pytest.raises(CloudResponseError):
        wrong_shape.generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=1)
    with pytest.raises(ValidationError):
        AgentModelAssignment(provider=InferenceProvider.GEMINI, model_tag="unapproved-model")


def test_local_request_and_output_budget_has_no_retry() -> None:
    calls = []
    client = GeminiClient(api_key="test", budget=CloudBudget(max_requests=1, max_output_tokens_total=8), request_fn=lambda *_: (calls.append(1) or {"candidates": [{"content": {"parts": [{"text": '{"finding":"ok"}'}]}}]}))
    client.generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=8)
    with pytest.raises(CloudQuotaError):
        client.generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=1)
    assert calls == [1]


def test_http_auth_quota_and_timeout_are_safe(monkeypatch) -> None:
    def raises(error):
        monkeypatch.setattr("app.services.cloud_inference.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
        return GeminiClient(api_key="test")
    with pytest.raises(CloudAuthenticationError):
        raises(HTTPError("https://example.invalid", 401, "", {}, None)).generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=1)
    with pytest.raises(CloudQuotaError):
        raises(HTTPError("https://example.invalid", 429, "", {}, None)).generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=1)
    with pytest.raises(CloudTimeoutError):
        raises(TimeoutError()).generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=1)


def test_missing_keys_and_free_confirmation_are_required(monkeypatch) -> None:
    with pytest.raises(CloudConfigurationError):
        GeminiClient(api_key="")
    monkeypatch.setattr("app.core.config.load_dotenv", lambda **_kwargs: False)
    monkeypatch.delenv("BLINDSPOT_CLOUD_FREE_TIER_CONFIRMED", raising=False)
    with pytest.raises(ConfigurationError):
        load_cloud_inference_settings()


def test_router_never_falls_back_to_ollama() -> None:
    config = BlindspotModelConfig(claim_extraction=AgentModelAssignment(provider=InferenceProvider.OLLAMA))
    router = CloudInferenceRouter(config, settings(), gemini=GeminiClient(api_key="test", request_fn=lambda *_: pytest.fail("must not call a cloud client")))
    with pytest.raises(CloudConfigurationError, match="no Ollama fallback"):
        router.generate(AgentRole.CLAIM_EXTRACTION, "x", StructuredReply)


def test_router_and_model_run_preserve_provider_provenance(session) -> None:
    assignment = AgentModelAssignment(provider=InferenceProvider.GEMINI, model_tag="gemini-3.5-flash-lite")
    reply = GeminiClient(api_key="test", request_fn=lambda *_: {"candidates": [{"content": {"parts": [{"text": '{"finding":"ok"}'}]}}]})
    router = CloudInferenceRouter(BlindspotModelConfig(claim_extraction=assignment), settings(), gemini=reply)
    assert router.generate(AgentRole.CLAIM_EXTRACTION, "x", StructuredReply)[0].finding == "ok"
    job = ResearchJob(topic="cloud provenance"); session.add(job); session.commit()
    run = ModelRunRecorder().start(session, research_job=job, role=AgentRole.CLAIM_EXTRACTION, assignment=assignment, input_reference="chunk")
    assert run.model_tag == "gemini-3.5-flash-lite" and run.model_config["provider"] == "gemini"


@pytest.mark.parametrize("client_class,model", [(GeminiClient, "gemini-3.5-flash-lite"), (GroqClient, "openai/gpt-oss-120b")])
def test_oversized_full_request_rejected_before_transport(client_class, model) -> None:
    calls = []
    budget = CloudBudget()
    client = client_class(api_key="test", budget=budget, request_fn=lambda *_: calls.append(1))
    with pytest.raises(CloudInputLimitError):
        client.generate_structured("instruction " + "界" * 1000, StructuredReply, model=model, output_tokens=32)
    assert calls == [] and budget.requests_used == 0 and budget.input_bytes_reserved == 0


def test_schema_and_instructions_are_included_in_admission() -> None:
    budget = CloudBudget()
    client = GeminiClient(api_key="test", budget=budget, request_fn=lambda *_: {"candidates": [{"content": {"parts": [{"text": '{"finding":"ok"}'}]}}]})
    client.generate_structured("A", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=32)
    assert budget.input_bytes_reserved > len("A".encode("utf-8"))
    with pytest.raises(CloudInputLimitError):
        client.generate_structured("A", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=32, input_tokens=20)
    with pytest.raises(CloudInputLimitError):
        client.generate_structured("instruction " * 500, StructuredReply, model="gemini-3.5-flash-lite", output_tokens=32)


def test_invalid_and_exhausted_input_budgets_fail_closed() -> None:
    with pytest.raises(CloudConfigurationError):
        CloudBudget(max_input_bytes_total=0)
    budget = CloudBudget(max_input_bytes_total=1)
    client = GeminiClient(api_key="test", budget=budget, request_fn=lambda *_: pytest.fail("transport called"))
    with pytest.raises(CloudQuotaError):
        client.generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=32)
    with pytest.raises(CloudConfigurationError):
        client.generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=32, input_tokens=None)


@pytest.mark.parametrize("response,code", [
    ({"candidates": []}, "empty_response"),
    ({"candidates": [{"content": {"parts": [{"text": ""}]}}]}, "empty_response"),
    ({"candidates": [{"content": {"parts": []}}]}, "missing_response_content"),
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "provider_refusal"),
    ({"candidates": [{"finishReason": "MAX_TOKENS"}]}, "output_truncated"),
    ({"candidates": [{"content": {"parts": [{"text": "{"}]}}]}, "invalid_json"),
    ({"candidates": [{"content": {"parts": [{"text": '{}'}]}}]}, "missing_required_fields"),
    ({"candidates": [{"content": {"parts": [{"text": '{"finding": 123}'}]}}]}, "invalid_field_value"),
    ({"candidates": [{"content": {"parts": [{"text": '[]'}]}}]}, "invalid_structure"),
    ({"unexpected": []}, "unsupported_provider_response"),
])
def test_safe_gemini_failure_classification(response, code) -> None:
    client = GeminiClient(api_key="secret-test", request_fn=lambda *_: response)
    with pytest.raises(CloudResponseError) as exc:
        client.generate_structured("private passage", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=32)
    assert exc.value.code == code
    assert "secret-test" not in str(exc.value) and "private passage" not in str(exc.value)


def test_groq_refusal_and_failure_usage_recorded(session) -> None:
    response = {"choices": [{"finish_reason": "content_filter"}], "usage": {"prompt_tokens": 42, "completion_tokens": 5}}
    client = GroqClient(api_key="secret-test", request_fn=lambda *_: response)
    with pytest.raises(CloudResponseError) as exc:
        client.generate_structured("private", StructuredReply, model="openai/gpt-oss-120b", output_tokens=32)
    assert exc.value.code == "provider_refusal"
    run = ModelRunRecorder().start(session, research_job=None, role=AgentRole.GAP_DETECTION, assignment=AgentModelAssignment(provider=InferenceProvider.GROQ, model_tag="openai/gpt-oss-120b"), input_reference="chunk")
    ModelRunRecorder().fail(run, exc.value)
    assert run.model_config["response_diagnostics"]["input_tokens"] == 42
    assert run.output_tokens == 5 and run.error_category == "provider_refusal"


def test_success_usage_diagnostics_and_safe_transport_failure() -> None:
    client = GeminiClient(api_key="secret-test", request_fn=lambda *_: {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"finding":"ok"}'}]}}], "usageMetadata": {"promptTokenCount": 41, "candidatesTokenCount": 4}})
    _, metrics = client.generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=32)
    assert metrics.diagnostics["json"] == "valid" and metrics.diagnostics["schema"] == "valid"
    assert metrics.diagnostics["input_tokens"] == 41 and metrics.diagnostics["finish_reason"] == "STOP"
    def fail(*_): raise RuntimeError("secret-test private passage")
    with pytest.raises(CloudResponseError) as exc:
        GeminiClient(api_key="secret-test", request_fn=fail).generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=32)
    assert exc.value.code == "unexpected_transport_failure" and "secret-test" not in str(exc.value)


def test_raw_body_hash_is_retained_without_raw_body(monkeypatch) -> None:
    from hashlib import sha256
    raw = b'{"candidates":[{"content":{"parts":[{"text":"not json"}]}}]}'
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return raw
    monkeypatch.setattr("app.services.cloud_inference.urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(CloudResponseError) as exc:
        GeminiClient(api_key="secret-test").generate_structured("private passage", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=32)
    assert exc.value.diagnostics["raw_response_hash"] == sha256(raw).hexdigest()
    assert "not json" not in str(exc.value.diagnostics)


def test_gemini_thinking_usage_and_missing_values() -> None:
    response = {"candidates": [{"finishReason": "MAX_TOKENS"}], "usageMetadata": {"promptTokenCount": 15, "candidatesTokenCount": 6, "thoughtsTokenCount": 0, "totalTokenCount": 21}}
    client = GeminiClient(api_key="test", request_fn=lambda *_: response)
    with pytest.raises(CloudResponseError) as exc:
        client.generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=256)
    assert exc.value.code == "output_truncated"
    assert exc.value.diagnostics["thinking_tokens"] == 0 and exc.value.diagnostics["total_tokens"] == 21
    del response["usageMetadata"]["thoughtsTokenCount"]
    with pytest.raises(CloudResponseError) as exc:
        client.generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=256)
    assert "thinking_tokens" not in exc.value.diagnostics


def test_thinking_settings_and_512_override_are_bounded() -> None:
    seen = []
    client = GeminiClient(api_key="test", request_fn=lambda payload, _headers: (seen.append(payload) or {"candidates": [{"content": {"parts": [{"text": '{"finding":"ok"}'}]}}]}))
    client.generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=512, thinking_level="LOW")
    assert seen[0]["generationConfig"]["maxOutputTokens"] == 512
    assert seen[0]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "LOW"
    with pytest.raises(CloudConfigurationError):
        client.generate_structured("x", StructuredReply, model="gemini-3.5-flash-lite", output_tokens=256, thinking_level="OFF")
    with pytest.raises(ValidationError):
        AgentModelAssignment(provider=InferenceProvider.GROQ, model_tag="openai/gpt-oss-120b", thinking_level="MINIMAL")
    assert len(seen) == 1
