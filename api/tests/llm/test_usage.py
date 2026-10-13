import json

from app.llm.usage import SseUsageTap, usage_from_json


def _event(name: str, data: dict[str, object]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()


STREAM = (
    _event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "model": "claude-sonnet-5-5",
                "usage": {"input_tokens": 1000, "cache_read_input_tokens": 500, "output_tokens": 1},
            },
        },
    )
    + _event("ping", {"type": "ping"})
    + _event("message_delta", {"type": "message_delta", "usage": {"output_tokens": 200}})
    + _event("message_stop", {"type": "message_stop"})
)


def test_message_delta_overlays_message_start_usage() -> None:
    tap = SseUsageTap()
    tap.feed(STREAM)
    assert tap.model == "claude-sonnet-5-5"
    assert tap.usage == {"input_tokens": 1000, "cache_read_input_tokens": 500, "output_tokens": 200}


def test_events_split_across_chunks_are_reassembled() -> None:
    tap = SseUsageTap()
    for i in range(0, len(STREAM), 5):
        tap.feed(STREAM[i : i + 5])
    assert tap.usage["output_tokens"] == 200
    assert tap.usage["input_tokens"] == 1000


def test_crlf_line_endings_and_garbage_are_tolerated() -> None:
    tap = SseUsageTap()
    tap.feed(STREAM.replace(b"\n", b"\r\n") + b"data: not json\r\n\r\n")
    assert tap.usage["output_tokens"] == 200


def test_null_delta_fields_do_not_erase_start_values() -> None:
    tap = SseUsageTap()
    tap.feed(
        STREAM + _event("message_delta", {"type": "message_delta", "usage": {"input_tokens": None}})
    )
    assert tap.usage["input_tokens"] == 1000


def test_usage_from_a_non_streaming_body() -> None:
    body = json.dumps(
        {"model": "claude-haiku-5-5", "usage": {"input_tokens": 7, "output_tokens": 3}}
    ).encode()
    assert usage_from_json(body) == ("claude-haiku-5-5", {"input_tokens": 7, "output_tokens": 3})
    assert usage_from_json(b"not json") == (None, {})
