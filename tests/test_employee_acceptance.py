"""Offline contracts for acceptance evidence capture; HTTP requests are mocked."""

import json
from types import SimpleNamespace

import httpx
import pytest

from evaluation import run_employee_acceptance as runner


def suite():
    return json.loads(runner.DEFAULT_SUITE.read_text(encoding="utf-8"))


def test_source_hashes_include_existing_policy_form_and_suite_files():
    hashes = runner.source_hashes(runner.DEFAULT_SUITE)
    assert set(hashes) == {*runner.SOURCE_FILES, "acceptance_suite"}
    assert "backend/services/consultation_policy.py" in hashes
    assert "backend/services/product_form.py" in hashes
    assert all(len(value) == 64 for value in hashes.values())


def test_suite_keeps_all_original_prompts_and_predeclares_every_turn_rubric():
    cases = suite()
    original = json.loads(
        (runner.ROOT / ("evaluation/cases/customer_consultation_35_dev_20261031.json")).read_text(
            encoding="utf-8"
        )
    )["queries"]
    original_turns = [
        turn
        for journey in cases["journeys"]
        for turn in journey["turns"]
        if "original_index" in turn
    ]
    assert len(cases["journeys"]) == 47
    assert len({journey["id"] for journey in cases["journeys"]}) == 47
    assert [turn["original_index"] for turn in original_turns] == list(range(1, 36))
    assert [turn["text"] for turn in original_turns] == original
    assert (
        sum(journey["group"] == "cross_category_generalization" for journey in cases["journeys"])
        == 12
    )
    for journey in cases["journeys"]:
        for turn in journey["turns"]:
            assert turn["decision_goal"] and turn["must_keep_criteria"]
            assert turn["clarification_legitimate_when"]
            assert turn["disallowed_unsupported_claims"]


def test_selection_and_global_turn_bound_keep_setup_in_order():
    schedule = runner.load_schedule(suite(), ["original-11,new-hotpot-meat-mission"], 3)
    assert [(row["journey_id"], row["turn_index"]) for row in schedule] == [
        ("original-11", 1),
        ("original-11", 2),
        ("new-hotpot-meat-mission", 1),
    ]
    with pytest.raises(ValueError, match="Unknown journey"):
        runner.load_schedule(suite(), ["original-99"], None)


@pytest.mark.parametrize(
    "status,payload,failed",
    [
        (200, {"text": "A plausible reply", "model": "consultation:consultation-fallback"}, True),
        (200, {"text": "A reply requiring content review", "model": "consultation:test"}, False),
        (200, {"text": "", "model": "test"}, True),
        (503, {"detail": "service unavailable"}, True),
    ],
)
def test_transport_or_fallback_failure_cannot_become_content_success(status, payload, failed):
    response = httpx.Response(status, json=payload)
    assert runner.is_service_failure(runner.capture_response(response)) is failed


@pytest.mark.asyncio
async def test_mocked_failed_session_retains_every_scheduled_turn(tmp_path, monkeypatch):
    cases = suite()
    schedule = runner.load_schedule(cases, ["new-hotpot-meat-mission"], 2)
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(503, json={"detail": "unavailable"})

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        runner.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    monkeypatch.setattr(runner, "source_hashes", lambda path: {"test": "unchanged"})
    output = tmp_path / "capture.json"
    await runner.run_capture(
        SimpleNamespace(
            suite=runner.DEFAULT_SUITE,
            base_url="http://mock.invalid",
            delay_seconds=45,
            timeout_seconds=1,
            max_turns=2,
            output=output,
        ),
        cases,
        schedule,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert requests == ["/sessions"]
    assert report["completed"] is True
    assert report["summary"]["scheduled_turns_denominator"] == 2
    assert report["summary"]["service_failures_in_denominator"] == 2
    assert report["summary"]["attempted_http_turns"] == 0
    assert report["summary"]["semantic_acceptance_rate"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("creation_status", [200, 201])
async def test_successfully_created_session_is_used_for_a_real_turn(
    tmp_path, monkeypatch, creation_status,
):
    cases = suite()
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == "/sessions":
            return httpx.Response(creation_status, json={"session_id": "created-session"})
        assert json.loads(request.content)["session_id"] == "created-session"
        return httpx.Response(200, json={"text": "Advice to review", "model": "consultation:test"})

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        runner.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    monkeypatch.setattr(runner, "source_hashes", lambda path: {"test": "unchanged"})
    output = tmp_path / "capture.json"
    await runner.run_capture(SimpleNamespace(
        suite=runner.DEFAULT_SUITE, base_url="http://mock.invalid", delay_seconds=45,
        timeout_seconds=1, max_turns=1, output=output,
    ), cases, runner.load_schedule(cases, ["original-02"], 1))
    report = json.loads(output.read_text(encoding="utf-8"))
    assert requests == ["/sessions", "/agent/respond"]
    assert report["summary"]["attempted_http_turns"] == 1
    assert report["summary"]["pending_agent_manual_review"] == 1
    assert report["summary"]["semantic_acceptance_rate"] is None
