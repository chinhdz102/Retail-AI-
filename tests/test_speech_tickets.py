import pytest

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


def test_token_collision_never_overwrites_an_outstanding_ticket() -> None:
    candidates = iter(["same", "same", "different"])
    store = SpeechTicketStore(token_factory=lambda: next(candidates))

    first = store.create({"text": "customer A"})
    second = store.create({"text": "customer B"})

    assert (first, second) == ("same", "different")
    assert store.consume(first) == {"text": "customer A"}
    assert store.consume(second) == {"text": "customer B"}


def test_repeated_token_collision_does_not_evict_an_existing_ticket() -> None:
    store = SpeechTicketStore(max_entries=1, token_factory=lambda: "duplicate")
    token = store.create({"text": "existing customer"})

    with pytest.raises(RuntimeError, match="unique speech ticket"):
        store.create({"text": "other customer"})

    assert store.consume(token) == {"text": "existing customer"}


@pytest.mark.parametrize(
    ("kwargs", "parameter"),
    [
        ({"ttl_seconds": 0}, "ttl_seconds"),
        ({"max_entries": 0}, "max_entries"),
    ],
)
def test_invalid_store_limits_fail_early(kwargs: dict, parameter: str) -> None:
    with pytest.raises(ValueError, match=parameter):
        SpeechTicketStore(**kwargs)
