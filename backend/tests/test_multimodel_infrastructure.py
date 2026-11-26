import json

from sqlalchemy import select

from app.models.research import ModelRun, ResearchJob
from app.schemas.research import AgentRole, ModelRunStatus
from app.services.model_config import AgentModelAssignment, BlindspotModelConfig, InferenceProvider, SequentialModelRunner
from app.services.model_runs import ModelRunRecorder
from app.services.ollama_benchmark import PassageClaim
from app.services.ollama_client import InferenceMetrics, OllamaClient


def response():
    return {"response": json.dumps({"research_finding": "finding", "claim_type": "result", "supporting_quotation": "finding", "source_chunk_identifier": "chunk", "uncertainty_or_missing_information": "unknown"})}


def test_independent_and_shared_assignments_with_explicit_unload() -> None:
    calls = []
    client = OllamaClient(request_fn=lambda payload, _timeout: (calls.append(payload) or response()))
    config = BlindspotModelConfig(claim_extraction=AgentModelAssignment(model_tag="small:latest", output_tokens=64), gap_detection=AgentModelAssignment(model_tag="large:latest", output_tokens=128))
    runner = SequentialModelRunner(client, config)
    runner.generate(AgentRole.CLAIM_EXTRACTION, "x", PassageClaim)
    runner.generate(AgentRole.GAP_DETECTION, "x", PassageClaim)
    assert calls[0]["model"] == "small:latest" and calls[0]["options"]["num_predict"] == 64
    assert calls[1] == {"model": "small:latest", "keep_alive": 0}
    assert calls[2]["model"] == "large:latest" and calls[2]["options"]["num_predict"] == 128
    defaults = BlindspotModelConfig()
    assert defaults.claim_extraction.provider is InferenceProvider.GEMINI
    assert defaults.gap_detection.provider is InferenceProvider.GROQ


def test_model_run_success_and_failure_persistence(session) -> None:
    job = ResearchJob(topic="Model run provenance")
    session.add(job); session.commit()
    assignment = AgentModelAssignment(model_tag="llama3.1:8b-instruct-q4_K_M")
    recorder = ModelRunRecorder()
    successful = recorder.start(session, research_job=job, role=AgentRole.CLAIM_EXTRACTION, assignment=assignment, input_reference="chunk-id")
    recorder.succeed(successful, output='{"safe": true}', metrics=InferenceMetrics(1.2, 12, 10.0), available_before_mb=8000, available_minimum_mb=7000, available_after_mb=7500)
    failed = recorder.start(session, research_job=None, role=AgentRole.GAP_DETECTION, assignment=assignment, input_reference=None)
    recorder.fail(failed, RuntimeError("model unavailable"))
    session.commit()
    runs = session.scalars(select(ModelRun).order_by(ModelRun.created_at)).all()
    assert runs[0].status is ModelRunStatus.SUCCESS and runs[0].output_hash and runs[0].input_reference_hash
    assert runs[0].model_config["context_tokens"] == 4096 and runs[0].available_memory_minimum_mb == 7000
    assert runs[1].status is ModelRunStatus.FAILED and runs[1].error_category == "RuntimeError" and runs[1].output_hash is None
