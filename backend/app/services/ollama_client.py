"""Minimal sequential Ollama client for bounded local structured inference."""
from __future__ import annotations

from dataclasses import dataclass
import json
from threading import Lock
import time
from typing import Any, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ValidationError


MODEL_TAG = "llama3.1:8b-instruct-q4_K_M"
T = TypeVar("T", bound=BaseModel)
_INFERENCE_LOCK = Lock()


class OllamaError(RuntimeError): pass
class OllamaUnavailableError(OllamaError): pass
class OllamaModelNotFoundError(OllamaError): pass
class OllamaTimeoutError(OllamaError): pass
class OllamaResponseError(OllamaError): pass


@dataclass(frozen=True)
class InferenceMetrics:
    elapsed_seconds: float
    output_tokens: int | None
    tokens_per_second: float | None
    diagnostics: dict | None = None


class OllamaClient:
    def __init__(self, *, base_url: str = "http://127.0.0.1:11434", model: str = MODEL_TAG, connect_timeout: float = 10.0, inference_timeout: float = 180.0, context_tokens: int = 4096, output_tokens: int = 256, request_fn=None) -> None:
        if context_tokens != 4096 or not 1 <= output_tokens <= 512:
            raise ValueError("Phase 4A requires exactly 4096 context tokens and a bounded output limit.")
        self._base_url, self._model = base_url.rstrip("/"), model
        self._connect_timeout, self._inference_timeout = connect_timeout, inference_timeout
        self._context_tokens, self._output_tokens = context_tokens, output_tokens
        self._request_fn = request_fn or self._request

    def generate_structured(self, prompt: str, schema: type[T], *, model: str | None = None, output_tokens: int | None = None) -> tuple[T, InferenceMetrics]:
        selected_model = self._validate_model_tag(model or self._model)
        selected_output = self._output_tokens if output_tokens is None else output_tokens
        if not 1 <= selected_output <= 512:
            raise ValueError("output_tokens must be between 1 and 512.")
        payload = {
            "model": selected_model,
            "prompt": prompt,
            "stream": False,
            "format": schema.model_json_schema(),
            "options": {"num_ctx": self._context_tokens, "num_predict": selected_output},
            "keep_alive": "0",
        }
        started = time.perf_counter()
        with _INFERENCE_LOCK:
            try:
                response = self._request_fn(payload, self._inference_timeout)
            except TimeoutError as error:
                raise OllamaTimeoutError("Ollama inference timed out.") from error
            except URLError as error:
                raise OllamaUnavailableError("Ollama local API is unavailable.") from error
        elapsed = time.perf_counter() - started
        content = response.get("response") if isinstance(response, dict) else None
        if not isinstance(content, str):
            raise OllamaResponseError("Ollama response lacks structured content.")
        try:
            parsed = schema.model_validate_json(content)
        except (ValidationError, ValueError) as error:
            raise OllamaResponseError("Ollama response is not valid for the requested schema.") from error
        tokens = response.get("eval_count") if isinstance(response.get("eval_count"), int) else None
        duration_ns = response.get("eval_duration") if isinstance(response.get("eval_duration"), int) else None
        rate = round(tokens / (duration_ns / 1_000_000_000), 3) if tokens is not None and duration_ns and duration_ns > 0 else None
        return parsed, InferenceMetrics(round(elapsed, 3), tokens, rate)

    def unload_model(self, model: str) -> None:
        """Explicitly request unloading before a different model assignment runs."""
        payload = {"model": self._validate_model_tag(model), "keep_alive": 0}
        with _INFERENCE_LOCK:
            try:
                self._request_fn(payload, self._connect_timeout)
            except (URLError, TimeoutError):
                # Unload failure is observable to callers only through later inference; never retry it.
                return

    @staticmethod
    def _validate_model_tag(value: str) -> str:
        value = value.strip()
        if not value or any(character.isspace() for character in value):
            raise ValueError("model must be a non-empty Ollama tag")
        return value

    def _request(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        request = Request(f"{self._base_url}/api/generate", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:  # nosec B310: fixed loopback API
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code == 404:
                raise OllamaModelNotFoundError("Configured Ollama model is unavailable.") from error
            raise OllamaUnavailableError("Ollama local API request failed.") from error
        except URLError:
            raise
        except TimeoutError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OllamaResponseError("Ollama returned malformed JSON.") from error
