"""Reported reviewer-family qualification for both legacy CLI directions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any


class FamilyAttestationError(RuntimeError):
    """The invoked reviewer did not prove the required model family."""


@dataclass(frozen=True)
class ClaudeAttestation:
    reported_model: str
    route: str


def _is_openai_model(model: str) -> bool:
    value = model.strip().lower()
    return value.startswith(("gpt-", "codex-")) or re.fullmatch(r"o\d+(?:-.+)?", value) is not None


def attest_codex(reported_model: str | None, reported_transport: str | None) -> str:
    """Attest the model family reported on the spawned Codex app-server route.

    `reported_transport` establishes that the app-server initialize handshake completed. It
    is not claimed as independent proof of the upstream provider; the returned string names
    `thread/start`'s resolved model as the family-attestation basis.
    """
    if not isinstance(reported_model, str) or not reported_model.strip():
        raise FamilyAttestationError(
            "Codex reviewer family is unverifiable: thread/start reported no model"
        )
    if not _is_openai_model(reported_model):
        raise FamilyAttestationError(
            "Codex reviewer family mismatch: app-server reported model "
            f"{reported_model!r}, not an OpenAI-family model"
        )
    if not isinstance(reported_transport, str) or not reported_transport.strip():
        raise FamilyAttestationError(
            "Codex reviewer route is unverifiable: initialize reported no transport identity"
        )
    return (
        "codex-app-server:"
        "reported_family=openai;"
        f"reported_model={reported_model.strip()};"
        "basis=thread/start-resolved-model"
    )


def attest_claude(model_usage: Any) -> ClaudeAttestation:
    """Attest every model named by Claude CLI's result metadata as Claude-family."""
    if not isinstance(model_usage, Mapping) or not model_usage:
        raise FamilyAttestationError(
            "Claude reviewer family is unverifiable: result carried no modelUsage metadata"
        )

    models: list[str] = []
    providers: list[str] = []
    for key, raw in model_usage.items():
        if not isinstance(raw, Mapping):
            raise FamilyAttestationError(
                f"Claude reviewer family is unverifiable: modelUsage[{key!r}] is not an object"
            )
        model = raw.get("canonicalModel")
        provider = raw.get("provider")
        if not isinstance(model, str) or not model.strip():
            raise FamilyAttestationError(
                f"Claude reviewer family is unverifiable: modelUsage[{key!r}] names no canonicalModel"
            )
        if not model.strip().lower().startswith("claude-"):
            raise FamilyAttestationError(
                "Claude reviewer family mismatch: Claude CLI reported model "
                f"{model!r}, not a Claude-family model"
            )
        if not isinstance(provider, str) or not provider.strip():
            raise FamilyAttestationError(
                f"Claude reviewer route is unverifiable: modelUsage[{key!r}] names no provider"
            )
        models.append(model.strip())
        providers.append(provider.strip())

    unique_models = sorted(set(models))
    unique_providers = sorted(set(providers))
    return ClaudeAttestation(
        reported_model=",".join(unique_models),
        route=(
            "claude-cli:"
            f"canonical_models={','.join(unique_models)};"
            f"providers={','.join(unique_providers)}"
        ),
    )
