import json
from threading import Thread
import time
from urllib.error import URLError

import pytest

from app.services.ollama_benchmark import PassageClaim
from app.services.ollama_client import OllamaClient, OllamaResponseError, OllamaTimeoutError, OllamaUnavailableError


def valid_response(chunk_id="chunk-1"):
    return {"response": json.dumps({"research_finding": "A stated finding.", "claim_type": "result", "supporting_quotation": "A stated finding.", "source_chunk_identifier": chunk_id, "uncertainty_or_missing_information": "Only this passage was reviewed."}), "eval_count": 10, "eval_duration": 2_000_000_000}


def test_valid_structured_response_and_bounded_context() -> None:
    observed = {}
    def request(payload, _timeout): observed.update(payload); return valid_response()
    claim, metrics = OllamaClient(request_fn=request).generate_structured("test", PassageClaim)
    assert claim.claim_type == "result" and metrics.tokens_per_second == 5.0
    assert observed["options"] == {"num_ctx": 4096, "num_predict": 256} and observed["keep_alive"] == "0"
    with pytest.raises(ValueError): OllamaClient(context_tokens=8192)


def test_invalid_json_schema_failure_unavailable_and_timeout_are_safe() -> None:
    with pytest.raises(OllamaResponseError): OllamaClient(request_fn=lambda *_: {"response": "not json"}).generate_structured("x", PassageClaim)
    with pytest.raises(OllamaResponseError): OllamaClient(request_fn=lambda *_: {"response": "{}"}).generate_structured("x", PassageClaim)
    with pytest.raises(OllamaUnavailableError): OllamaClient(request_fn=lambda *_: (_ for _ in ()).throw(URLError("down"))).generate_structured("x", PassageClaim)
    with pytest.raises(OllamaTimeoutError): OllamaClient(request_fn=lambda *_: (_ for _ in ()).throw(TimeoutError())).generate_structured("x", PassageClaim)


def test_global_inference_lock_serializes_requests() -> None:
    active = 0; peak = 0
    def request(*_):
        nonlocal active, peak
        active += 1; peak = max(peak, active); time.sleep(0.03); active -= 1
        return valid_response()
    client = OllamaClient(request_fn=request)
    threads = [Thread(target=lambda: client.generate_structured("x", PassageClaim)) for _ in range(2)]
    [thread.start() for thread in threads]; [thread.join() for thread in threads]
    assert peak == 1
