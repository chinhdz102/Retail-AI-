"""In-process API latency/concurrency sanity check for deterministic catalog turns."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import subprocess
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

import backend.api.main as api
from backend.products.snapshot_repository import SnapshotProductRepository
from backend.services import retail_tools
from backend.services.groq import GroqService
from backend.services.sessions import InMemorySessionStore

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "winmart_products.json"
DEFAULT_OUTPUT = ROOT / "docs" / "reports" / "artifacts" / "api_runtime_load_sanity_20261028.json"
QUERIES = (
    "Gợi ý dầu gội",
    "Giới thiệu nước giặt",
    "Gợi ý khăn mặt",
    "Tôi muốn tìm thịt để ăn lẩu",
)


class _UnexpectedGroqCall(GroqService):
    async def chat(self, messages, model=None, tools=None, tool_choice=None):
        raise AssertionError("This catalog-only load probe must not call a model")

    async def transcribe(self, *args, **kwargs):
        raise AssertionError("This text-only load probe must not call transcription")

    async def synthesize(self, *args, **kwargs):
        raise AssertionError("This text-only load probe must not call speech synthesis")


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile + 0.5)))
    return round(ordered[index], 3)


async def _run_level(
    client: httpx.AsyncClient,
    repository: SnapshotProductRepository,
    concurrency: int,
    requests_per_level: int,
) -> dict[str, Any]:
    next_index = 0
    latencies: list[float] = []
    statuses: list[int] = []
    identity_mismatches = 0
    turn_errors: list[str] = []
    models: dict[str, int] = {}
    start = time.perf_counter()
    cpu_start = time.process_time()

    async def worker() -> None:
        nonlocal next_index, identity_mismatches
        while True:
            index = next_index
            next_index += 1
            if index >= requests_per_level:
                return
            session_response = await client.post(
                "/sessions", json={"kiosk_id": f"load-{concurrency}-{index}"}
            )
            if session_response.status_code != 201:
                statuses.append(session_response.status_code)
                turn_errors.append("session_create_failed")
                continue
            session_id = session_response.json()["session_id"]
            query = QUERIES[index % len(QUERIES)]
            request_start = time.perf_counter()
            response = await client.post(
                "/agent/respond",
                json={"session_id": session_id, "text": query, "language": "vi"},
            )
            latencies.append((time.perf_counter() - request_start) * 1000)
            statuses.append(response.status_code)
            if response.status_code != 200:
                turn_errors.append(f"respond_http_{response.status_code}")
                continue
            payload = response.json()
            model_name = str(payload.get("model") or "unknown")
            models[model_name] = models.get(model_name, 0) + 1
            for card in payload.get("products", []):
                snapshot = repository.get_product_snapshot(card.get("product_id", ""))
                if (
                    snapshot is None
                    or snapshot.name != card.get("name")
                    or snapshot.price_vnd != card.get("price_vnd")
                ):
                    identity_mismatches += 1
            if payload.get("grounded_product_ids") and not payload.get("products"):
                identity_mismatches += 1

    workers = min(concurrency, requests_per_level)
    await asyncio.gather(*(worker() for _ in range(workers)))
    wall_ms = (time.perf_counter() - start) * 1000
    cpu_ms = (time.process_time() - cpu_start) * 1000
    success_count = sum(status == 200 for status in statuses)
    return {
        "concurrency": concurrency,
        "requests": requests_per_level,
        "successful_responses": success_count,
        "http_errors": len(statuses) - success_count,
        "identity_or_price_mismatches": identity_mismatches,
        "response_models": models,
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "mean": round(statistics.mean(latencies), 3) if latencies else None,
        },
        "wall_time_ms": round(wall_ms, 3),
        "process_cpu_ms": round(cpu_ms, 3),
        "throughput_requests_per_second": round(success_count / (wall_ms / 1000), 3)
        if wall_ms
        else 0,
        "errors": turn_errors,
    }


async def _run_heap_probe(client: httpx.AsyncClient) -> dict[str, Any]:
    session_response = await client.post(
        "/sessions", json={"kiosk_id": "load-memory-probe"}
    )
    if session_response.status_code != 201:
        return {"status": "session_create_failed"}
    tracemalloc.start()
    try:
        response = await client.post(
            "/agent/respond",
            json={
                "session_id": session_response.json()["session_id"],
                "text": QUERIES[0],
                "language": "vi",
            },
        )
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        return {
            "http_status": response.status_code,
            "python_heap_current_bytes": current_bytes,
            "python_heap_peak_bytes": peak_bytes,
        }
    finally:
        tracemalloc.stop()


async def run(output: Path, requests_per_level: int) -> dict[str, Any]:
    repository = SnapshotProductRepository(CATALOG)
    api.get_product_repository = lambda *args: repository
    retail_tools.get_product_repository = lambda *args: repository
    api.session_store = InMemorySessionStore()
    api.app.dependency_overrides[api.get_groq_service] = lambda: _UnexpectedGroqCall()
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://load-test", timeout=60
    ) as client:
        levels = []
        for concurrency in (1, 5, 20):
            levels.append(await _run_level(client, repository, concurrency, requests_per_level))
        heap_probe = await _run_heap_probe(client)
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    payload = {
        "artifact_id": "retail_ai_api_runtime_load_sanity",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "commit_sha": git_sha,
        "mode": "in_process_asgi_deterministic_catalog_only",
        "catalog_sha256": hashlib.sha256(CATALOG.read_bytes()).hexdigest(),
        "catalog_valid_product_count": len(repository.products),
        "requests_per_concurrency": requests_per_level,
        "levels": levels,
        "python_heap_probe_separate_from_latency": heap_probe,
        "limitations": [
            (
                "ASGITransport is in-process and excludes sockets, browser, TLS, proxy, "
                "and server workers."
            ),
            "The path uses deterministic catalog turns and does not measure Groq, STT, or TTS.",
            (
                "Python heap is measured in a separate single-request probe to avoid distorting "
                "timed latency; native allocations and process resident memory are not measured."
            ),
            "This is a smoke load profile, not a capacity or SLA claim.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--requests-per-concurrency", type=int, default=5)
    args = parser.parse_args()
    if args.requests_per_concurrency < 1:
        parser.error("--requests-per-concurrency must be positive")
    result = asyncio.run(run(args.output, args.requests_per_concurrency))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
