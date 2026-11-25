"""Optional CPU Vietnamese recognizer; no downloads or transcript rewriting.

The caller must pass the same acoustic speech/noise gates used for cloud ASR.
Model license and measured accuracy are documented separately from runtime code.
"""

from __future__ import annotations

import importlib.util
import os
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import perf_counter

from backend.services.speech_activity import _decode_audio

MODEL_NAME = "zipformer-vi-30m-int8-2026-02-09"
_SLOTS = BoundedSemaphore(1)
_MODEL_LOCK = Lock()
_DLL_HANDLES = []


class VietnameseASRUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=2)
def _recognizer(directory: str, decoding_method: str = "greedy_search"):
    if not directory:
        raise VietnameseASRUnavailable("Local Vietnamese ASR model directory is not configured")
    root = Path(directory).expanduser().resolve()
    decoder = "decoder.int8.onnx" if (root / "decoder.int8.onnx").is_file() else "decoder.onnx"
    names = ("encoder.int8.onnx", decoder, "joiner.int8.onnx", "tokens.txt")
    if not all((root / name).is_file() for name in names):
        raise VietnameseASRUnavailable("Local Vietnamese ASR model files are missing")
    try:
        if os.name == "nt" and not _DLL_HANDLES:
            # Windows wheels share ONNX Runtime DLLs. Resolve package locations
            # without importing sherpa before its DLL directories are registered.
            for package, relative in (("onnxruntime", "capi"), ("sherpa_onnx", "lib")):
                spec = importlib.util.find_spec(package)
                if spec and spec.origin:
                    directory_path = Path(spec.origin).parent / relative
                    if directory_path.is_dir():
                        _DLL_HANDLES.append(os.add_dll_directory(str(directory_path)))
        import onnxruntime  # noqa: F401 -- load its runtime before the sherpa extension
        import sherpa_onnx

        return sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(root / names[0]),
            decoder=str(root / names[1]),
            joiner=str(root / names[2]),
            tokens=str(root / names[3]),
            num_threads=2,
            decoding_method=decoding_method,
            max_active_paths=4,
            debug=False,
        )
    except (ImportError, OSError, RuntimeError) as exc:
        raise VietnameseASRUnavailable("Local Vietnamese ASR runtime is unavailable") from exc


def transcribe_vietnamese(
    content: bytes, model_dir: str, decoding_method: str = "greedy_search",
) -> str:
    # A cancelled await cannot release a running native decoder. The worker
    # owns this slot until it actually exits, including cold initialization.
    if not _SLOTS.acquire(blocking=False):
        raise VietnameseASRUnavailable("Local Vietnamese ASR is busy")
    try:
        samples = _decode_audio(content, perf_counter() + 5)
        return _recognize_samples(samples, model_dir, decoding_method)
    finally:
        _SLOTS.release()


def transcribe_vietnamese_samples(samples, model_dir: str, decoding_method: str) -> str:
    """Decode already verified mono 16 kHz samples without decoding the file twice."""
    if not _SLOTS.acquire(blocking=False):
        raise VietnameseASRUnavailable("Local Vietnamese ASR is busy")
    try:
        return _recognize_samples(samples, model_dir, decoding_method)
    finally:
        _SLOTS.release()


def _recognize_samples(samples, model_dir: str, decoding_method: str) -> str:
    with _MODEL_LOCK:
        recognizer = _recognizer(model_dir, decoding_method)
    stream = recognizer.create_stream()
    stream.accept_waveform(16_000, samples)
    recognizer.decode_stream(stream)
    # No language-model rewrite, prompt, product substitution or cloud fallback.
    return stream.result.text.strip()
