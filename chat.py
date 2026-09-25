"""Chat completions: forwards OpenWhispr's Language Models requests to the
Foundry /openai/v1 endpoint, rewriting only the parameters Azure rejects."""

from __future__ import annotations

import json
import os

from foundry import UpstreamError, log, open_azure

CHAT_ENDPOINT = os.environ.get(
    "FOUNDRY_CHAT_ENDPOINT", "https://opencode-lpqn3wrkin5y2.openai.azure.com/openai/v1"
)
# Deployment names listed on GET /models. Requests for other deployments still pass through.
CHAT_MODELS = [m.strip() for m in os.environ.get(
    "FOUNDRY_CHAT_MODELS", "gpt-5.4-mini,gpt-5.4-nano,gpt-6-luna"
).split(",") if m.strip()]


def adapt_chat_body(body: dict) -> dict:
    """Rewrite what OpenWhispr's self-hosted (llama.cpp-style) requests carry
    into what Azure OpenAI accepts. Everything else passes through untouched.

    - max_tokens: gpt-5+ on Azure rejects it; renamed to max_completion_tokens.
    - reasoning {effort}, think, thinking, chat_template_kwargs: OpenWhispr's
      "Disable thinking output" hints for Ollama/vLLM. Azure 400s on them.
      They become reasoning_effort, so OpenWhispr's toggle still decides.
    """
    body = dict(body)
    if "max_tokens" in body:
        tokens = body.pop("max_tokens")
        body.setdefault("max_completion_tokens", tokens)

    effort = None
    reasoning = body.pop("reasoning", None)
    if isinstance(reasoning, dict):
        if reasoning.get("effort"):
            effort = reasoning["effort"]
        elif reasoning.get("enabled") is False:
            effort = "none"
    if body.pop("think", None) is False:
        effort = "none"
    thinking = body.pop("thinking", None)
    if isinstance(thinking, dict) and thinking.get("type") == "disabled":
        effort = "none"
    kwargs = body.pop("chat_template_kwargs", None)
    if isinstance(kwargs, dict) and kwargs.get("enable_thinking") is False:
        effort = effort or "none"
    if effort and "reasoning_effort" not in body:
        body["reasoning_effort"] = effort
    return body


def rejected_param(error_body: bytes) -> str | None:
    """Name of the parameter Azure rejected, if the 400 says so."""
    try:
        err = json.loads(error_body).get("error", {})
    except (ValueError, AttributeError):
        return None
    param = err.get("param")
    if param and param not in ("model", "messages"):
        return param
    return None


def open_chat(body: dict):
    """Open a chat completion. If Azure rejects a parameter by name
    (e.g. temperature on a model that doesn't take it), drop it and retry."""
    url = f"{CHAT_ENDPOINT.rstrip('/')}/chat/completions"
    for _ in range(4):
        try:
            return open_azure(url, json.dumps(body).encode(), "application/json"), body
        except UpstreamError as exc:
            param = rejected_param(exc.body) if exc.status == 400 else None
            if not param or param not in body:
                raise
            log(f"chat {body.get('model')}: Azure rejected '{param}', retrying without it")
            body = {k: v for k, v in body.items() if k != param}
    raise UpstreamError(400, b'{"error": {"message": "too many rejected parameters"}}')
