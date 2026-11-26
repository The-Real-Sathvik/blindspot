"""Bounded, allowlisted cloud structured-inference clients; no automatic fallback."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import re
from hashlib import sha256
from pathlib import Path
import os
import getpass
import subprocess
import time
from threading import Lock
from typing import Any, Protocol, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ValidationError

from app.core.config import CloudInferenceSettings
from app.core.config import load_cloud_inference_settings
from app.schemas.research import AgentRole
from app.services.model_config import APPROVED_CLOUD_MODELS, AgentModelAssignment, BlindspotModelConfig, InferenceProvider
from app.services.ollama_client import InferenceMetrics


T = TypeVar("T", bound=BaseModel)
_CLOUD_INFERENCE_LOCK = Lock()
_SYNTHETIC_DIAGNOSTIC_MARKER = "BLINDSPOT_SYNTHETIC_JSON_DIAGNOSTIC_V1"
_SYNTHETIC_CAPTURE_ROOT = Path(__file__).resolve().parents[3] / "tmp" / "gemini-synthetic-diagnostics"
_ALLOWED_PROVIDER_CODES = frozenset({"model_not_found", "model_decommissioned", "model_terms_required", "model_permission_blocked_org", "model_permission_blocked_project", "json_validate_failed", "invalid_api_key", "rate_limit_exceeded"})
_ALLOWED_GOOGLE_STATUSES = frozenset({"INVALID_ARGUMENT", "FAILED_PRECONDITION", "RESOURCE_EXHAUSTED", "PERMISSION_DENIED", "UNAUTHENTICATED", "NOT_FOUND", "INTERNAL", "UNAVAILABLE"})
_ALLOWED_GOOGLE_REASONS = frozenset({"API_KEY_INVALID", "RATE_LIMIT_EXCEEDED", "SERVICE_DISABLED", "MODEL_NOT_FOUND", "SCHEMA_VALIDATION_FAILED"})
# Never persist a provider-supplied arbitrary path; only exact known request paths.
_ALLOWED_ERROR_FIELDS = frozenset({
    "contents", "contents[0].parts", "contents[0].parts[0].text", "system_instruction",
    "generation_config", "generation_config.response_mime_type", "generation_config.response_json_schema",
    "generation_config.response_json_schema.properties.claims", "generation_config.response_json_schema.$defs.ClaimCandidateV3",
    "generation_config.thinking_config", "generation_config.thinking_config.thinking_level", "generation_config.max_output_tokens",
    "generationConfig", "generationConfig.responseMimeType", "generationConfig.responseJsonSchema",
    "generationConfig.responseJsonSchema.properties.claims", "generationConfig.responseJsonSchema.$defs.ClaimCandidateV3",
    "generationConfig.thinkingConfig", "generationConfig.thinkingConfig.thinkingLevel", "generationConfig.maxOutputTokens",
})


def _safe_http_diagnostics(status: int, details: Any) -> dict[str, Any]:
    """Keep only recognized status, reason, and exact static request paths."""
    result: dict[str, Any] = {"http_status": status}
    if not isinstance(details, dict):
        return result
    code = details.get("code")
    if isinstance(code, str) and code in _ALLOWED_PROVIDER_CODES:
        result["provider_error_code"] = code
    provider_status = details.get("status")
    if isinstance(provider_status, str) and provider_status in _ALLOWED_GOOGLE_STATUSES:
        result["provider_status"] = provider_status
    items = details.get("details")
    if not isinstance(items, list):
        return result
    fields, reasons = set(), set()
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        kind = item.get("@type")
        if kind == "type.googleapis.com/google.rpc.BadRequest":
            violations = item.get("fieldViolations")
            if isinstance(violations, list):
                for violation in violations[:20]:
                    field = violation.get("field") if isinstance(violation, dict) else None
                    if isinstance(field, str) and field in _ALLOWED_ERROR_FIELDS:
                        fields.add(field)
        elif kind == "type.googleapis.com/google.rpc.ErrorInfo":
            reason = item.get("reason")
            if isinstance(reason, str) and reason in _ALLOWED_GOOGLE_REASONS:
                reasons.add(reason)
    if fields: result["invalid_fields"] = sorted(fields)
    if reasons: result["provider_reasons"] = sorted(reasons)
    return result


def _local_error_hints(details: Any) -> tuple[str, ...]:
    """Classify bounded free-text locally; return fixed hints, never provider text.

    Hints are not proof of the cause and are not attached to persisted errors.
    """
    if not isinstance(details, dict): return ()
    messages = [details.get('message')]
    for item in (details.get('details') if isinstance(details.get('details'), list) else [])[:20]:
        if isinstance(item, dict) and isinstance(item.get('fieldViolations'), list):
            messages.extend(v.get('description') for v in item['fieldViolations'][:20] if isinstance(v, dict))
    text = ' '.join(m[:2048] for m in messages if isinstance(m, str)).lower()
    hints = []
    if re.search(r'(schema.{0,80}(too complex|complexity|state limit|too many states))|((too complex|complexity|state limit).{0,80}schema)', text): hints.append('schema_complexity_mentioned')
    if re.search(r'unknown (name|field|argument)|unrecognized (field|argument)', text): hints.append('unknown_argument_mentioned')
    if re.search(r'unsupported (field|argument|keyword|schema)', text): hints.append('unsupported_argument_mentioned')
    if re.search(r'invalid (json )?schema', text): hints.append('invalid_schema_mentioned')
    return tuple(hints)


class CloudInferenceError(RuntimeError): pass
class CloudAuthenticationError(CloudInferenceError):
    def __init__(self, code: str):
        if code not in {"authentication_invalid", "permission_denied"}:
            raise ValueError("Invalid authentication error code.")
        self.code = code
        super().__init__(code)
class CloudQuotaError(CloudInferenceError): pass
class CloudHTTPError(CloudInferenceError): pass
class CloudTimeoutError(CloudInferenceError): pass
class CloudResponseError(CloudInferenceError):
    def __init__(self, code: str, diagnostics: dict | None = None):
        super().__init__(code)
        self.code = code
        self.diagnostics = diagnostics or {}
class CloudConfigurationError(CloudInferenceError): pass
class CloudInputLimitError(CloudInferenceError): pass


@dataclass(frozen=True)
class SyntheticGeminiResponseCapture:
    """Explicitly opt-in local capture for one harmless synthetic request only."""
    label: str
    directory: Path = _SYNTHETIC_CAPTURE_ROOT
    max_text_bytes: int = 8192

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", self.label):
            raise CloudConfigurationError("Synthetic diagnostic label is invalid.")
        if type(self.max_text_bytes) is not int or not 1 <= self.max_text_bytes <= 16384:
            raise CloudConfigurationError("Synthetic capture size limit is invalid.")
        try:
            root = _SYNTHETIC_CAPTURE_ROOT.resolve()
            target = self.directory.resolve()
        except OSError as error:
            raise CloudConfigurationError("Synthetic capture location is unavailable.") from error
        if target != root:
            raise CloudConfigurationError("Synthetic capture must use the local diagnostic directory.")

    def capture(self, text: str) -> str:
        encoded = text.encode("utf-8")
        if len(encoded) > self.max_text_bytes:
            raise CloudConfigurationError("Synthetic candidate text exceeds the capture limit.")
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = self.directory / f"{self.label}.candidate.txt"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            os.chmod(path, 0o600)
            if os.name == "nt":
                user = getpass.getuser()
                acl = subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(R,W)"],
                    capture_output=True, text=True, check=False,
                )
                if acl.returncode != 0:
                    path.unlink(missing_ok=True)
                    raise CloudConfigurationError("Synthetic diagnostic text could not be access-restricted.")
            return str(path)
        except FileExistsError as error:
            raise CloudConfigurationError("Synthetic diagnostic capture label was already used.") from error
        except OSError as error:
            raise CloudConfigurationError("Synthetic diagnostic text could not be stored safely.") from error


def synthetic_prompt_json_diagnostic_prompt() -> str:
    """Harmless input for a separately approved one-request transport diagnostic."""
    return (
        f"{_SYNTHETIC_DIAGNOSTIC_MARKER}\n"
        "Synthetic evidence only. Evidence ID ev_synthetic_01 states: "
        '"The imaginary experiment used 12 samples."\n'
        "Return exactly one JSON object and nothing else: no Markdown fences, prose, or commentary. "
        "Return a V3-shaped abstention object with an empty claims array."
    )


class StructuredInferenceProvider(Protocol):
    def generate_structured(self, prompt: str, schema: type[T], *, model: str, output_tokens: int, input_tokens: int = 4096, context_tokens: int = 4096) -> tuple[T, InferenceMetrics]: ...


def cloud_request_payload(assignment: AgentModelAssignment, prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
    """Build the very same provider body used for admission and generation."""
    assignment.require_approved_cloud_model()
    if assignment.provider is InferenceProvider.GEMINI:
        if assignment.thinking_level not in {"MINIMAL", "LOW", "MEDIUM", "HIGH"}:
            raise CloudConfigurationError("Unsupported Gemini thinking level.")
        config = {"maxOutputTokens": assignment.output_tokens, "thinkingConfig": {"thinkingLevel": assignment.thinking_level}}
        if assignment.gemini_response_mode == 'native_schema':
            config.update(responseMimeType='application/json', responseJsonSchema=schema.model_json_schema())
        elif assignment.gemini_response_mode == 'prompt_json':
            prompt += '\nReturn only strict JSON conforming to this schema: ' + json.dumps(schema.model_json_schema(), separators=(',', ':'))
        elif assignment.gemini_response_mode == 'plain_text':
            # The V3 prompt itself supplies the exact plain-text contract.
            # No native JSON response mode or schema is sent to Gemini.
            pass
        else:
            raise CloudConfigurationError('Unsupported Gemini response mode.')
        return {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config}
    if assignment.provider is InferenceProvider.GROQ:
        return {"model": assignment.model_tag, "messages": [{"role": "user", "content": prompt}], "max_tokens": assignment.output_tokens, "response_format": {"type": "json_schema", "json_schema": {"name": "blindspot_response", "schema": schema.model_json_schema()}}}
    if assignment.provider is InferenceProvider.OPENROUTER:
        # This pinned free route twice exhausted its bounded completion budget
        # on a V3 extraction. Limit only this extraction contract to one compact
        # claim; the local schema and evidence gates remain unchanged.
        if schema.__name__ == "ClaimExtractionResponseV3":
            prompt += ("\nOpenRouter output constraint: return one compact JSON object only, with at most one "
                       "concise directly stated scientific claim. If no claim is supported, return exactly {\"claims\":[]}. "
                       "Do not include reasoning, Markdown, repeated evidence, or extra fields.")
        return {"model": assignment.model_tag, "messages": [{"role": "user", "content": prompt}], "max_tokens": assignment.output_tokens, "response_format": {"type": "json_object"}}
    raise CloudConfigurationError("Cloud request requires an approved cloud provider.")


def cloud_request_size(assignment: AgentModelAssignment, prompt: str, schema: type[BaseModel]) -> int:
    return len(json.dumps(cloud_request_payload(assignment, prompt, schema)).encode("utf-8"))


@dataclass
class CloudBudget:
    max_requests: int = 20
    max_output_tokens_total: int = 4096
    max_input_bytes_total: int = 81920
    requests_used: int = 0
    output_tokens_used: int = 0
    input_bytes_reserved: int = 0

    def __post_init__(self) -> None:
        if any(type(value) is not int or value <= 0 for value in (self.max_requests, self.max_output_tokens_total, self.max_input_bytes_total)):
            raise CloudConfigurationError("Cloud budget limits must be positive integers.")

    def reserve(self, output_tokens: int, input_bytes: int) -> None:
        if self.requests_used >= self.max_requests or self.output_tokens_used + output_tokens > self.max_output_tokens_total or self.input_bytes_reserved + input_bytes > self.max_input_bytes_total:
            raise CloudQuotaError("Local cloud request, input-byte, or output-token budget is exhausted.")
        self.requests_used += 1
        self.output_tokens_used += output_tokens
        self.input_bytes_reserved += input_bytes


class _CloudClient:
    def __init__(self, *, api_key: str, endpoint: str, timeout_seconds: float = 30.0, budget: CloudBudget | None = None, request_fn=None, diagnostic_callback=None) -> None:
        if not isinstance(api_key, str) or not api_key or any(ch.isspace() or ch in "\"'" or ord(ch) < 32 or ord(ch) > 126 for ch in api_key):
            raise CloudConfigurationError("Cloud API key is missing or malformed.")
        self._api_key, self._endpoint, self._timeout = api_key, endpoint, timeout_seconds
        self._budget, self._request_fn = budget or CloudBudget(), request_fn or self._request
        self._raw_hash = None
        self._diagnostic_callback = diagnostic_callback

    def _admit(self, payload: dict[str, Any], input_tokens: int, output_tokens: int, context_tokens: int) -> None:
        # Count the exact serialized request body, not just the user passage.
        if any(type(value) is not int or value <= 0 for value in (input_tokens, output_tokens, context_tokens)) or input_tokens > 4096 or output_tokens > 512 or context_tokens > 4096:
            raise CloudConfigurationError("Invalid cloud input, output, or local context limit.")
        try:
            size = len(json.dumps(payload).encode("utf-8"))
        except (TypeError, ValueError, UnicodeError) as error:
            raise CloudConfigurationError("Cloud request cannot be safely serialized.") from error
        if size > input_tokens or size + output_tokens > context_tokens:
            raise CloudInputLimitError("Cloud request exceeds the local input or combined context admission limit.")
        self._budget.reserve(output_tokens, size)

    def _request(self, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        request = Request(self._endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self._timeout) as response:  # nosec B310: configured provider HTTPS endpoint
                raw = response.read()
                self._raw_hash = sha256(raw).hexdigest()
                return json.loads(raw.decode("utf-8"))
        except HTTPError as error:
            diagnostics = {"http_status": error.code}
            try:
                raw_error = error.read(16385)
                if len(raw_error) <= 16384:
                    parsed_error = json.loads(raw_error.decode("utf-8"))
                    details = parsed_error.get("error") if isinstance(parsed_error, dict) else None
                    diagnostics = _safe_http_diagnostics(error.code, details)
                    if self._diagnostic_callback is not None:
                        hints = _local_error_hints(details)
                        try: self._diagnostic_callback(hints)
                        except Exception: pass
            except (OSError, ValueError, TypeError, UnicodeError):
                pass
            if error.code in {401, 403}:
                failure = CloudAuthenticationError("authentication_invalid" if error.code == 401 else "permission_denied")
            elif error.code == 429:
                failure = CloudQuotaError("Cloud provider quota or rate limit was reached.")
            else:
                category = {400: "invalid_request", 422: "unprocessable_request"}.get(error.code, "provider_http_error")
                failure = CloudHTTPError(category)
            failure.http_diagnostics = diagnostics
            raise failure from None
        except TimeoutError as error:
            raise CloudTimeoutError("Cloud inference timed out.") from error
        except URLError as error:
            raise CloudInferenceError("Cloud provider is unavailable.") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CloudResponseError("unsupported_provider_response", {"response_status": "received", "json": "not_checked", "schema": "not_checked", "raw_response_hash": self._raw_hash}) from None

    @staticmethod
    def _parse(content: Any, schema: type[T], diagnostics: dict) -> T:
        if content is None:
            raise CloudResponseError("missing_response_content", diagnostics)
        if not isinstance(content, str):
            raise CloudResponseError("unsupported_provider_response", diagnostics)
        if not content.strip():
            raise CloudResponseError("empty_response", diagnostics)
        try:
            def reject_constant(_value): raise ValueError('Non-standard JSON constant')
            def unique_pairs(pairs):
                result = {}
                for key, item in pairs:
                    if key in result: raise ValueError('Duplicate JSON key')
                    result[key] = item
                return result
            value = json.loads(content, parse_constant=reject_constant, object_pairs_hook=unique_pairs)
        except (ValueError, RecursionError):
            diagnostics["json"] = "invalid"
            raise CloudResponseError("invalid_json", diagnostics) from None
        diagnostics["json"] = "valid"
        try:
            result = schema.model_validate(value)
        except ValidationError as error:
            types = {item["type"] for item in error.errors(include_input=False, include_url=False, include_context=False)}
            code = "missing_required_fields" if "missing" in types else ("invalid_structure" if types & {"model_type", "model_attributes_type", "list_type", "dict_type"} else ("invalid_field_value" if any(t.endswith("_type") or t in {"enum", "literal_error", "string_too_short"} for t in types) else "invalid_structure"))
            diagnostics["schema"] = "invalid"
            raise CloudResponseError(code, diagnostics) from None
        diagnostics["schema"] = "valid"
        return result

    def _interpret(self, response: Any, provider: str, schema: type[T], elapsed: float, capture: SyntheticGeminiResponseCapture | None = None, plain_text_parser=None) -> tuple[T, InferenceMetrics]:
        diagnostics = {"response_status": "received", "json": "not_checked", "schema": "not_checked", "raw_response_hash": self._raw_hash}
        if not isinstance(response, dict):
            raise CloudResponseError("unsupported_provider_response", diagnostics)
        usage = response.get("usageMetadata" if provider == "gemini" else "usage")
        usage = usage if isinstance(usage, dict) else {}
        for target, key in (("input_tokens", "promptTokenCount" if provider == "gemini" else "prompt_tokens"), ("output_tokens", "candidatesTokenCount" if provider == "gemini" else "completion_tokens")):
            value = usage.get(key)
            if type(value) is int and 0 <= value <= 100000000: diagnostics[target] = value
        if provider == "gemini":
            for target, key in (("thinking_tokens", "thoughtsTokenCount"), ("total_tokens", "totalTokenCount")):
                value = usage.get(key)
                if type(value) is int and 0 <= value <= 100000000: diagnostics[target] = value
        choices = response.get("candidates" if provider == "gemini" else "choices")
        block = response.get("promptFeedback") if provider == "gemini" else None
        if isinstance(block, dict) and block.get("blockReason"):
            raise CloudResponseError("provider_refusal", diagnostics)
        if not isinstance(choices, list):
            raise CloudResponseError("unsupported_provider_response", diagnostics)
        if not choices:
            raise CloudResponseError("empty_response", diagnostics)
        if provider == "gemini":
            diagnostics["candidate_count"] = len(choices)
        choice = choices[0]
        if not isinstance(choice, dict): raise CloudResponseError("unsupported_provider_response", diagnostics)
        finish = choice.get("finishReason" if provider == "gemini" else "finish_reason")
        allowed = {"STOP", "MAX_TOKENS", "SAFETY", "RECITATION", "OTHER", "stop", "length", "content_filter"}
        if not isinstance(finish, str): finish = None
        if finish in allowed: diagnostics["finish_reason"] = finish
        if finish in {"MAX_TOKENS", "length"}: raise CloudResponseError("output_truncated", diagnostics)
        if finish in {"SAFETY", "RECITATION", "content_filter"}: raise CloudResponseError("provider_refusal", diagnostics)
        if provider == "gemini":
            content_obj = choice.get("content")
            parts = content_obj.get("parts") if isinstance(content_obj, dict) else None
            if isinstance(parts, list):
                diagnostics["candidate_part_count"] = len(parts)
                part_types = {"text": 0, "non_text": 0, "invalid_part": 0}
                for part in parts:
                    kind = "text" if isinstance(part, dict) and isinstance(part.get("text"), str) else "non_text" if isinstance(part, dict) else "invalid_part"
                    part_types[kind] += 1
                diagnostics["candidate_part_types"] = {kind: count for kind, count in part_types.items() if count}
                # A candidate may be split across ordered text parts. Assemble
                # only that selected candidate; never merge candidates or mix
                # unsupported content such as function calls into JSON text.
                if not parts:
                    content = None
                elif part_types["non_text"] or part_types["invalid_part"]:
                    raise CloudResponseError("unsupported_provider_response", diagnostics)
                else:
                    content = "".join(part["text"] for part in parts)
            else:
                content = None
            if capture is not None:
                if not isinstance(content, str):
                    raise CloudResponseError("missing_response_content", diagnostics)
                diagnostics["synthetic_capture_path"] = capture.capture(content)
        else:
            message = choice.get("message")
            if isinstance(message, dict) and message.get("refusal"): raise CloudResponseError("provider_refusal", diagnostics)
            content = message.get("content") if isinstance(message, dict) else None
        result = plain_text_parser(content, schema, diagnostics) if plain_text_parser is not None else self._parse(content, schema, diagnostics)
        return result, InferenceMetrics(elapsed, diagnostics.get("output_tokens"), None, diagnostics)


class GeminiClient(_CloudClient):
    def __init__(self, *, api_key: str, timeout_seconds: float = 30.0, budget: CloudBudget | None = None, request_fn=None, diagnostic_callback=None) -> None:
        super().__init__(api_key=api_key, endpoint="https://generativelanguage.googleapis.com/v1beta/models", timeout_seconds=timeout_seconds, budget=budget, request_fn=request_fn, diagnostic_callback=diagnostic_callback)

    def generate_structured(self, prompt: str, schema: type[T], *, model: str, output_tokens: int, input_tokens: int = 4096, context_tokens: int = 4096, thinking_level: str = "MINIMAL", response_mode: str = 'native_schema', synthetic_capture: SyntheticGeminiResponseCapture | None = None, plain_text_parser=None) -> tuple[T, InferenceMetrics]:
        _require_approved(InferenceProvider.GEMINI, model)
        if thinking_level not in {"MINIMAL", "LOW", "MEDIUM", "HIGH"}:
            raise CloudConfigurationError("Unsupported Gemini thinking level.")
        if synthetic_capture is not None and (response_mode != "prompt_json" or not prompt.startswith(_SYNTHETIC_DIAGNOSTIC_MARKER)):
            raise CloudConfigurationError("Synthetic response capture is prohibited for non-synthetic or native-schema requests.")
        assignment = AgentModelAssignment.model_construct(provider=InferenceProvider.GEMINI, model_tag=model, output_tokens=output_tokens, input_tokens=input_tokens, context_tokens=context_tokens, thinking_level=thinking_level, gemini_response_mode=response_mode)
        payload = cloud_request_payload(assignment, prompt, schema)
        self._admit(payload, input_tokens, output_tokens, context_tokens)
        endpoint = self._endpoint
        self._endpoint = f"{endpoint}/{model}:generateContent"
        started = time.perf_counter()
        self._raw_hash = None
        try:
            try:
                response = self._request_fn(payload, {"Content-Type": "application/json", "x-goog-api-key": self._api_key})
            except CloudInferenceError:
                raise
            except Exception:
                raise CloudResponseError("unexpected_transport_failure") from None
        finally:
            self._endpoint = endpoint
        elapsed = round(time.perf_counter() - started, 3)
        return self._interpret(response, "gemini", schema, elapsed, synthetic_capture, plain_text_parser)


class GroqClient(_CloudClient):
    def __init__(self, *, api_key: str, timeout_seconds: float = 30.0, budget: CloudBudget | None = None, request_fn=None) -> None:
        super().__init__(api_key=api_key, endpoint="https://api.groq.com/openai/v1/chat/completions", timeout_seconds=timeout_seconds, budget=budget, request_fn=request_fn)

    def generate_structured(self, prompt: str, schema: type[T], *, model: str, output_tokens: int, input_tokens: int = 4096, context_tokens: int = 4096) -> tuple[T, InferenceMetrics]:
        _require_approved(InferenceProvider.GROQ, model)
        assignment = AgentModelAssignment.model_construct(provider=InferenceProvider.GROQ, model_tag=model, output_tokens=output_tokens, input_tokens=input_tokens, context_tokens=context_tokens)
        payload = cloud_request_payload(assignment, prompt, schema)
        self._admit(payload, input_tokens, output_tokens, context_tokens)
        started = time.perf_counter()
        self._raw_hash = None
        try:
            response = self._request_fn(payload, {"Content-Type": "application/json", "Authorization": f"Bearer {self._api_key}"})
        except CloudInferenceError:
            raise
        except Exception:
            raise CloudResponseError("unexpected_transport_failure") from None
        elapsed = round(time.perf_counter() - started, 3)
        return self._interpret(response, "groq", schema, elapsed)


class OpenRouterClient(_CloudClient):
    """Pinned free-model client that checks current catalog metadata first."""
    _MODEL_CATALOG_ENDPOINT = "https://openrouter.ai/api/v1/models"

    def __init__(self, *, api_key: str, timeout_seconds: float = 30.0, budget: CloudBudget | None = None, request_fn=None, metadata_fn=None) -> None:
        super().__init__(api_key=api_key, endpoint="https://openrouter.ai/api/v1/chat/completions", timeout_seconds=timeout_seconds, budget=budget, request_fn=request_fn)
        self._metadata_fn = metadata_fn or self._model_metadata

    @staticmethod
    def _zero_price(value: Any) -> bool:
        try:
            return Decimal(str(value)) == Decimal("0")
        except (InvalidOperation, ValueError):
            return False

    def _model_metadata(self, model: str) -> dict[str, Any] | None:
        request = Request(self._MODEL_CATALOG_ENDPOINT, headers={"Authorization": f"Bearer {self._api_key}"}, method="GET")
        try:
            with urlopen(request, timeout=self._timeout) as response:  # nosec B310: fixed HTTPS catalog endpoint
                parsed = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise CloudConfigurationError("OpenRouter free-model metadata is unavailable.") from None
        records = parsed.get("data") if isinstance(parsed, dict) else None
        if not isinstance(records, list):
            raise CloudConfigurationError("OpenRouter free-model metadata is unavailable.")
        for record in records:
            if isinstance(record, dict) and record.get("id") == model:
                return record
        return None

    def _verify_free_model(self, model: str) -> None:
        try:
            record = self._metadata_fn(model)
        except CloudConfigurationError:
            raise
        except Exception:
            raise CloudConfigurationError("OpenRouter free-model metadata is unavailable.") from None
        if not isinstance(record, dict):
            raise CloudConfigurationError("OpenRouter model availability or zero pricing cannot be verified.")
        pricing, parameters = record.get("pricing"), record.get("supported_parameters")
        if not isinstance(pricing, dict) or not self._zero_price(pricing.get("prompt")) or not self._zero_price(pricing.get("completion")):
            raise CloudConfigurationError("OpenRouter model availability or zero pricing cannot be verified.")
        if not isinstance(parameters, list) or "response_format" not in parameters:
            raise CloudConfigurationError("OpenRouter model availability or JSON mode cannot be verified.")

    def generate_structured(self, prompt: str, schema: type[T], *, model: str, output_tokens: int, input_tokens: int = 4096, context_tokens: int = 4096) -> tuple[T, InferenceMetrics]:
        _require_approved(InferenceProvider.OPENROUTER, model)
        self._verify_free_model(model)
        assignment = AgentModelAssignment.model_construct(provider=InferenceProvider.OPENROUTER, model_tag=model, output_tokens=output_tokens, input_tokens=input_tokens, context_tokens=context_tokens)
        payload = cloud_request_payload(assignment, prompt, schema)
        self._admit(payload, input_tokens, output_tokens, context_tokens)
        started = time.perf_counter(); self._raw_hash = None
        try:
            response = self._request_fn(payload, {"Content-Type": "application/json", "Authorization": f"Bearer {self._api_key}"})
        except CloudInferenceError:
            raise
        except Exception:
            raise CloudResponseError("unexpected_transport_failure") from None
        return self._interpret(response, "openrouter", schema, round(time.perf_counter() - started, 3))


def _require_approved(provider: InferenceProvider, model: str) -> None:
    if model not in APPROVED_CLOUD_MODELS.get(provider, set()):
        raise CloudConfigurationError("Provider/model configuration is not approved.")


class CloudInferenceRouter:
    """Routes only the selected approved cloud provider; it never falls back to Ollama."""
    def __init__(self, config: BlindspotModelConfig, settings: CloudInferenceSettings, *, gemini: GeminiClient | None = None, groq: GroqClient | None = None, openrouter: OpenRouterClient | None = None) -> None:
        self._config = config
        self._clients = {InferenceProvider.GEMINI: gemini or GeminiClient(api_key=settings.gemini_api_key), InferenceProvider.GROQ: groq or GroqClient(api_key=settings.groq_api_key)}
        if openrouter is not None:
            self._clients[InferenceProvider.OPENROUTER] = openrouter
        elif settings.openrouter_api_key:
            self._clients[InferenceProvider.OPENROUTER] = OpenRouterClient(api_key=settings.openrouter_api_key)

    @classmethod
    def from_environment(cls, config: BlindspotModelConfig) -> "CloudInferenceRouter":
        """Normal application entry point; enforces documented free-tier confirmation."""
        return cls(config, load_cloud_inference_settings())

    def generate(self, role: AgentRole, prompt: str, schema: type[T]) -> tuple[T, InferenceMetrics]:
        assignment = self._config.assignment_for(role)
        if assignment is None or assignment.provider is InferenceProvider.OLLAMA:
            raise CloudConfigurationError("Cloud router requires an explicit approved cloud provider; no Ollama fallback exists.")
        assignment.require_approved_cloud_model()
        client = self._clients.get(assignment.provider)
        if client is None:
            raise CloudConfigurationError("Selected cloud provider is unavailable.")
        # One request at a time across the process, including future cloud agents.
        with _CLOUD_INFERENCE_LOCK:
            options = {"model": assignment.model_tag, "output_tokens": assignment.output_tokens, "input_tokens": assignment.input_tokens, "context_tokens": assignment.context_tokens}
            if assignment.provider is InferenceProvider.GEMINI:
                options["thinking_level"] = assignment.thinking_level
                if assignment.gemini_response_mode != 'native_schema': options['response_mode'] = assignment.gemini_response_mode
                if assignment.gemini_response_mode == 'plain_text':
                    # Local import avoids a module cycle; this mode is exclusive
                    # to the Claim Extraction V3 contract.
                    if schema.__name__ != 'ClaimExtractionResponseV3':
                        raise CloudConfigurationError("Gemini plain-text mode is available only for Claim Extraction V3.")
                    from app.services.claim_extraction_v3 import parse_gemini_plain_text_response
                    options['plain_text_parser'] = parse_gemini_plain_text_response
            return client.generate_structured(prompt, schema, **options)
