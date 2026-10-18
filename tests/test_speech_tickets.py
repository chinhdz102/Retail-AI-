from backend.services.speech_tickets import SpeechTicketStore


def test_ticket_can_be_consumed_only_once() -> None:
    store = SpeechTicketStore(token_factory=lambda: "single-use")
    token = store.create({"text": "private speech"})

    assert token == "single-use"
    assert store.consume(token) == {"text": "private speech"}
    assert store.consume(token) is None


def test_expired_ticket_is_not_returned() -> None:
    now = [10.0]
    store = SpeechTicketStore(ttl_seconds=5, clock=lambda: now[0], token_factory=lambda: "expires")
    token = store.create({"text": "private speech"})

    now[0] = 15.0

    assert store.consume(token) is None
