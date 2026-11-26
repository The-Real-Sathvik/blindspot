"""Compact provenance recording for future agent-model calls; no raw prompts or outputs."""
from datetime import datetime, timezone
from hashlib import sha256
from typing import Optional

from sqlalchemy.orm import Session

from app.models.research import ModelRun, ResearchJob
from app.schemas.research import AgentRole, ModelRunStatus
from app.services.model_config import AgentModelAssignment
from app.services.ollama_client import InferenceMetrics
from app.services.cloud_inference import CloudAuthenticationError, CloudHTTPError, CloudInferenceError, CloudQuotaError, CloudResponseError


def safe_hash(value: str | None) -> Optional[str]:
    return sha256(value.encode("utf-8")).hexdigest() if value is not None else None


class ModelRunRecorder:
    def start(self, session: Session, *, research_job: ResearchJob | None, role: AgentRole, assignment: AgentModelAssignment, input_reference: str | None) -> ModelRun:
        run = ModelRun(research_job=research_job, agent_role=role, model_tag=assignment.model_tag, model_config=assignment.model_dump(), prompt_template_version=assignment.prompt_template_version, input_reference_hash=safe_hash(input_reference), started_at=datetime.now(timezone.utc), status=ModelRunStatus.FAILED)
        session.add(run); session.flush()
        return run

    def succeed(self, run: ModelRun, *, output: str, metrics: InferenceMetrics, available_before_mb: float | None = None, available_minimum_mb: float | None = None, available_after_mb: float | None = None) -> None:
        run.status = ModelRunStatus.SUCCESS
        run.completed_at = datetime.now(timezone.utc)
        run.output_hash = safe_hash(output)
        run.runtime_seconds, run.output_tokens, run.tokens_per_second = metrics.elapsed_seconds, metrics.output_tokens, metrics.tokens_per_second
        run.available_memory_before_mb, run.available_memory_minimum_mb, run.available_memory_after_mb = available_before_mb, available_minimum_mb, available_after_mb
        run.error_category, run.error_summary = None, None
        if metrics.diagnostics is not None:
            run.model_config = {**run.model_config, "response_diagnostics": metrics.diagnostics}

    def fail(self, run: ModelRun, error: Exception) -> None:
        run.status = ModelRunStatus.FAILED
        run.completed_at = datetime.now(timezone.utc)
        if isinstance(error, CloudAuthenticationError):
            run.error_category = error.code
            run.error_summary = "Cloud provider authentication or permission denied."
        elif isinstance(error, CloudHTTPError):
            run.error_category = str(error)
            run.error_summary = "Cloud provider HTTP request failed."
        elif isinstance(error, CloudResponseError):
            run.error_category = error.code[:64]
            run.error_summary = "Cloud response validation failed."
            run.model_config = {**run.model_config, "response_diagnostics": error.diagnostics}
            run.output_tokens = error.diagnostics.get("output_tokens")
        elif isinstance(error, CloudInferenceError):
            run.error_category = type(error).__name__[:64]
            run.error_summary = "Cloud inference failed."
        else:
            run.error_category = type(error).__name__[:64]
            run.error_summary = str(error)[:512]
        if isinstance(error, (CloudAuthenticationError, CloudHTTPError, CloudQuotaError)) and hasattr(error, "http_diagnostics"):
            run.model_config = {**run.model_config, "http_diagnostics": error.http_diagnostics}
