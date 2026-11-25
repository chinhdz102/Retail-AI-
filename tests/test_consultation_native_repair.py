"""Native read-only verification cannot repair a rejected purchase draft."""

import pytest
from test_customer_consultation import Repository, product

from backend.config import Settings
from backend.services.customer_consultation import (
    ConsultationDecision,
    ConsultationReview,
    GroundedSelection,
    consult_customer,
)
from backend.services.groq import GroqService
from backend.services.sessions import InMemorySessionStore


@pytest.mark.parametrize("repaired", [True, False])
@pytest.mark.asyncio
async def test_native_repair_skips_invalid_draft_review_and_still_fails_closed(
    monkeypatch, repaired,
):
    import backend.services.customer_consultation as consultation

    rows = [product("pen", "Bút Nova", 20000, "Cửa hàng > Học tập > Bút"),
            product("book", "Vở Nova", 15000, "Cửa hàng > Học tập > Vở")]
    stages, selector_contexts, reviewed_texts = [], [], []

    async def stage(model, schema, prompt, data):
        stages.append(schema.__name__)
        if schema is ConsultationDecision:
            return schema(action="recommend", basket=True, total_budget_vnd=60000,
                          searches=[{"category": "Bút"}, {"category": "Vở"}]), "offline"
        if schema is GroundedSelection:
            selector_contexts.append(data.copy())
            quantity = 1 if len(selector_contexts) == 2 and repaired else 2
            return schema(picks=[{"candidate": 0, "quantity": quantity, "search_index": 0},
                                 {"candidate": 1, "quantity": quantity, "search_index": 1}],
                          text="Mình chọn {name:0} và {name:1}, tổng {total}."), "offline"
        assert schema is ConsultationReview
        reviewed_texts.append(data["rendered_text"])
        return schema(acceptable=True, product_checks=[{
            "candidate": i, "name_quote": row["name"], "actual_type": row["type"],
            "fits_need": True, "claims_supported": True,
        } for i, row in enumerate(data["selected_products"])]), "offline"

    monkeypatch.setattr(consultation, "_structured", stage)
    service = GroqService(Settings(_env_file=None, groq_api_key="offline"))
    kwargs = dict(text="Mình mua bút với vở, tổng dưới 60 nghìn.", repository=Repository(rows),
                  record=InMemorySessionStore().create("native-repair"), groq=service,
                  review_selection=True)
    if repaired:
        result = await consult_customer(**kwargs)
        assert result.products == rows and "35.000đ" in result.text
        assert reviewed_texts == [result.text]
        assert stages == ["ConsultationDecision", "GroundedSelection", "GroundedSelection",
                          "ConsultationReview"]
    else:
        with pytest.raises(ValueError, match="Basket exceeds total budget"):
            await consult_customer(**kwargs)
        assert reviewed_texts == []
    assert len(selector_contexts) == 2
    correction = selector_contexts[1]
    assert correction["selection_correction"] == "Basket exceeds total budget"
    assert correction["purchase_budget"]["previous_total_vnd"] == 70000
    assert correction["previous_draft"]["picks"][0]["quantity"] == 2
