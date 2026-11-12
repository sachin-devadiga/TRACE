"""LLM client factory for the PS-01 financial auditing agent.

Two providers are configured behind stable role names:

    primary_extractor  Google Gemini  (structured extraction)
    backup_extractor   Groq           (fallback structured extraction)
    agent_router       Groq           (routing decisions)
    anomaly_explainer  Groq           (two-sentence anomaly narratives)

The defaults target the providers' currently available models as of November
2026: Gemini 3.8 Flash and Groq GPT-OSS 120B. The original Gemini 2.5 Flash and
Llama 3.3 70B IDs have been retired by the providers. Model IDs can be
overridden without code changes with ``GEMINI_MODEL`` and ``GROQ_MODEL``.

Clients are built lazily so importing this module never fails when a key is
missing; only the provider that is actually used needs to be configured.

Note on base URLs: the public ``groq.com`` marketing domain is not an API
endpoint. The default below is Groq's OpenAI-compatible API endpoint and can be
overridden with ``GROQ_BASE_URL`` if needed.

The anomaly explainer intentionally has a deterministic Python fallback in
``app.graph``: if Groq is slow, rate-limited, or unavailable, the graph still
produces a useful explanation during live demonstrations.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, TypeVar

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.confidence import compare_extractor_batches

load_dotenv()

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
DEFAULT_GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
try:
    DEFAULT_GEMINI_THINKING_BUDGET = int(os.getenv("GEMINI_THINKING_BUDGET", "0"))
except ValueError:
    DEFAULT_GEMINI_THINKING_BUDGET = 0

__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_GROQ_BASE_URL",
    "DEFAULT_GROQ_MODEL",
    "MissingAPIKeyError",
    "PROVIDERS",
    "ProviderSpec",
    "extract_with_agreement",
    "extract_with_fallback",
    "get_all_clients",
    "get_client",
]


class MissingAPIKeyError(RuntimeError):
    """Raised when a provider's API key is not present in the environment."""


@dataclass(frozen=True)
class ProviderSpec:
    """Configuration for one provider role."""

    provider: str
    role: str
    model: str
    api_key_env: str
    temperature: float
    base_url: str | None = None
    timeout: float = 90.0


PROVIDERS: dict[str, ProviderSpec] = {
    "primary_extractor": ProviderSpec(
        provider="Google Gemini",
        role="Primary extractor",
        model=DEFAULT_GEMINI_MODEL,
        api_key_env="GEMINI_API_KEY",
        temperature=0.0,
    ),
    "backup_extractor": ProviderSpec(
        provider="Groq",
        role="Backup extractor",
        model=DEFAULT_GROQ_MODEL,
        api_key_env="GROQ_API_KEY",
        temperature=0.0,
        base_url=os.getenv("GROQ_BASE_URL", DEFAULT_GROQ_BASE_URL),
    ),
    "agent_router": ProviderSpec(
        provider="Groq",
        role="Agent router",
        model=DEFAULT_GROQ_MODEL,
        api_key_env="GROQ_API_KEY",
        temperature=0.0,
        base_url=os.getenv("GROQ_BASE_URL", DEFAULT_GROQ_BASE_URL),
    ),
    "anomaly_explainer": ProviderSpec(
        provider="Groq",
        role="Anomaly explainer",
        model=DEFAULT_GROQ_MODEL,
        api_key_env="GROQ_API_KEY",
        temperature=0.2,
        base_url=os.getenv("GROQ_BASE_URL", DEFAULT_GROQ_BASE_URL),
    ),
}


def _require_api_key(spec: ProviderSpec) -> str:
    value = (os.getenv(spec.api_key_env) or "").strip()
    if not value:
        raise MissingAPIKeyError(
            f"{spec.api_key_env} is not set (required for {spec.provider}). "
            "Add it to .env or the process environment."
        )
    return value


@lru_cache(maxsize=None)
def get_client(client_key: str) -> Any:
    """Build (and cache) the LangChain chat client for a role.

    Raises:
        KeyError: if ``client_key`` is not a configured role.
        MissingAPIKeyError: if the role's API key is not configured.
    """
    try:
        spec = PROVIDERS[client_key]
    except KeyError as exc:
        choices = ", ".join(sorted(PROVIDERS))
        raise KeyError(
            f"Unknown client '{client_key}'. Expected one of: {choices}"
        ) from exc

    api_key = _require_api_key(spec)

    if spec.provider == "Google Gemini":
        return ChatGoogleGenerativeAI(
            model=spec.model,
            google_api_key=api_key,
            temperature=spec.temperature,
            timeout=spec.timeout,
            max_retries=1,
            # Flash models default to extended reasoning, which adds ~5-10s to
            # every structured extraction. The task is a short table lookup, so
            # the budget is zero by default; override with GEMINI_THINKING_BUDGET.
            thinking_budget=DEFAULT_GEMINI_THINKING_BUDGET,
        )

    return ChatOpenAI(
        model=spec.model,
        api_key=api_key,
        base_url=spec.base_url,
        temperature=spec.temperature,
        timeout=spec.timeout,
        max_retries=1,
    )


def get_all_clients() -> dict[str, Any]:
    """Build every configured client, keyed by role name.

    Useful from application code that wants to fail fast at startup. The smoke
    test deliberately calls :func:`get_client` instead so one missing key does
    not prevent the other provider from being tested.
    """
    return {key: get_client(key) for key in PROVIDERS}


def extract_with_fallback(prompt: str, schema: type[SchemaT]) -> SchemaT:
    """Extract structured data with Gemini 3.8 Flash, then Groq GPT-OSS.

    The primary provider can fail because of an outage, a rate limit, an
    exhausted quota, a bad key, or a schema/tool-calling incompatibility. Any
    such exception triggers the Groq backup extractor.

    Args:
        prompt: Extraction instructions plus the source text.
        schema: Pydantic model describing the desired output.

    Returns:
        An instance of ``schema`` produced by Gemini or, on failure, Groq.

    Raises:
        RuntimeError: if both providers fail. The message includes both errors.
    """
    try:
        primary = get_client("primary_extractor").with_structured_output(schema)
        return primary.invoke(prompt)
    except Exception as primary_error:
        logger.warning(
            "Gemini extraction failed (%s: %s); falling back to Groq.",
            type(primary_error).__name__,
            primary_error,
        )
        try:
            backup = get_client("backup_extractor").with_structured_output(schema)
            return backup.invoke(prompt)
        except Exception as backup_error:
            raise RuntimeError(
                "Both extraction providers failed. "
                f"Gemini: {primary_error}; Groq: {backup_error}"
            ) from backup_error


def _attempt_extraction(
    client_key: str,
    prompt: str,
    schema: type[SchemaT],
) -> tuple[SchemaT | None, str | None]:
    try:
        client = get_client(client_key).with_structured_output(schema)
        batch = client.invoke(prompt)
        if not isinstance(batch, schema):
            batch = schema.model_validate(batch)
        return batch, None
    except Exception as error:  # noqa: BLE001 - agreement is best-effort
        logger.warning(
            "%s extraction failed (%s: %s).",
            client_key,
            type(error).__name__,
            error,
        )
        return None, f"{type(error).__name__}: {error}"


def extract_with_agreement(
    prompt: str,
    schema: type[SchemaT],
) -> tuple[SchemaT, dict[str, dict[str, Any]]]:
    """Run Gemini and Groq on the same filing and compare their values.

    The primary result is authoritative when it succeeds; otherwise the
    fallback result is used. The second return value is a mapping keyed by
    canonical metric role containing ``agree``, ``disagree``, or
    ``fallback-unavailable``. Values are compared after full-USD normalization
    with a 0.5% tolerance. SEC XBRL facts are never used.

    Both providers are called concurrently: the agreement signal needs both
    readings, so running them in parallel cuts the critical path to the slower
    provider instead of the sum of both.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        primary_future = pool.submit(
            _attempt_extraction, "primary_extractor", prompt, schema
        )
        fallback_future = pool.submit(
            _attempt_extraction, "backup_extractor", prompt, schema
        )
        primary, primary_error = primary_future.result()
        fallback, fallback_error = fallback_future.result()

    if primary is None and fallback is None:
        raise RuntimeError(
            "Both extraction providers failed. "
            f"Gemini: {primary_error}; Groq: {fallback_error}"
        )

    selected = primary if primary is not None else fallback
    assert selected is not None
    agreement = compare_extractor_batches(
        primary,
        fallback,
        primary_error=primary_error,
        fallback_error=fallback_error,
    )
    return selected, agreement
