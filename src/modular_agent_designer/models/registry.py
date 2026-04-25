"""Build LiteLlm instances from ModelConfig, resolving API keys from env."""
from __future__ import annotations

import os

from google.adk.models.lite_llm import LiteLlm

from ..config.schema import ModelConfig, PROVIDER_ENV_VARS


def build_model(cfg: ModelConfig) -> LiteLlm:
    """Instantiate a LiteLlm from a validated ModelConfig.

    Raises EnvironmentError if a required API key env var is not set.
    """
    env_var = PROVIDER_ENV_VARS.get(cfg.provider)
    if env_var is not None and not os.environ.get(env_var):
        raise EnvironmentError(
            f"Provider '{cfg.provider}' requires env var {env_var} to be set"
        )

    kwargs: dict = {}
    if cfg.temperature is not None:
        kwargs["temperature"] = cfg.temperature
    if cfg.max_tokens is not None:
        kwargs["max_tokens"] = cfg.max_tokens
    if cfg.thinking is not None:
        # LiteLLM routes the `thinking` kwarg to Anthropic's extended-thinking
        # API and `reasoning_effort` to OpenAI o-series. For Gemini,
        # `thinking_config` is the pass-through. We lift a top-level
        # `reasoning_effort` out so it becomes its own kwarg, and pass the rest
        # as `thinking=...`.
        thinking = dict(cfg.thinking)
        if "reasoning_effort" in thinking:
            kwargs["reasoning_effort"] = thinking.pop("reasoning_effort")
        if thinking:
            kwargs["thinking"] = thinking

    return LiteLlm(model=cfg.model, **kwargs)


def build_model_registry(
    models: dict[str, ModelConfig]
) -> dict[str, LiteLlm]:
    """Build a name -> LiteLlm mapping from the YAML models section."""
    return {name: build_model(cfg) for name, cfg in models.items()}
