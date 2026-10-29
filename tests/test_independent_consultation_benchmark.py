from __future__ import annotations

from pathlib import Path

from evaluation.independent_consultation_benchmark import _reproducibility_metadata

ROOT = Path(__file__).resolve().parents[1]


def test_consultation_benchmark_captures_reproduction_provenance() -> None:
    metadata = _reproducibility_metadata(
        ROOT / "evaluation/cases/independent_consultation_v2.json",
        ROOT / "data/winmart_products.json",
    )

    assert metadata["commit_sha"]
    assert isinstance(metadata["worktree_dirty"], bool)
    assert len(metadata["dataset_sha256"]) == 64
    assert len(metadata["catalog_sha256"]) == 64
    assert len(metadata["pyproject_sha256"]) == 64
    assert metadata["model"].startswith("none;")
    assert metadata["configuration"]["candidate_limit"] == 5
    assert "python" in metadata["runtime"]
