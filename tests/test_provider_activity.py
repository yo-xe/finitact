from finitact import model
from finitact.provider_activity import listening, provider_call


def test_provider_call_reports_start_and_end_to_the_listener():
    events = []
    with listening(events.append):
        with provider_call():
            events.append("request")
    assert events == [True, "request", False]


def test_provider_call_without_a_listener_does_nothing():
    with provider_call():
        pass


def test_post_json_is_reported_even_when_the_request_fails(monkeypatch):
    def failing(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(model, "_post_json", failing)
    events = []
    with listening(events.append):
        try:
            model.post_json("https://example.invalid", "key", {})
        except RuntimeError:
            pass
    assert events == [True, False]
