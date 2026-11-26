"""Configuration loaded from the project-root .env without exposing secrets."""
from dataclasses import dataclass, field
from pathlib import Path
import os

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


class ConfigurationError(RuntimeError):
    """Raised when required local configuration is unavailable."""


@dataclass(frozen=True)
class OpenAlexSettings:
    api_key: str = field(repr=False)
    timeout_seconds: float = 15.0
    max_retries: int = 3
    requests_per_second: float = 25.0


@dataclass(frozen=True)
class CloudInferenceSettings:
    gemini_api_key: str = field(repr=False)
    groq_api_key: str = field(repr=False)
    openrouter_api_key: str | None = field(default=None, repr=False)
    free_tier_confirmed: bool = True


def load_openalex_settings() -> OpenAlexSettings:
    """Load the OpenAlex key from the project root without logging its value."""
    load_dotenv(dotenv_path=ENV_FILE, override=False)
    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    if not api_key:
        raise ConfigurationError("OPENALEX_API_KEY is required in the project-root .env file.")
    return OpenAlexSettings(api_key=api_key)


def load_cloud_inference_settings() -> CloudInferenceSettings:
    """Load cloud keys only after a developer explicitly confirms free-tier use."""
    load_dotenv(dotenv_path=ENV_FILE, override=False)
    if os.getenv("BLINDSPOT_CLOUD_FREE_TIER_CONFIRMED", "").strip().casefold() != "true":
        raise ConfigurationError("BLINDSPOT_CLOUD_FREE_TIER_CONFIRMED=true is required before cloud inference.")
    gemini = os.getenv("GEMINI_API_KEY", "").strip()
    groq = os.getenv("GROQ_API_KEY", "").strip()
    openrouter = os.getenv("OPENROUTER_API_KEY", "").strip() or None
    if not gemini or not groq:
        raise ConfigurationError("GEMINI_API_KEY and GROQ_API_KEY are required in the project-root .env file.")
    return CloudInferenceSettings(gemini_api_key=gemini, groq_api_key=groq, openrouter_api_key=openrouter)
