from types import SimpleNamespace

from backend.services.sessions import InMemorySessionStore
from backend.services.transcription_context import session_speech_terms


class Catalog:
    def list_speech_terms(self, limit):
        return ("THIÊN LONG", "VINAMILK", "INOCHI")[:limit]

    def list_brand_names(self, limit):
        return ("ARIEL", "OMO", "IN")

    def get_product_snapshot(self, product_id):
        return SimpleNamespace(brand="OMO", category="Nước giặt") if product_id == "p" else None


def test_prior_customer_names_and_visible_products_precede_global_popularity():
    record = InMemorySessionStore().create("test")
    record.messages = [{"role": "user", "content": "So sánh Ariel với OMO giúp mình"}]
    record.shopping.selected_product_ids = ["p"]
    terms = session_speech_terms(Catalog(), record)
    assert terms[:3] == ("ARIEL", "OMO", "Nước giặt")
    assert len(terms) == len(set(terms)) and len(terms) <= 8


def test_assistant_prose_or_substrings_cannot_create_brand_context():
    record = InMemorySessionStore().create("test")
    record.messages = [
        {"role": "user", "content": "Mình xin hỏi một chút"},
        {"role": "assistant", "content": "ARIEL"},
    ]
    assert session_speech_terms(Catalog(), record) == Catalog().list_speech_terms(8)


def test_absent_context_preserves_existing_prompt_and_limits_are_bounded():
    assert session_speech_terms(Catalog(), None, 2) == ("THIÊN LONG", "VINAMILK")
    assert session_speech_terms(Catalog(), None, 0) == ()
