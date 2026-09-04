import pytest
from pydantic import ValidationError

from backend.shared.contracts import (
    HealthResponse,
    IntentType,
    OrderStatus,
    PaymentStatus,
    SessionState,
    ToolCallRequest,
    ToolName,
)


def test_tool_call_request_rejects_unknown_tool() -> None:
    with pytest.raises(ValidationError):
        ToolCallRequest(tool="execute_sql", arguments={})


def test_tool_call_request_serializes_stable_tool_value() -> None:
    request = ToolCallRequest(tool=ToolName.GET_CART, arguments={"session_id": "s-1"})

    assert request.model_dump() == {
        "tool": "get_cart",
        "arguments": {"session_id": "s-1"},
    }


def test_shared_enums_match_wire_contract() -> None:
    assert SessionState.BROWSING.value == "BROWSING"
    assert IntentType.PRODUCT_LOOKUP.value == "PRODUCT_LOOKUP"
    assert PaymentStatus.PENDING.value == "PENDING"
    assert OrderStatus.AWAITING_PAYMENT.value == "AWAITING_PAYMENT"


def test_health_response_has_stable_shape() -> None:
    response = HealthResponse(status="ok", service="ai-retail-backend", version="0.1.0")

    assert response.model_dump() == {
        "status": "ok",
        "service": "ai-retail-backend",
        "version": "0.1.0",
    }
