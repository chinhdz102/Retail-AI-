from backend.config import Settings
from evaluation.live_groq_smoke import (
    grade_minimum_fields,
    select_scenarios,
    settings_for_mode,
)


def test_model_mode_locks_primary_model_by_disabling_fallbacks() -> None:
    settings = Settings(
        groq_api_key="test-key",
        groq_model="qwen/primary",
        groq_fallback_model="openai/fallback-one",
        groq_fallback_model_2="openai/fallback-two",
    )

    model_settings = settings_for_mode(settings, "model")
    system_settings = settings_for_mode(settings, "system")

    assert model_settings.groq_model == "qwen/primary"
    assert model_settings.groq_fallback_model == ""
    assert model_settings.groq_fallback_model_2 == ""
    assert system_settings.groq_fallback_model == "openai/fallback-one"


def test_minimum_field_grader_rejects_unneeded_fields() -> None:
    trace = [
        {
            "tool_calls": [
                {
                    "name": "get_product_snapshot",
                    "arguments": '{"fields":["price_vnd","stock"]}',
                }
            ],
        }
    ]

    grade = grade_minimum_fields("A1_price", trace)

    assert grade == {
        "status": "FAIL",
        "required_fields": ["price_vnd"],
        "requested_fields": ["price_vnd", "stock"],
    }


def test_scenario_selection_keeps_only_requested_cases() -> None:
    selected = select_scenarios(["A1_price", "A3_shelf"])

    assert list(selected) == ["A1_price", "A3_shelf"]
