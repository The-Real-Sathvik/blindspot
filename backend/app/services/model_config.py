"""Validated independent provider/model assignments for future Blindspot agents."""
from enum import Enum
from typing import Literal
from app.schemas.research import AgentRole
from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.ollama_client import MODEL_TAG
from app.services.ollama_client import OllamaClient


class InferenceProvider(str, Enum):
    GEMINI = "gemini"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"


APPROVED_CLOUD_MODELS = {
    InferenceProvider.GEMINI: {"gemini-3.5-flash-lite"},
    InferenceProvider.GROQ: {"openai/gpt-oss-120b"},
    InferenceProvider.OPENROUTER: {"inclusionai/ling-3.2-flash:free"},
}


class AgentModelAssignment(BaseModel):
    provider: InferenceProvider = InferenceProvider.OLLAMA
    model_tag: str = MODEL_TAG
    context_tokens: int = Field(default=4096, frozen=True)
    input_tokens: int = Field(default=4096, ge=1, le=4096)
    output_tokens: int = Field(default=256, ge=1, le=512)
    thinking_level: str | None = None
    gemini_response_mode: Literal['native_schema', 'prompt_json', 'plain_text'] = 'native_schema'
    prompt_template_version: str = Field(default="v1", min_length=1, max_length=64)

    @field_validator("model_tag")
    @classmethod
    def require_model_tag(cls, value: str) -> str:
        value = value.strip()
        if not value or any(character.isspace() for character in value):
            raise ValueError("model_tag must be a non-empty provider model identifier")
        return value

    @field_validator("provider")
    @classmethod
    def require_known_provider(cls, value: InferenceProvider) -> InferenceProvider:
        return value

    def require_approved_cloud_model(self) -> "AgentModelAssignment":
        if self.provider in APPROVED_CLOUD_MODELS and self.model_tag not in APPROVED_CLOUD_MODELS[self.provider]:
            raise ValueError(f"{self.model_tag!r} is not approved for {self.provider.value}.")
        return self

    @model_validator(mode="after")
    def validate_cloud_allowlist(self) -> "AgentModelAssignment":
        """Cloud selections are rejected at configuration time; Ollama stays manual-only."""
        if self.provider is InferenceProvider.GEMINI:
            if self.thinking_level is None:
                self.thinking_level = "MINIMAL"
            if self.thinking_level not in {"MINIMAL", "LOW", "MEDIUM", "HIGH"}:
                raise ValueError("Unsupported Gemini thinking level.")
        elif self.thinking_level is not None:
            raise ValueError("Thinking level is not configured for this provider.")
        if self.provider is not InferenceProvider.GEMINI and self.gemini_response_mode != 'native_schema':
            raise ValueError('Prompt JSON mode is available only for Gemini.')
        return self.require_approved_cloud_model()


class BlindspotModelConfig(BaseModel):
    claim_extraction: AgentModelAssignment = Field(default_factory=lambda: AgentModelAssignment(provider=InferenceProvider.GEMINI, model_tag="gemini-3.5-flash-lite", output_tokens=256, prompt_template_version="claim-extraction-v2"))
    gap_detection: AgentModelAssignment = Field(default_factory=lambda: AgentModelAssignment(provider=InferenceProvider.GROQ, model_tag="openai/gpt-oss-120b", output_tokens=256, prompt_template_version="gap-detection-v1"))
    semantic_review: AgentModelAssignment | None = None

    def assignment_for(self, role: AgentRole) -> AgentModelAssignment | None:
        return getattr(self, role.value)


class SequentialModelRunner:
    """Configuration-aware model lifecycle only; it contains no agent reasoning."""
    def __init__(self, client: OllamaClient, config: BlindspotModelConfig) -> None:
        self._client, self._config, self._last_model = client, config, None

    def generate(self, role: AgentRole, prompt: str, schema):
        assignment = self._config.assignment_for(role)
        if assignment is None:
            raise ValueError(f"No model assignment configured for {role.value}.")
        if assignment.provider is not InferenceProvider.OLLAMA:
            raise ValueError("Ollama runner requires an explicit ollama assignment; cloud providers use CloudInferenceRouter.")
        if self._last_model and self._last_model != assignment.model_tag:
            self._client.unload_model(self._last_model)
        result = self._client.generate_structured(prompt, schema, model=assignment.model_tag, output_tokens=assignment.output_tokens)
        self._last_model = assignment.model_tag
        return result
