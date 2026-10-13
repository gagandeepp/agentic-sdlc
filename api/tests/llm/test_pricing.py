import logging

import pytest

from app.llm.pricing import (
    RESERVE_OUTPUT_TOKENS,
    Usage,
    context_window,
    cost_usd,
    estimate_reservation,
    load_pricing,
)

TABLE = load_pricing()


def test_sonnet_cost_matches_what_claude_code_reports() -> None:
    # Measured in the CLI spike: 11 in, 5 cache-read, 7 out on Sonnet 5.5 -> $0.000093.
    usage = Usage(input_tokens=11, cache_read_input_tokens=5, output_tokens=7)
    assert cost_usd(TABLE, "claude-sonnet-5-5", usage) == pytest.approx(0.000093)


def test_opus_cache_reads_cost_a_twentieth_of_input() -> None:
    usage = Usage(cache_read_input_tokens=1_000_000)
    assert cost_usd(TABLE, "claude-opus-5-5", usage) == pytest.approx(0.20)


def test_haiku_switches_to_long_context_rates_above_100k_prompt_tokens() -> None:
    short = Usage(input_tokens=50_000, output_tokens=1_000)
    long = Usage(input_tokens=150_000, output_tokens=1_000)
    assert cost_usd(TABLE, "claude-haiku-5-5", short) == pytest.approx(0.0055)
    assert cost_usd(TABLE, "claude-haiku-5-5", long) == pytest.approx(0.0775)


def test_cache_writes_are_priced_by_ttl() -> None:
    usage = Usage.from_api(
        {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 3_000,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 1_000,
                "ephemeral_1h_input_tokens": 2_000,
            },
        }
    )
    # 1000 * $2 * 1.25 + 2000 * $2 * 2.0 per MTok
    assert cost_usd(TABLE, "claude-sonnet-5-5", usage) == pytest.approx(0.0105)


def test_cache_writes_without_a_breakdown_count_as_5_minute() -> None:
    usage = Usage.from_api({"cache_creation_input_tokens": 1_000})
    assert cost_usd(TABLE, "claude-sonnet-5-5", usage) == pytest.approx(0.0025)


def test_unknown_models_are_charged_the_most_expensive_rates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    usage = Usage(input_tokens=1_000_000)
    with caplog.at_level(logging.WARNING):
        cost = cost_usd(TABLE, "claude-mystery-9", usage)
    assert cost == pytest.approx(4.0)
    assert "claude-mystery-9" in caplog.text


def test_from_api_tolerates_missing_and_null_fields() -> None:
    usage = Usage.from_api({"input_tokens": 5, "output_tokens": None, "server_tool_use": {}})
    assert (usage.input_tokens, usage.output_tokens, usage.prompt_tokens) == (5, 0, 5)


def test_context_window_and_reservation() -> None:
    assert context_window(TABLE, "claude-opus-5-5") == 1_000_000
    # 4000 body bytes ~ 1000 tokens at $4 + RESERVE_OUTPUT_TOKENS at $20 per MTok
    expected = (1_000 * 4 + RESERVE_OUTPUT_TOKENS * 20) / 1_000_000
    assert estimate_reservation(TABLE, "claude-opus-5-5", 4_000) == pytest.approx(expected)
