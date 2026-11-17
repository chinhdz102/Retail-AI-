"""Opt-in local development traces; no audio/text is added to application logs."""

import hashlib
import json
import logging
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2] / ".tmp/stt-diagnostics"
_LOCK = Lock()


def save_trace(request_id, content, prepared, provider_result, result, root=ROOT):
    """Keep at most 12 turns; purge traces older than 30 minutes on each capture."""
    try:
        request_id = str(UUID(request_id))
        with _LOCK:
            root = Path(root).resolve()
            root.mkdir(parents=True, exist_ok=True)
            directories = sorted(
                (p for p in root.iterdir() if p.is_dir() and not p.is_symlink()),
                key=lambda p: p.stat().st_mtime,
            )
            now = time.time()
            kept = []
            for old in directories:
                try:
                    UUID(old.name)
                except ValueError:
                    continue
                if now - old.stat().st_mtime > 1800:
                    # The target must remain an immediate child of this trace folder.
                    if old.resolve().parent == root:
                        shutil.rmtree(old)
                else:
                    kept.append(old)
            for old in kept[:max(0, len(kept) - 11)]:
                if old.resolve().parent == root:
                    shutil.rmtree(old)
            folder = root / request_id
            folder.mkdir(exist_ok=False)
            (folder / "uploaded.audio").write_bytes(content)
            if prepared is not None:
                (folder / "provider-input.wav").write_bytes(prepared)
            metadata = {
                "created_epoch": now, "request_id": request_id,
                "uploaded_sha256": hashlib.sha256(content).hexdigest(),
                "provider_input_sha256": hashlib.sha256(prepared).hexdigest() if prepared else None,
                "provider_response": provider_result, "returned_result": asdict(result),
            }
            (folder / "trace.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError, TypeError):
        logging.getLogger(__name__).warning("Local STT trace could not be saved")
