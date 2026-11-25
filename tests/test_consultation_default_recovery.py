"""Guarded default recovery: observed provider output and adversarial rewrites."""
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, model_validator

from backend.services.customer_consultation import ConsultationDecision, _accept_default_only_json


def test_observed_120b_omission_and_wire_nulls_recover():
    text = (Path(__file__).parent / "fixtures/consultation_default_recovery_120b.json").read_text(
        encoding="utf-8",
    )
    raw = json.loads(text)
    parsed = ConsultationDecision.model_validate_json(text, strict=True)
    assert parsed.model_fields_set == set(raw)
    assert "required_size" not in raw and parsed.required_size is None
    assert _accept_default_only_json(ConsultationDecision, text)


@pytest.mark.parametrize("changes", [
    {"searches": [{"category": None}]},
    {"max_price_vnd": "200000"},
    {"child_snack": 1},
    {"action": "invented"},
    {"unknown": None},
    {"guidance": "x" * 801},
])
def test_wire_null_permission_does_not_relax_other_fields(changes):
    raw = {"action": "recommend", "question": None, **changes}
    assert not _accept_default_only_json(ConsultationDecision, json.dumps(raw))


class RewritingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    optional: str = ""

    @model_validator(mode="before")
    @classmethod
    def rewrite(cls, value):
        return {**value, "text": value["text"].strip()}


class InjectingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    optional: str = ""

    @model_validator(mode="before")
    @classmethod
    def inject(cls, value):
        return {**value, "optional": value.get("optional", "invented")}


@pytest.mark.parametrize("schema,raw", [
    (RewritingModel, {"text": " changed "}),
    (InjectingModel, {"text": "unchanged"}),
])
def test_custom_before_validators_cannot_rewrite_or_invent(schema, raw):
    assert not _accept_default_only_json(schema, json.dumps(raw))
