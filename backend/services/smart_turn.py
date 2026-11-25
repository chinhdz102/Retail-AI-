"""Audio-native semantic end-of-turn detection using Pipecat Smart Turn v3.2 (ONNX).

Uses ``pipecat-ai/smart-turn-v3`` (``smart-turn-v3.2-cpu.onnx``, ~8.3 MB INT8 ONNX,
Whisper-Tiny encoder + classification head trained across 23 languages including
Vietnamese) paired with a pure-NumPy Whisper log-mel spectrogram extractor (no
PyTorch, Transformers, SciPy, or Librosa runtime overhead) and Vietnamese lexical
completeness heuristics.

Pure-NumPy Whisper log-mel extraction follows Pipecat's BSD-2-Clause / Hugging Face
Transformers Apache-2.0 Slaney filterbank and zero-mean unit-variance normalization.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock
from time import perf_counter

import numpy as np
import onnxruntime as ort
from numpy.lib.stride_tricks import sliding_window_view

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
MAX_WINDOW_SECONDS = 8
MAX_WINDOW_SAMPLES = SAMPLE_RATE * MAX_WINDOW_SECONDS  # 128,000 samples

_N_FFT = 400
_HOP_LENGTH = 160
_N_MELS = 80
_MEL_FLOOR = 1e-10
_NORM_VARIANCE_EPS = 1e-7

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SMART_TURN_MODEL = ROOT / "data/models/turn-detection/smart-turn-v3.2-cpu.onnx"
_SESSION_LOCK = Lock()

# Trailing Vietnamese words that strongly signal the shopper paused mid-sentence
# rather than finishing their turn (conjunctions, prepositions, classifiers, fillers).
_VI_INCOMPLETE_TAILS = frozenset(
    {
        "là",
        "mà",
        "và",
        "với",
        "hoặc",
        "hay",
        "nhưng",
        "nhưng mà",
        "vì",
        "bởi vì",
        "tại vì",
        "nên",
        "cho nên",
        "thì",
        "của",
        "cho",
        "từ",
        "đến",
        "tới",
        "ở",
        "trong",
        "trên",
        "dưới",
        "về",
        "theo",
        "bằng",
        "như",
        "cái",
        "chiếc",
        "loại",
        "chai",
        "lon",
        "hộp",
        "gói",
        "lốc",
        "thùng",
        "bịch",
        "túi",
        "hũ",
        "lọ",
        "tuýp",
        "có",
        "muốn",
        "cần",
        "đang",
        "định",
        "hỏi",
        "xem",
        "tìm",
        "mua",
        "lấy",
        "thêm",
        "chọn",
        "dùng",
        "để",
        "nếu",
        "khi",
        "lúc",
        "xong",
        "rồi thì",
        "với cả",
        "với lại",
        "còn",
        "cũng",
        "kiểu",
        "giống",
        "ờ",
        "à",
        "ừm",
        "âm",
        "ơ",
        "ê",
    }
)

_VI_INCOMPLETE_PHRASE_TAILS: tuple[str, ...] = (
    "cho mình hỏi",
    "cho tôi hỏi",
    "cho em hỏi",
    "mình muốn hỏi",
    "mình muốn mua",
    "mình đang tìm",
    "mình cần tìm",
    "mình cần mua",
    "cho mình xem",
    "cho tôi xem",
    "ở đây có",
    "cửa hàng có",
    "bên mình có",
    "bên bạn có",
    "tư vấn giúp mình",
    "tư vấn cho mình",
    "cho mình lấy",
    "lấy cho mình",
    "tìm giúp mình",
    "ví dụ như",
    "nghĩa là",
    "tức là",
    "với cả",
    "với lại",
    "nhưng mà",
    "thế thì",
)

# Short Vietnamese retail phrases that are unambiguously complete turns on their own.
_VI_COMPLETE_SHORT_PHRASES = frozenset(
    {
        "có",
        "không",
        "ok",
        "được",
        "vâng",
        "dạ",
        "rồi",
        "xong rồi",
        "đúng rồi",
        "không phải",
        "cảm ơn",
        "cảm ơn nhé",
        "cảm ơn bạn",
        "tạm biệt",
        "xin chào",
        "chào mai",
        "dừng lại",
        "khoan đã",
        "thôi không cần",
        "không cần đâu",
        "thêm vào giỏ",
        "bỏ ra giỏ",
        "thanh toán",
        "tính tiền",
        "xem giỏ hàng",
        "gọi quản lý",
        "gặp quản lý",
        "giá bao nhiêu",
        "bao nhiêu tiền",
        "ở kệ nào",
        "ở đâu",
        "rẻ hơn không",
        "có loại nào",
        "có loại khác không",
        "lấy cái này",
        "chọn cái này",
    }
)

_VI_COMPLETE_ENDING_TOKENS = frozenset(
    {
        "không",
        "chưa",
        "nào",
        "đâu",
        "nhiêu",
        "mấy",
        "gì",
        "sao",
        "nhé",
        "nha",
        "ạ",
        "nhỉ",
        "chứ",
        "hả",
        "vậy",
        "thế",
        "đấy",
        "đó",
        "này",
        "thôi",
        "đi",
        "luôn",
        "ngay",
        "nhất",
        "hơn",
        "rồi",
    }
)

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class SmartTurnResult:
    complete: bool
    probability: float
    elapsed_ms: float
    model_used: str
    reason: str


def _hertz_to_mel_slaney(freq: np.ndarray) -> np.ndarray:
    min_log_hertz = 1000.0
    min_log_mel = 15.0
    logstep = 27.0 / np.log(6.4)
    freq = np.atleast_1d(np.asarray(freq, dtype=np.float64))
    mels = 3.0 * freq / 200.0
    log_region = freq >= min_log_hertz
    mels[log_region] = min_log_mel + np.log(freq[log_region] / min_log_hertz) * logstep
    return mels


def _mel_to_hertz_slaney(mels: np.ndarray) -> np.ndarray:
    min_log_hertz = 1000.0
    min_log_mel = 15.0
    logstep = np.log(6.4) / 27.0
    mels = np.atleast_1d(np.asarray(mels, dtype=np.float64))
    freq = 200.0 * mels / 3.0
    log_region = mels >= min_log_mel
    freq[log_region] = min_log_hertz * np.exp(logstep * (mels[log_region] - min_log_mel))
    return freq


def _build_mel_filterbank(
    num_frequency_bins: int,
    num_mel_filters: int,
    min_frequency: float,
    max_frequency: float,
    sampling_rate: int,
) -> np.ndarray:
    mel_min = float(_hertz_to_mel_slaney(np.array([min_frequency], dtype=np.float64))[0])
    mel_max = float(_hertz_to_mel_slaney(np.array([max_frequency], dtype=np.float64))[0])
    mel_freqs = np.linspace(mel_min, mel_max, num_mel_filters + 2)
    filter_freqs = _mel_to_hertz_slaney(mel_freqs)
    fft_freqs = np.linspace(0, sampling_rate // 2, num_frequency_bins)

    filter_diff = np.diff(filter_freqs)
    slopes = np.expand_dims(filter_freqs, 0) - np.expand_dims(fft_freqs, 1)
    down_slopes = -slopes[:, :-2] / filter_diff[:-1]
    up_slopes = slopes[:, 2:] / filter_diff[1:]
    mel_filters = np.maximum(np.zeros(1), np.minimum(down_slopes, up_slopes))

    enorm = 2.0 / (filter_freqs[2 : num_mel_filters + 2] - filter_freqs[:num_mel_filters])
    mel_filters *= np.expand_dims(enorm, 0)
    return mel_filters


_HANN_WINDOW = np.hanning(_N_FFT + 1)[:-1].astype(np.float64)
_MEL_FILTERS = _build_mel_filterbank(
    num_frequency_bins=_N_FFT // 2 + 1,
    num_mel_filters=_N_MELS,
    min_frequency=0.0,
    max_frequency=SAMPLE_RATE / 2.0,
    sampling_rate=SAMPLE_RATE,
)


def _power_spectrogram(waveform: np.ndarray) -> np.ndarray:
    pad = _N_FFT // 2
    padded = np.pad(waveform.astype(np.float64), (pad, pad), mode="reflect")
    frames = sliding_window_view(padded, _N_FFT)[::_HOP_LENGTH]
    windowed = frames * _HANN_WINDOW
    spectrum = np.fft.rfft(windowed, axis=1)
    return (np.abs(spectrum) ** 2).T


def truncate_or_left_pad(audio: np.ndarray) -> np.ndarray:
    """Keep the trailing 8 seconds at 16 kHz, left-padding shorter clips with zeros."""
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    if x.size > MAX_WINDOW_SAMPLES:
        return x[-MAX_WINDOW_SAMPLES:]
    if x.size < MAX_WINDOW_SAMPLES:
        return np.pad(x, (MAX_WINDOW_SAMPLES - x.size, 0), mode="constant", constant_values=0.0)
    return x


def compute_whisper_log_mel_features(audio: np.ndarray, *, do_normalize: bool = True) -> np.ndarray:
    """Compute exact (80, 800) float32 Whisper log-mel features for Smart Turn v3."""
    x = truncate_or_left_pad(audio)
    if do_normalize:
        x = (x - x.mean()) / np.sqrt(x.var() + _NORM_VARIANCE_EPS)
    magnitudes = _power_spectrogram(x)
    mel_spec = np.maximum(_MEL_FLOOR, _MEL_FILTERS.T @ magnitudes)
    log_spec = np.log10(mel_spec)
    log_spec = log_spec[:, :-1]  # 801 -> 800 frames matching WhisperFeatureExtractor
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    log_spec = (log_spec + 4.0) / 4.0
    return log_spec.astype(np.float32)


@lru_cache(maxsize=2)
def _load_smart_turn_session(model_path: str) -> ort.InferenceSession:
    path = Path(model_path).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Smart Turn ONNX model missing: {path}")
    so = ort.SessionOptions()
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.intra_op_num_threads = 2
    so.inter_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])


def warm_smart_turn(model_path: str | Path | None = None) -> bool:
    """Preload the Smart Turn v3 ONNX graph at startup."""
    target = str(model_path or DEFAULT_SMART_TURN_MODEL)
    try:
        with _SESSION_LOCK:
            _load_smart_turn_session(target)
        logger.info("Smart Turn v3 ONNX pre-warmed model=%s", Path(target).name)
        return True
    except Exception as exc:
        logger.warning("Smart Turn v3 warmup skipped: %s", exc)
        return False


def classify_vietnamese_lexical_turn(partial_text: str | None) -> str:
    """Return ``'complete'``, ``'incomplete'``, or ``'ambiguous'`` from live Vietnamese text."""
    text = (partial_text or "").strip()
    if not text:
        return "ambiguous"
    if text.endswith("?") or text.endswith("!"):
        return "complete"
    tokens = [tok.casefold() for tok in _TOKEN_RE.findall(text)]
    if not tokens:
        return "ambiguous"
    phrase = " ".join(tokens)
    if phrase in _VI_COMPLETE_SHORT_PHRASES:
        return "complete"
    for suffix in _VI_INCOMPLETE_PHRASE_TAILS:
        if phrase == suffix or phrase.endswith(f" {suffix}"):
            return "incomplete"
    last_token = tokens[-1]
    last_two = " ".join(tokens[-2:]) if len(tokens) >= 2 else ""
    if last_two in _VI_INCOMPLETE_TAILS or last_token in _VI_INCOMPLETE_TAILS:
        return "incomplete"
    if len(tokens) >= 3 and last_token in _VI_COMPLETE_ENDING_TOKENS:
        return "complete"
    return "ambiguous"


def predict_turn_completion(
    samples: np.ndarray,
    *,
    partial_text: str | None = None,
    language: str = "vi",
    threshold: float = 0.50,
    model_path: str | Path | None = None,
) -> SmartTurnResult:
    """Combine Smart Turn v3.2 audio prosody with Vietnamese lexical completeness.

    Decision policy calibrated empirically on Vietnamese retail speech:
    - Unambiguous complete retail commands/questions finalize immediately (0 ms ONNX wait).
    - Clearly dangling Vietnamese clauses (``"Cho mình hỏi là..."``, ``"...của"``)
      always request a short continuation hold instead of chopping the user mid-sentence.
    - Ambiguous pauses (product names, mid-clause stops, empty partial) defer to
      ``smart-turn-v3.2-cpu.onnx`` (~70 ms CPU, ``>= threshold`` -> complete).
    """
    started = perf_counter()
    lexical = classify_vietnamese_lexical_turn(partial_text) if language == "vi" else "ambiguous"
    if lexical == "complete":
        return SmartTurnResult(
            complete=True,
            probability=0.95,
            elapsed_ms=round((perf_counter() - started) * 1000.0, 1),
            model_used="lexical-fastpath",
            reason="vietnamese_complete_clause",
        )
    if lexical == "incomplete":
        return SmartTurnResult(
            complete=False,
            probability=0.15,
            elapsed_ms=round((perf_counter() - started) * 1000.0, 1),
            model_used="lexical-fastpath",
            reason="vietnamese_incomplete_clause",
        )

    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    if x.size < SAMPLE_RATE // 4:
        return SmartTurnResult(
            complete=True,
            probability=0.65,
            elapsed_ms=round((perf_counter() - started) * 1000.0, 1),
            model_used="short-clip-fallback",
            reason="too_short_for_smart_turn",
        )

    target = str(model_path or DEFAULT_SMART_TURN_MODEL)
    try:
        with _SESSION_LOCK:
            session = _load_smart_turn_session(target)
        features = compute_whisper_log_mel_features(x, do_normalize=True)
        input_tensor = np.expand_dims(features, axis=0)
        input_name = session.get_inputs()[0].name
        outputs = session.run(None, {input_name: input_tensor})
        prob = float(np.asarray(outputs[0]).reshape(-1)[0])
        complete = prob >= threshold
        reason = "smart_turn_complete" if complete else "smart_turn_incomplete"
        return SmartTurnResult(
            complete=complete,
            probability=round(prob, 4),
            elapsed_ms=round((perf_counter() - started) * 1000.0, 1),
            model_used=Path(target).stem,
            reason=reason,
        )
    except Exception as exc:
        logger.debug("Smart Turn ONNX unavailable, falling back to lexical check: %s", exc)
        return SmartTurnResult(
            complete=True,
            probability=0.75,
            elapsed_ms=round((perf_counter() - started) * 1000.0, 1),
            model_used="lexical-fallback",
            reason="smart_turn_unavailable",
        )
