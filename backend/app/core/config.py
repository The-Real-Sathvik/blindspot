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


def load_openalex_settings() -> OpenAlexSettings:
    """Load the OpenAlex key from the project root without logging its value."""
    load_dotenv(dotenv_path=ENV_FILE, override=False)
    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    if not api_key:
        raise ConfigurationError("OPENALEX_API_KEY is required in the project-root .env file.")
    return OpenAlexSettings(api_key=api_key)
