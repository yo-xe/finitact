import json

from finitact.outer_trace import breakdown, turns


def _line(t, event):
    return json.dumps({"t_ms": t, "event": event})


def _stream(t, kind, **fields):
    return _line(t, {"type": "stream_event", "event": {"type": kind, **fields}})


def test_a_tool_turn_splits_into_wait_thinking_text_tool_input_and_tool_time():
    lines = [
        _stream(100, "message_start", message={"usage": {"input_tokens": 2, "cache_read_input_tokens": 1000}}),
        _stream(300, "content_block_start", index=0, content_block={"type": "thinking"}),
        _stream(310, "content_block_delta", index=0, delta={"type": "thinking_delta", "thinking": ""}),
        _stream(900, "content_block_stop", index=0),
        _stream(900, "content_block_start", index=1, content_block={"type": "text"}),
        _stream(950, "content_block_delta", index=1, delta={"type": "text_delta", "text": "Opening."}),
        _stream(1000, "content_block_stop", index=1),
        _stream(1000, "content_block_start", index=2, content_block={"type": "tool_use", "name": "mcp__finitact__run_browser", "id": "tu1"}),
        _stream(1100, "content_block_delta", index=2, delta={"type": "input_json_delta", "partial_json": '{"goals":[]}'}),
        _stream(1500, "content_block_stop", index=2),
        _stream(1500, "message_delta", usage={"output_tokens": 300, "output_tokens_details": {"thinking_tokens": 200}}),
        _stream(1510, "message_stop"),
        _line(9510, {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1", "content": [{"type": "text", "text": '{"run_id": "r1"}'}]}]}}),
        _stream(9600, "message_start", message={"usage": {"input_tokens": 1, "cache_read_input_tokens": 1500}}),
        _stream(9700, "content_block_start", index=0, content_block={"type": "text"}),
        _stream(9800, "content_block_delta", index=0, delta={"type": "text_delta", "text": "DONE"}),
        _stream(9900, "content_block_stop", index=0),
        _stream(9900, "message_delta", usage={"output_tokens": 5}),
        _stream(9910, "message_stop"),
    ]
    first, second = turns(lines)
    assert (first["wait_ms"], first["ttft_ms"], first["thinking_ms"], first["text_ms"], first["tool_input_ms"]) == (100, 210, 600, 100, 500)
    assert (first["tool_input_chars"], first["thinking_tokens"], first["context_tokens"]) == (12, 200, 1002)
    assert first["tools"][0]["name"] == "run_browser" and first["tools"][0]["tool_ms"] == 8010
    assert (second["trigger"], second["wait_ms"]) == ("run_browser", 90)
    ledger = {"r1": {"goals": [{"stage_ms": {"decide": 700, "observe": 300, "adapter_send": 5}}]}}
    total = breakdown([first, second], ledger.get)
    assert total["model_ms"] == 100 + 1410 + 90 + 310 and total["tool_ms"] == 8010
    assert total["finitact_stage_ms"] == {"decide": 700, "observe": 300}
    assert total["by_tool"]["run_browser"]["calls"] == 1
