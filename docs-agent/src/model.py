"""Shared model configuration.

Uses the `global.` cross-region inference profiles on Bedrock — they route
across all available regions for higher throughput vs. the US-only `us.`
profiles. All 1M-context factories carry both the 1M beta (for input window)
and the interleaved-thinking beta (for tool-heavy reasoning), with a fixed
thinking budget to keep per-turn latency predictable.
"""

from strands.models.bedrock import BedrockModel

# Cross-region inference profiles (route across AWS regions for throughput).
OPUS_MODEL_ID = "global.anthropic.claude-opus-4-6-v1"
# Sonnet 4.6's profile drops the `-v1` suffix. Verified empirically.
SONNET_MODEL_ID = "global.anthropic.claude-sonnet-4-6"

# Shared inference config. `max_tokens` and `budget_tokens` are paired:
# Bedrock rejects requests where `max_tokens <= budget_tokens`. 64K output
# cap is generous; 8K thinking budget caps per-turn latency without
# strangling reasoning on tool-heavy phases.
MAX_TOKENS = 64_000
THINKING_BUDGET_TOKENS = 8_000
REGION = "us-west-2"

# Kept for the legacy `MODEL_ID` symbol some callers import.
MODEL_ID = OPUS_MODEL_ID


def create_model() -> BedrockModel:
    """Create a standard Bedrock Opus 4.6 model (200K context, no betas)."""
    return BedrockModel(
        model_id=OPUS_MODEL_ID,
        region_name=REGION,
        cache_prompt="default",
        cache_tools="default",
    )


def create_model_1m() -> BedrockModel:
    """Bedrock Opus 4.6 with 1M context + interleaved thinking (budgeted).

    Betas:
      - `context-1m-2025-08-07` — expands input window to 1M tokens.
      - `interleaved-thinking-2025-05-14` — allows thinking to interleave
        with tool_use blocks, improving reasoning quality on tool-heavy
        turns.
    """
    return BedrockModel(
        model_id=OPUS_MODEL_ID,
        region_name=REGION,
        cache_prompt="default",
        cache_tools="default",
        max_tokens=MAX_TOKENS,
        additional_request_fields={
            "anthropic_beta": [
                "context-1m-2025-08-07",
                "interleaved-thinking-2025-05-14",
            ],
            "thinking": {
                "type": "enabled",
                "budget_tokens": THINKING_BUDGET_TOKENS,
            },
        },
    )


def create_sonnet_1m() -> BedrockModel:
    """Bedrock Sonnet 4.6 with 1M context + interleaved thinking (budgeted).

    Same config as the Opus factory; swap call-sites between factories to
    A/B models without any other prompt changes.
    """
    return BedrockModel(
        model_id=SONNET_MODEL_ID,
        region_name=REGION,
        cache_prompt="default",
        cache_tools="default",
        max_tokens=MAX_TOKENS,
        additional_request_fields={
            "anthropic_beta": [
                "context-1m-2025-08-07",
                "interleaved-thinking-2025-05-14",
            ],
            "thinking": {
                "type": "enabled",
                "budget_tokens": THINKING_BUDGET_TOKENS,
            },
        },
    )
