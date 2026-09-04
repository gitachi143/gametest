"""Runtime configuration for SWAY, read once from the environment.

With no environment at all the app runs in DEMO MODE against a scripted
opponent, so `docker run` and `pytest` both work with zero setup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # .../sway


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:   # real environment always wins
            os.environ[key] = value


_load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class Settings:
    provider: str = field(default_factory=lambda: (os.environ.get("LLM_PROVIDER") or "").strip().lower())
    model: str = field(default_factory=lambda: (os.environ.get("LLM_MODEL") or "").strip())

    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", "").strip())
    google_project: str = field(default_factory=lambda: os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip())
    vertex_location: str = field(default_factory=lambda: os.environ.get("VERTEX_LOCATION", "global").strip())
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "").strip())
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", "").strip())
    openai_base_url: str = field(
        default_factory=lambda: (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    )

    # Azure OpenAI. Paste the endpoint the portal shows on the resource; the
    # `azure_base_url` property below turns it into the v1 API root, which is
    # OpenAI-compatible and needs no api-version.
    azure_endpoint: str = field(default_factory=lambda: os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip())
    azure_api_key: str = field(default_factory=lambda: os.environ.get("AZURE_OPENAI_API_KEY", "").strip())
    # On Azure the `model` field names the *deployment*, not the model. They are
    # usually named alike, so this falls back to LLM_MODEL.
    azure_deployment: str = field(default_factory=lambda: os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip())

    port: int = field(default_factory=lambda: _int("PORT", 8080))
    app_secret: str = field(default_factory=lambda: os.environ.get("APP_SECRET", "dev-secret-change-me"))
    db_path: str = field(default_factory=lambda: os.environ.get("DB_PATH", "data/sway.db"))

    # One instance, one upstream quota: too many simultaneous calls earns a 429
    # for all of them, so turns queue instead. True of Azure OpenAI's per-minute
    # token quota and of Vertex's shared capacity pool alike.
    llm_concurrency: int = field(default_factory=lambda: _int("LLM_CONCURRENCY", 3))
    llm_timeout: int = field(default_factory=lambda: _int("LLM_TIMEOUT", 45))
    max_output_tokens: int = field(default_factory=lambda: _int("MAX_OUTPUT_TOKENS", 700))
    daily_call_budget: int = field(default_factory=lambda: _int("DAILY_CALL_BUDGET", 900))
    judge_temperature: float = field(default_factory=lambda: _float("JUDGE_TEMPERATURE", 0.15))

    def __post_init__(self) -> None:
        if not self.provider:
            if self.azure_endpoint and self.azure_api_key:
                self.provider = "azure"
            elif self.gemini_api_key:
                self.provider = "gemini"
            elif self.google_project:
                self.provider = "vertex"
            elif self.anthropic_api_key:
                self.provider = "anthropic"
            elif self.openai_api_key:
                self.provider = "openai"
            else:
                self.provider = "mock"

        # A provider named without its credential degrades to the scripted
        # opponent rather than serving 500s on every turn.
        missing = {
            "azure": not (self.azure_endpoint and self.azure_api_key),
            "gemini": not self.gemini_api_key,
            "vertex": not self.google_project,
            "anthropic": not self.anthropic_api_key,
            "openai": not self.openai_api_key,
        }
        if missing.get(self.provider):
            self.provider = "mock"

        if not self.model:
            self.model = {
                "azure": "gpt-4.1-mini",
                "gemini": "gemini-2.5-flash",
                "vertex": "gemini-2.5-flash",
                "anthropic": "claude-opus-5",
                "openai": "gpt-4o-mini",
            }.get(self.provider, "scripted-mock")

    @property
    def demo_mode(self) -> bool:
        return self.provider == "mock"

    @property
    def azure_base_url(self) -> str:
        """The Azure OpenAI v1 API root, however the endpoint was pasted in.

        The portal shows the bare resource host, but the OpenAI-compatible
        surface lives under /openai/v1 - so accept either and normalise.
        """
        root = self.azure_endpoint.rstrip("/")
        if root.endswith("/openai/v1"):
            return root
        if root.endswith("/openai"):
            return root + "/v1"
        return root + "/openai/v1"

    def absolute_db_path(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else ROOT / p


settings = Settings()
