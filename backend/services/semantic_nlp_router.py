"""Ultra-fast (<1 ms CPU) Vietnamese semantic NLP taxonomy & intent router.

Combines:
1. Pretrained multilingual semantic embeddings distilled from Hugging Face
   `minishlab/potion-multilingual-128M` (Model2Vec of BAAI/bge-m3), pruned to
   Vietnamese/catalog subwords and quantized to INT8 (~4 MB).
2. Position-weighted Vietnamese diacritic head-noun + bigram + char-ngram features
   that separate head nouns ('trái cây', 'nước cam', 'chanh', 'cá', 'táo') from
   flavor/scent modifiers ('kẹo dẻo trái cây', 'nước lau sàn hương cam', 'mì tôm chanh').
3. A trained discriminative softmax head mapping queries directly onto official
   Thanh Lam Mart taxonomy labels in pure NumPy (zero PyTorch/Transformers/API overhead).
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

MODEL_PATH = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "semantic_nlp"
    / "thanh_lam_intent_router_v1.npz"
)
_LEX_DIM = 2048

_MODIFIER_SPLIT_RE = re.compile(
    r"\b(?:vị|hương\s+vị|hương|chiết\s+xuất|tinh\s+chất|tinh\s+dầu|ruột|nhân|ngăn|chống|dưỡng|"
    r"kết\s+hợp|bổ\s+sung|giàu|hỗ\s+trợ|từ|làm\s+từ|có\s+tép|có\s+thạch|với|mix)\b",
    flags=re.IGNORECASE,
)
_CONVERSATIONAL_STRIP_RE = re.compile(
    r"(?i)(?:^|(?<=[\s,.;:!?]))"
    r"(?:chúng\s+tôi|chúng\s+ta|bọn\s+mình|bọn\s+tao|"
    r"siêu\s+thị|cửa\s+hàng|bên\s+bạn|ở\s+đây|"
    r"cho\s+(?:mình|tôi|tao|em|anh|chị)\s+(?:hỏi|xem|tìm|mua|biết)?|"
    r"(?:mình|tôi|tao|em|anh|chị)\s+(?:muốn|cần|đang\s+tìm|muốn\s+mua|muốn\s+hỏi)|"
    r"tư\s+vấn|gợi\s+ý|giới\s+thiệu|đề\s+xuất|bán\s+chạy|khuyến\s+mãi|giảm\s+giá|"
    r"giá\s+bao\s+nhiêu|bao\s+nhiêu\s+tiền|còn\s+hàng|"
    r"loại\s+nào|món\s+nào|cái\s+nào|mẫu\s+nào|chai\s+nào|tuýp\s+nào|hộp\s+nào|gói\s+nào|"
    r"sản\s+phẩm\s+nào|thứ\s+nào|trong\s+số\s+đó|trong\s+mấy\s+loại\s+đó|"
    r"dưới\s+\d+(?:[.,]\d+)?\s*(?:k|nghìn|ngàn|đ|đồng|vnd)?|"
    r"trên\s+\d+(?:[.,]\d+)?\s*(?:k|nghìn|ngàn|đ|đồng|vnd)?|"
    r"tầm\s+\d+(?:[.,]\d+)?\s*(?:k|nghìn|ngàn|đ|đồng|vnd)?|"
    r"khoảng\s+\d+(?:[.,]\d+)?\s*(?:k|nghìn|ngàn|đ|đồng|vnd)?|"
    r"có\s+bán|có\s+những|có\s+các|còn\s+loại|có|còn|không|ko|k(?=\s*$|\s*[?.,!])|khum|"
    r"rẻ\s+nhất|đắt\s+nhất|tốt\s+nhất|ngon\s+nhất|lớn\s+nhất|nhỏ\s+nhất|cao\s+nhất|thấp\s+nhất|"
    r"rẻ\s+hơn|đắt\s+hơn|tốt\s+hơn|lớn\s+hơn|nhỏ\s+hơn|tiết\s+kiệm|lợi\s+nhất|"
    r"gì|nào|loại|cái|món|mẫu|khác|ngon|tốt|rẻ|đắt|mắc|nhất|hơn|nhé|nha|ạ|ơi|đi|với|giúp|"
    r"xem|tìm|mua|lấy|thử|xíu|thêm|vậy|thế|sao|nhỉ|được|dc|đc|hả|à|ư|ừ|mà|thì|nữa|thôi|này|kia|đó)"
    r"(?=$|[\s,.;:!?])"
)


@dataclass(frozen=True)
class SemanticRoute:
    """Top predicted Thanh Lam Mart taxonomy category branches for a shopper query."""

    categories: tuple[str, ...]
    scores: tuple[float, ...]
    confidence: float
    margin: float
    core_query: str


@dataclass(frozen=True)
class _LoadedRouter:
    emb_f32: np.ndarray
    piece_to_local: dict[str, int]
    local_scores: tuple[float, ...]
    W: np.ndarray
    b: np.ndarray
    labels: tuple[str, ...]
    sku_ids: tuple[str, ...] = ()
    sku_emb_f32: np.ndarray | None = None


def _strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def clean_native_shopper_query(text: str, *, fallback_to_raw: bool = False) -> str:
    """Strip Vietnamese conversational wrappers while preserving exact Vietnamese diacritics."""
    s = unicodedata.normalize("NFC", text.strip().lower())
    s = re.sub(r"\btương\s+ót\b", "tương ớt", s)
    s = re.sub(r"\bsửa\s+tươi\b", "sữa tươi", s)
    s = re.sub(r"\bsửa\s+chua\b", "sữa chua", s)
    s = re.sub(r"\bsửa\s+rửa\s+mặt\b", "sữa rửa mặt", s)
    s = re.sub(r"\b(?:ko|k)\s+đường\b", "không đường", s)
    prev = None
    while prev != s:
        prev = s
        s = _CONVERSATIONAL_STRIP_RE.sub(" ", s)
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = " ".join(s.split())
    if s:
        return s
    return unicodedata.normalize("NFC", text.strip().lower()) if fallback_to_raw else ""


def _hash_bucket(key: str) -> tuple[int, float]:
    h = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    val = int.from_bytes(h[:4], "little")
    sign = 1.0 if (h[4] & 1) == 0 else -1.0
    return val % _LEX_DIM, sign


def _lex_head_vector(core: str) -> np.ndarray:
    vec = np.zeros(_LEX_DIM, dtype=np.float32)
    native = unicodedata.normalize("NFC", core.strip().lower())
    parts = _MODIFIER_SPLIT_RE.split(native, maxsplit=1)
    head_part = re.sub(r"[^\w\s]", " ", parts[0], flags=re.UNICODE)
    mod_part = re.sub(r"[^\w\s]", " ", parts[1], flags=re.UNICODE) if len(parts) > 1 else ""

    head_toks = [t for t in head_part.split() if t]
    mod_toks = [t for t in mod_part.split() if t]

    for idx, tok in enumerate(head_toks):
        w = 2.2 if idx == 0 else (1.7 if idx == 1 else (1.2 if idx == 2 else 0.7))
        b, s = _hash_bucket(f"nw:{tok}")
        vec[b] += s * w
        if idx == 0:
            b, s = _hash_bucket(f"h0:{tok}")
            vec[b] += s * 1.8
        ft = _strip_accents(tok)
        b, s = _hash_bucket(f"fw:{ft}")
        vec[b] += s * (0.55 * w)
        if len(tok) >= 3:
            padded = f"<{tok}>"
            for k in (3, 4):
                for j in range(len(padded) - k + 1):
                    b, s = _hash_bucket(f"c{k}:{padded[j : j + k]}")
                    vec[b] += s * (0.18 * w)

    for idx in range(len(head_toks) - 1):
        w = 2.4 if idx == 0 else 1.5
        bg = f"{head_toks[idx]}_{head_toks[idx + 1]}"
        b, s = _hash_bucket(f"nbg:{bg}")
        vec[b] += s * w
        fbg = f"{_strip_accents(head_toks[idx])}_{_strip_accents(head_toks[idx + 1])}"
        b, s = _hash_bucket(f"fbg:{fbg}")
        vec[b] += s * (0.75 * w)
    if len(head_toks) >= 3:
        tg = f"{head_toks[0]}_{head_toks[1]}_{head_toks[2]}"
        b, s = _hash_bucket(f"ntg:{tg}")
        vec[b] += s * 2.0

    for tok in mod_toks[:4]:
        b, s = _hash_bucket(f"mod:{tok}")
        vec[b] += s * 0.2

    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 1e-8 else vec


def _unigram_encode(core: str, router: _LoadedRouter) -> np.ndarray:
    s = "▁" + re.sub(r"\s+", "▁", unicodedata.normalize("NFC", core.strip().lower()))
    n = len(s)
    piece_to_local = router.piece_to_local
    local_scores = router.local_scores
    best: list[tuple[float, int, int]] = [(-1e18, -1, -1)] * (n + 1)
    best[0] = (0.0, -1, -1)
    for i in range(n):
        cur_score = best[i][0]
        if cur_score <= -1e17:
            continue
        end_lim = min(n, i + 20) + 1
        for j in range(i + 1, end_lim):
            loc = piece_to_local.get(s[i:j])
            if loc is not None:
                sc = cur_score + local_scores[loc]
                if sc > best[j][0]:
                    best[j] = (sc, i, loc)
        if best[i + 1][0] <= -1e17:
            best[i + 1] = (cur_score - 20.0, i, -1)
    ids: list[int] = []
    pos = n
    while pos > 0:
        _, prev, loc = best[pos]
        if loc >= 0:
            ids.append(loc)
        pos = prev if prev >= 0 else pos - 1
    if not ids:
        return np.zeros(router.emb_f32.shape[1], dtype=np.float32)
    vec = router.emb_f32[ids].mean(axis=0)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 1e-8 else vec


@lru_cache(maxsize=1)
def _load_router(path_str: str = "") -> _LoadedRouter | None:
    target = Path(path_str) if path_str else MODEL_PATH
    if not target.is_file():
        return None
    try:
        data = np.load(target, allow_pickle=True)
        emb_int8 = data["emb_int8"]
        emb_scales = data["emb_scales"].astype(np.float32)[:, None]
        emb_f32 = emb_int8.astype(np.float32) * emb_scales
        pieces = [str(p) for p in data["pieces"].tolist()]
        scores = tuple(float(s) for s in data["scores"].tolist())
        piece_to_local = {p: i for i, p in enumerate(pieces)}
        W = data["W"].astype(np.float32)
        b = data["b"].astype(np.float32)
        labels = tuple(str(lbl) for lbl in data["labels"].tolist())
        sku_ids: tuple[str, ...] = ()
        sku_emb_f32: np.ndarray | None = None
        if "sku_ids" in data and "sku_emb_int8" in data and "sku_scales" in data:
            sku_ids = tuple(str(pid) for pid in data["sku_ids"].tolist())
            s_scales = data["sku_scales"].astype(np.float32)[:, None]
            sku_emb_f32 = data["sku_emb_int8"].astype(np.float32) * s_scales
        return _LoadedRouter(
            emb_f32=emb_f32,
            piece_to_local=piece_to_local,
            local_scores=scores,
            W=W,
            b=b,
            labels=labels,
            sku_ids=sku_ids,
            sku_emb_f32=sku_emb_f32,
        )
    except Exception as exc:
        logger.warning("Semantic NLP router failed to load from %s: %s", target, exc)
        return None


def warm_semantic_nlp_router() -> bool:
    """Preload the compact semantic NLP taxonomy router during FastAPI startup."""
    router = _load_router()
    if router is None:
        return False
    route_query("có trái cây gì ko")
    logger.info(
        "Semantic NLP router pre-warmed classes=%d vocab=%d",
        len(router.labels),
        len(router.piece_to_local),
    )
    return True


@lru_cache(maxsize=1024)
def _route_cached(text: str, top_k: int) -> SemanticRoute | None:
    router = _load_router()
    if router is None:
        return None
    core = clean_native_shopper_query(text)
    if not core:
        return None
    e_sem = _unigram_encode(core, router)
    e_lex = _lex_head_vector(core)
    feat = np.concatenate([e_sem * 1.15, e_lex * 1.45]).astype(np.float32)
    logits = feat @ router.W + router.b
    logits -= float(logits.max())
    exp_l = np.exp(logits)
    probs = exp_l / float(exp_l.sum())
    order = np.argsort(probs)[::-1][: max(2, top_k)]
    top_cats = tuple(router.labels[int(i)] for i in order[:top_k])
    top_scores = tuple(float(probs[int(i)]) for i in order[:top_k])
    conf = top_scores[0]
    margin = conf - float(probs[int(order[1])]) if len(order) >= 2 else conf
    return SemanticRoute(
        categories=top_cats,
        scores=top_scores,
        confidence=conf,
        margin=margin,
        core_query=core,
    )


def route_query(
    text: str,
    *,
    top_k: int = 3,
    min_confidence: float = 0.45,
) -> SemanticRoute | None:
    """Predict the primary Thanh Lam Mart taxonomy category branch(es) for a shopper query in <1 ms."""
    if not text or not text.strip():
        return None
    res = _route_cached(text.strip(), top_k)
    if res is None or res.confidence < min_confidence:
        return None
    return res


def dense_search_skus(
    text: str,
    *,
    top_k: int = 12,
    min_sim: float = 0.45,
) -> tuple[tuple[str, float], ...]:
    """Return top `(product_id, cosine_sim)` matches across all catalog SKUs in <0.5 ms."""
    router = _load_router()
    if router is None or router.sku_emb_f32 is None or not router.sku_ids:
        return ()
    core = clean_native_shopper_query(text, fallback_to_raw=False)
    if not core:
        return ()
    qv = _unigram_encode(core, router)
    if float(np.linalg.norm(qv)) < 1e-6:
        return ()
    sims = router.sku_emb_f32 @ qv
    k = min(top_k, len(router.sku_ids))
    top_idx = np.argpartition(sims, -k)[-k:]
    top_idx = top_idx[np.argsort(sims[top_idx])[::-1]]
    return tuple(
        (router.sku_ids[int(i)], float(sims[int(i)]))
        for i in top_idx
        if float(sims[int(i)]) >= min_sim
    )
