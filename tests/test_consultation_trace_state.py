"""Trace follow-ups retain exactly the last actual product page across failures."""

from backend.services.sessions import InMemorySessionStore
from evaluation.trace_consultation_probe import (
    probe_state,
    remember_published_turn,
    restore_probe_state,
)


def test_failure_and_general_advice_cannot_erase_products_already_shown():
    record = InMemorySessionStore().create("trace-state")
    rows = [
        {"query": "Chọn giúp", "text": "Gợi ý đã xuất", "memory": {"need": "mua khăn"},
         "products": [{"id": "shown"}]},
        {"query": "Vì sao?", "text": "Tạm lỗi", "memory": {"need": "mua khăn"}, "products": []},
        {"query": "Tư vấn tiếp", "text": "Lời khuyên", "memory": {"need": "mua khăn"},
         "products": []},
    ]
    restore_probe_state(record, {"rows": rows})
    assert record.shopping.selected_product_ids == ["shown"]
    assert len(record.messages) == 6
    restored = InMemorySessionStore().create("resume-again")
    restore_probe_state(restored, {"final_state": probe_state(record), "rows": rows[-1:]})
    assert restored.shopping.selected_product_ids == ["shown"]
    assert restored.messages == record.messages
    remember_published_turn(restored, dict(rows[0], products=[{"id": "new"}]))
    assert restored.shopping.selected_product_ids == ["new"]
