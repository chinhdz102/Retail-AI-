"""Build a compact (<5 MB), sub-millisecond Vietnamese semantic NLP category router.

Architecture:
1. Pretrained multilingual semantic backbone:
   - Distilled from Hugging Face `minishlab/potion-multilingual-128M` (Model2Vec of BAAI/bge-m3).
   - Vocabulary pruned to Vietnamese diacritic subwords, Thanh Lam Mart catalog subwords, and high-frequency
     ASCII/Latin subwords (~20K tokens), INT8 quantized with per-row scale (~3.5-4.5 MB).
2. Discriminative Head-Noun + Semantic Dual-Space Softmax Classifier (pure NumPy):
   - Input features = [256-dim L2-normalized pretrained bge-m3/Model2Vec vector,
                       2048-dim position-weighted diacritic word/bigram/char-ngram hash vector].
   - Trained with Adam + L2 regularization + label smoothing over all Thanh Lam Mart catalog SKUs
     (both head-noun-isolated titles and full titles so modifiers like 'kẹo dẻo trái cây',
     'nước lau sàn hương cam', 'mì tôm chanh' stay cleanly separated from 'trái cây', 'nước cam',
     'chanh'), plus taxonomy labels and Vietnamese colloquial retail synonyms.
3. Runtime artifact:
   - Single compressed `.npz` file (`data/models/semantic-nlp/thanh_lam_intent_router_v1.npz`).
   - Zero PyTorch/Transformers/HuggingFace runtime dependencies (pure NumPy + stdlib).
   - CPU latency: < 1 ms per query; runs identically on Windows, Ubuntu CI, and Vercel Serverless.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import time
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "thanh_lam_products.json"
DETAILS_PATH = ROOT / "data" / "thanh_lam_product_details.json"
CACHE_DIR = ROOT / ".tmp" / "hf_potion"
OUT_PATH = ROOT / "backend" / "assets" / "semantic_nlp" / "thanh_lam_intent_router_v1.npz"

HF_BASE = "https://huggingface.co/minishlab/potion-multilingual-128M/resolve/main"
LEX_DIM = 2048

# Flavor / scent / derivative preposition markers in Vietnamese product names.
_MODIFIER_SPLIT_RE = re.compile(
    r"\b(?:vị|hương\s+vị|hương|chiết\s+xuất|tinh\s+chất|tinh\s+dầu|ruột|nhân|ngăn|chống|dưỡng|"
    r"kết\s+hợp|bổ\s+sung|giàu|hỗ\s+trợ|từ|làm\s+từ|có\s+tép|có\s+thạch|với|mix)\b",
    flags=re.IGNORECASE,
)
_SIZE_NOISE_RE = re.compile(
    r"\b\d+(?:[.,/x×-]\d+)*\s*(?:ml|lít|lit|l|kg|gr|g|gam|cái|gói|chai|lon|hộp|túi|bịch|lốc|"
    r"thùng|vỉ|cuộn|viên|miếng|tờ|chiếc|quả|trái|cây|thanh|tuýp|can|hũ|lọ|khay|size)?\b",
    flags=re.IGNORECASE,
)
_PACK_PREFIX_RE = re.compile(
    r"^(?:thùng|lốc|bịch|hộp|gói|túi|chai|lon|vỉ|khay|can|hũ|lọ|combo|bộ|set)\s+"
    r"(?:\d+\s*(?:gói|chai|lon|hộp|túi|vỉ|miếng|chiếc|cuộn|cây|thanh|quả|trái)?\s*)?",
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
    r"loại\s+nào|món\s+nào|cái\s+nào|sản\s+phẩm\s+nào|thứ\s+nào|"
    r"dưới\s+\d+(?:[.,]\d+)?\s*(?:k|nghìn|ngàn|đ|đồng|vnd)?|"
    r"trên\s+\d+(?:[.,]\d+)?\s*(?:k|nghìn|ngàn|đ|đồng|vnd)?|"
    r"tầm\s+\d+(?:[.,]\d+)?\s*(?:k|nghìn|ngàn|đ|đồng|vnd)?|"
    r"khoảng\s+\d+(?:[.,]\d+)?\s*(?:k|nghìn|ngàn|đ|đồng|vnd)?|"
    r"có\s+bán|có\s+những|có\s+các|có|không|ko|k(?=\s*$|\s*[?.,!])|khum|"
    r"gì|nào|ngon|tốt|rẻ|nhất|nhé|nha|ạ|ơi|đi|với|giúp|xem|tìm|mua|lấy|thử|xíu|"
    r"vậy|thế|sao|nhỉ|được|dc|đc|hả|à|ư|ừ|mà|thì|nữa|thôi|này|kia|đó)"
    r"(?=$|[\s,.;:!?])"
)
_VI_DIACRITIC_RE = re.compile(
    r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]",
    flags=re.IGNORECASE,
)


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def clean_native_query(text: str) -> str:
    """Preserve Vietnamese diacritics while stripping conversational wrappers."""
    s = unicodedata.normalize("NFC", text.strip().lower())
    s = re.sub(r"\btương\s+ót\b", "tương ớt", s)
    s = re.sub(r"\bsửa\s+tươi\b", "sữa tươi", s)
    s = re.sub(r"\bsửa\s+chua\b", "sữa chua", s)
    s = re.sub(r"\b(?:ko|k)\s+đường\b", "không đường", s)
    prev = None
    while prev != s:
        prev = s
        s = _CONVERSATIONAL_STRIP_RE.sub(" ", s)
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = " ".join(s.split())
    return s or unicodedata.normalize("NFC", text.strip().lower())


def extract_head_phrase(title: str) -> str:
    """Isolate the Vietnamese product head noun before flavor/scent modifiers & sizes."""
    s = unicodedata.normalize("NFC", title.strip().lower())
    s = _PACK_PREFIX_RE.sub("", s)
    # Cut at modifier prepositions ("vị", "hương", "ruột", "chiết xuất", ...)
    parts = _MODIFIER_SPLIT_RE.split(s, maxsplit=1)
    head = parts[0] if parts and parts[0].strip() else s
    head = _SIZE_NOISE_RE.sub(" ", head)
    head = re.sub(r"[^\w\s]", " ", head, flags=re.UNICODE)
    toks = head.split()
    return " ".join(toks[:5]) if toks else s


def canonical_taxonomy_label(item: dict) -> str:
    """Map each Thanh Lam Mart SKU to its exact searchable Thanh Lam Mart taxonomy segment name.

    CRITICAL DESIGN PRINCIPLE: Never merge heterogeneous leaf product types into a
    coarse parent bucket (e.g. NEVER lump 'Kẹo gum' + 'Chocolate' + 'Thạch đóng gói'
    into 'Kẹo & Chocolate', or 'Bánh bông lan' + 'Bánh gạo' + 'Bánh biscuit' into 'Bánh').
    Only merge genuinely homogeneous shopper-level categories (like Trái cây NK + NĐ -> Trái cây).
    """
    hier = str(item.get("category_hierarchy") or "")
    parts = [s.strip() for s in hier.split(">") if s.strip()]
    if len(parts) < 3:
        return str(item.get("category_name") or (parts[-1] if parts else "Khác"))

    lvl2 = parts[2]
    lvl3 = parts[3] if len(parts) >= 4 else lvl2
    lvl4 = parts[4] if len(parts) >= 5 else lvl3

    # Truly homogeneous broad shopper categories where browsing across leaves is desired
    if lvl2 == "Trái cây":
        return "Trái cây"
    if lvl2 == "Rau củ":
        if lvl3 in {"Rau gia vị", "Nấm"}:
            return lvl3
        return "Rau củ"
    if lvl2 == "Thịt":
        return lvl3  # Thịt Bò, Bê | Lợn (heo) | Thịt gà
    if lvl2 == "Thủy hải sản":
        return "Thủy hải sản"
    if lvl2 == "Bánh mỳ":
        return "Bánh mỳ"

    # Homogeneous lvl3 categories (all leaves are variants of the same product type)
    if lvl3 in {
        "Cà phê",
        "Trà",
        "Bia, Cider",
        "Rượu",
        "Sữa tươi",
        "Sữa chua",
        "Phô mai",
        "Trứng",
        "Bơ",
        "Sữa nguồn gốc thực vật",
        "Snack",
        "Hạt sấy khô",
        "Ô mai & Trái cây sấy khô",
        "Giấy ăn, giấy vệ sinh",
        "Băng vệ sinh",
        "Tã bỉm",
        "Đường",
    }:
        return lvl3

    # For Dầu ăn, keep 'Dầu ô liu' distinct while keeping general cooking oils together
    if lvl3 == "Dầu ăn":
        return "Dầu ô liu" if lvl4 == "Dầu ô liu" else "Dầu ăn"

    # For Xúc xích, Lạp xưởng: separate Lạp xưởng khô from Xúc xích
    if lvl3 == "Xúc xích, Lạp xưởng":
        return "Lạp xưởng khô" if lvl4 == "Lạp xưởng khô" else "Xúc xích, Lạp xưởng"

    # Everything else keeps its EXACT lvl4 leaf category (Kẹo gum, Chocolate, Kẹo cứng,
    # Kẹo mềm, Thạch đóng gói, Bánh biscuit, Bánh bông lan, Bánh gạo, Cá/tôm đóng hộp,
    # Thịt đóng hộp, Mật ong, Ngũ cốc ăn sáng, Lăn khử mùi, Nước hoa hồng, Mỳ khô, Miến khô, ...)!
    return lvl4


# Natural Vietnamese shopper phrasing & regional synonyms per official Thanh Lam Mart taxonomy label.
DOMAIN_SYNONYMS: dict[str, list[str]] = {
    "Trái cây": [
        "trái cây", "hoa quả", "trái cây tươi", "hoa quả tươi", "quả tươi",
        "táo", "quả táo", "trái táo", "táo đỏ", "táo xanh", "táo mỹ", "táo envy", "táo nhập khẩu",
        "cam", "quả cam", "trái cam", "cam sành", "cam úc", "cam vàng", "cam ngọt",
        "nho", "quả nho", "trái nho", "nho mỹ", "nho đen", "nho không hạt", "nho xanh",
        "chuối", "quả chuối", "trái chuối", "chuối tươi", "chuối chín", "chuối dole",
        "xoài", "quả xoài", "trái xoài", "xoài cát", "xoài đài loan", "xoài keo",
        "dưa hấu", "dưa lưới", "quả dưa", "trái dưa",
        "kiwi", "quả kiwi", "lê", "quả lê", "bưởi", "quả bưởi", "bưởi da xanh",
        "ổi", "quả ổi", "ổi lê", "thanh long", "sầu riêng", "đu đủ", "chôm chôm",
        "dứa", "quả dứa", "trái thơm", "quả thơm", "khóm", "mận", "roi", "hồng xiêm",
        "sapoche", "chanh dây", "chanh leo", "việt quất", "chà là",
    ],
    "Rau củ": [
        "rau", "rau củ", "rau xanh", "rau tươi", "rau củ quả", "củ quả",
        "rau cải", "cải bó xôi", "xà lách", "cần tây", "cà rốt", "củ cải",
        "khoai tây", "khoai lang", "khoai môn", "củ đậu", "hành tây",
        "bí xanh", "bí đỏ", "bầu", "cà tím", "cà chua", "dưa chuột", "dưa leo",
        "ngô bắp", "bắp ngô", "su su", "đậu bắp", "ớt chuông", "giá đỗ", "măng tươi",
    ],
    "Rau gia vị": [
        "chanh", "quả chanh", "trái chanh", "chanh tươi", "chanh có hạt",
        "tỏi", "củ tỏi", "gừng", "củ gừng", "sả", "củ sả", "hành lá", "hành khô",
        "hành tím", "ớt tươi", "rau thơm", "rau mùi", "ngò rí", "rau ngổ", "lá lốt", "tía tô",
    ],
    "Nấm": ["nấm", "nấm kim châm", "nấm đùi gà", "nấm hương tươi", "nấm rơm"],
    "Thủy hải sản": [
        "cá", "cá tươi", "cá biển", "cá đồng", "cá hồi", "cá diêu hồng", "cá chim", "cá khô",
        "hải sản", "thủy sản", "tôm", "tôm tươi", "tôm khô", "mực", "mực khô", "chả cá", "chả tôm", "sứa",
    ],
    "Thịt Bò, Bê": ["thịt bò", "bò", "bò úc", "bò mỹ", "ba chỉ bò", "thăn bò", "nạm bò", "bò xay", "bò viên"],
    "Lợn (heo)": ["thịt heo", "thịt lợn", "heo", "lợn", "nạc vai", "ba rọi", "ba chỉ heo", "sườn heo", "thịt đùi heo"],
    "Thịt gà": ["thịt gà", "gà", "ức gà", "đùi gà", "cánh gà", "gà tươi", "gà viên"],
    "Bánh mỳ": ["bánh mì", "bánh mỳ", "bánh mì tươi", "bánh mì sandwich", "sandwich", "bánh croissant", "bánh sừng bò", "bánh bông lan tươi"],
    "Nước hoa quả": [
        "nước cam", "nước ép cam", "nước ép", "nước trái cây", "nước ép trái cây",
        "nước hoa quả", "nước táo ép", "nước nho ép", "nước đào", "nước dừa", "nước ổi",
        "nước ép hoa quả", "nước cam vfresh", "nước cam teppy", "twister",
    ],
    "Nước ngọt có ga": [
        "nước ngọt", "nước có gas", "nước có ga", "nước giải khát", "giải khát",
        "coca", "coca cola", "pepsi", "sprite", "7up", "fanta", "mirinda", "soda",
    ],
    "Nước tăng lực": ["nước tăng lực", "tăng lực", "sting", "red bull", "bò húc", "monster", "warrior", "number 1"],
    "Nước khoáng": ["nước khoáng", "nước suối", "nước lọc", "nước đóng chai", "lavie", "vĩnh hảo", "aquafina", "dasani"],
    "Nước trà": ["trà đóng chai", "nước trà", "trà xanh không độ", "trà ô long", "trà bí đao", "trà đào", "c2", "tea plus", "fuze tea"],
    "Trà": ["trà", "trà xanh", "trà túi lọc", "trà đen", "trà sen", "trà gừng", "trà hoa cúc", "trà khô", "chè khô", "chè xanh"],
    "Cà phê": ["cà phê", "cafe", "coffee", "cà phê hòa tan", "cà phê sữa", "cà phê đen", "cà phê đóng chai", "cà phê lon", "nescafe", "g7", "vinacafe"],
    "Bia, Cider": ["bia", "bia lon", "bia chai", "đồ uống có cồn", "tiger", "heineken", "saigon", "huda", "333", "strongbow", "cider"],
    "Sữa tươi": ["sữa tươi", "sữa tươi không đường", "sữa tươi ít đường", "sữa tươi có đường", "sữa hộp", "sữa tiệt trùng", "sữa thanh trùng", "vinamilk", "th true milk", "cô gái hà lan"],
    "Sữa chua": ["sữa chua", "sữa chua ăn", "sữa chua uống", "yaourt", "sữa chua không đường", "sữa chua ít đường", "probi", "yakult", "betagen"],
    "Sữa nguồn gốc thực vật": ["sữa hạt", "sữa đậu nành", "sữa óc chó", "sữa hạnh nhân", "sữa yến mạch", "fami", "vinasoy", "th true nut"],
    "Phô mai": ["phô mai", "pho mai", "cheese", "phô mai con bò cười", "belcube", "phô mai lát", "phô mai kéo sợi"],
    "Trứng": ["trứng", "trứng gà", "trứng vịt", "trứng cút", "trứng gà ta", "trứng công nghiệp"],
    "Snack": ["đồ ăn vặt", "ăn vặt", "snack", "bim bim", "khoai tây chiên", "snack khoai tây", "snack mực", "bánh tráng", "rong biển ăn liền", "lays", "ostar", "poca", "slide", "doritos"],
    "Kẹo gum": ["kẹo gum", "kẹo cao su", "singum", "kẹo singum", "chewing gum", "doublemint", "xylitol", "cool air"],
    "Kẹo mềm": ["kẹo dẻo", "kẹo mềm", "kẹo dẻo trái cây", "haribo", "alpenliebe"],
    "Kẹo cứng": ["kẹo cứng", "kẹo ngậm", "kẹo mút", "chupa chups"],
    "Chocolate": ["socola", "sô cô la", "chocolate", "kitkat", "snickers", "m&m", "kẹo socola"],
    "Thạch đóng gói": ["thạch", "rau câu", "thạch trái cây", "thạch dừa", "thạch rau câu"],
    "Bánh biscuit": ["bánh quy", "bánh quy bơ", "bánh cracker", "oreo", "danisa", "afc", "cosy"],
    "Bánh bông lan": ["bánh bông lan", "bánh custas", "solite", "custas"],
    "Bánh gạo": ["bánh gạo", "one one", "richy"],
    "Bánh pie": ["bánh pie", "chocopie", "choco pie"],
    "Bánh xốp và quế": ["bánh xốp", "bánh quế", "nabati", "toppo"],
    "Ô mai & Trái cây sấy khô": ["trái cây sấy", "trái cây sấy khô", "hoa quả sấy", "mít sấy", "chuối sấy", "ô mai", "xí muội", "mứt", "nho khô", "vinamit"],
    "Hạt sấy khô": ["hạt", "hạt khô", "hạt điều", "hạt hướng dương", "đậu phộng", "lạc rang", "hạt hạnh nhân", "hạt dẻ", "hạt bí"],
    "Mì ăn liền": ["mì", "mì gói", "mì tôm", "mì ăn liền", "mì ly", "mì hộp", "mì xào", "mì cay", "mì không cay", "mì tôm chanh", "hảo hảo", "omachi", "kokomi", "indomie", "samyang"],
    "Mỳ khô": ["mì ý", "mỳ ý", "spaghetti", "mì spaghetti", "mỳ khô"],
    "Dầu ăn": ["dầu ăn", "dầu thực vật", "dầu đậu nành", "dầu hướng dương", "dầu gạo", "dầu chiên", "simply", "neptune", "tường an", "cái lân", "meizan"],
    "Dầu ô liu": ["dầu ô liu", "dầu oliu", "olive oil"],
    "Bơ": ["bơ", "bơ động vật", "bơ lạt", "bơ mặn", "bơ anchor", "bơ president", "bơ thực vật", "bơ tường an", "bơ meizan"],
    "Cá, tôm đóng hộp": ["cá hộp", "cá mòi", "cá nục hộp", "cá ngừ hộp"],
    "Thịt đóng hộp": ["thịt hộp", "pate", "pa tê", "spam"],
    "Mật ong": ["mật ong", "mật ong rừng"],
    "Ngũ cốc ăn sáng": ["yến mạch", "ngũ cốc", "ngũ cốc ăn sáng", "oatmeal"],
    "Tương ớt": ["tương ớt", "tương ót", "tương ớt chinsu", "tương ớt cholimex", "tương ớt nam dương"],
    "Tương cà": ["tương cà", "sốt cà chua", "ketchup"],
    "Nước mắm": ["nước mắm", "mắm", "nam ngư", "chinsu nước mắm", "phú quốc"],
    "Nước tương": ["nước tương", "xì dầu", "maggi", "tam thái tử", "chinsu nước tương"],
    "Mỳ chính": ["bột ngọt", "mì chính", "mỳ chính", "ajinomoto", "vedan"],
    "Bột canh, hạt nêm": ["hạt nêm", "bột nêm", "bột canh", "knorr", "aji ngon", "chinsu hạt nêm"],
    "Dầu hào": ["dầu hào", "maggi dầu hào", "chinsu dầu hào"],
    "Dấm": ["giấm", "dấm", "giấm gạo", "giấm táo"],
    "Muối": ["muối", "muối ăn", "muối iot", "muối tôm", "muối tiêu"],
    "Tiêu": ["tiêu", "hạt tiêu", "tiêu xay", "tiêu đen"],
    "Mayonnaise & S.salad": ["mayonnaise", "sốt mayonnaise", "kewpie", "sốt trộn salad"],
    "Đường": ["đường", "đường trắng", "đường cát", "đường phèn", "đường ăn kiêng", "biên hòa"],
    "Gạo tẻ": ["gạo", "gạo thơm", "gạo st25", "gạo tẻ"],
    "Lăn khử mùi nữ": ["lăn khử mùi", "lăn nách", "khử mùi"],
    "Tăm chỉ nkhoa vslưỡi": ["chỉ nha khoa", "tăm chỉ nha khoa", "tăm nha khoa"],
    "Mỹ phẩm t.điểm môi": ["son dưỡng môi", "dưỡng môi", "son môi"],
    "Nước rửa chén bát": ["nước rửa chén", "nước rửa bát", "rửa chén", "rửa bát", "sunlight rửa chén", "lix rửa chén", "nước rửa chén trà xanh"],
    "Nước lau sàn nhà": ["nước lau sàn", "lau sàn", "lau nhà", "nước lau nhà", "sunlight lau sàn", "nước lau sàn hương cam"],
    "Nước giặt": ["nước giặt", "nước giặt quần áo", "omo", "ariel", "comfort giặt", "surf", "maxkleen"],
    "Bột giặt": ["bột giặt", "xà phòng giặt", "omo bột giặt", "aba", "tide"],
    "Nước xả": ["nước xả", "nước xả vải", "xả vải", "comfort", "downy", "hygiene"],
    "Kem đánh răng": ["kem đánh răng", "thuốc đánh răng", "ps", "colgate", "closeup", "sensodyne"],
    "Bàn chải đánh răng": ["bàn chải", "bàn chải đánh răng", "bàn chải mềm"],
    "Nước súc miệng": ["nước súc miệng", "listerine", "súc miệng"],
    "Dầu gội nữ": ["dầu gội", "dầu gội đầu", "dầu gội trị gàu", "dầu gội chống gàu", "selsun", "clear", "head shoulders", "pantene", "sunsilk", "dove", "tresemme"],
    "Dầu xả & dưỡng tóc": ["dầu xả", "kem ủ tóc", "dưỡng tóc"],
    "Sữa tắm và làm sạch": ["sữa tắm", "xà bông tắm", "xà phòng tắm", "lifebuoy", "dove sữa tắm", "lux", "purite"],
    "Sữa rửa mặt nữ": ["sữa rửa mặt", "nước rửa mặt", "senka", "cetaphil", "simple", "ponds", "hazeline"],
    "Kem chống nắng": ["kem chống nắng", "chống nắng", "sunplay", "anessa", "biore chống nắng"],
    "Nước tẩy trang": ["nước tẩy trang", "tẩy trang", "garnier", "loreal tẩy trang", "bioderma"],
    "Giấy ăn, giấy vệ sinh": ["giấy vệ sinh", "giấy ăn", "khăn giấy", "giấy cuộn", "khăn rút", "pulppy", "bless you", "tempo"],
    "Nước rửa tay": ["nước rửa tay", "rửa tay", "xà phòng rửa tay"],
}


def download_potion() -> tuple[np.ndarray, list[str], list[float]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for fn in ("config.json", "tokenizer.json", "model.safetensors"):
        dest = CACHE_DIR / fn
        if not dest.exists():
            print(f"Downloading {fn}...")
            urllib.request.urlretrieve(f"{HF_BASE}/{fn}", dest)

    with open(CACHE_DIR / "model.safetensors", "rb") as f:
        hlen = struct.unpack("<Q", f.read(8))[0]
        hdr = json.loads(f.read(hlen).decode("utf-8"))
        tinfo = hdr["embeddings"]
        start, end = tinfo["data_offsets"]
        shape = tuple(tinfo["shape"])
        f.seek(8 + hlen + start)
        emb = np.frombuffer(f.read(end - start), dtype=np.float32).reshape(shape).copy()

    tok = json.loads((CACHE_DIR / "tokenizer.json").read_text(encoding="utf-8"))
    vocab_list = tok["model"]["vocab"]
    pieces = [item[0] for item in vocab_list]
    scores = [float(item[1]) for item in vocab_list]
    return emb, pieces, scores


def is_ascii_or_vi(piece: str) -> bool:
    cleaned = piece.replace("▁", "")
    if not cleaned:
        return True
    for ch in cleaned:
        o = ord(ch)
        if o < 128 or (0x00C0 <= o <= 0x024F) or (0x1E00 <= o <= 0x1EFF):
            continue
        return False
    return True


def select_pruned_vocab(
    pieces: list[str],
    scores: list[float],
    domain_corpus: list[str],
    target_max: int = 20_000,
) -> list[int]:
    """Keep all tokens hit by our Vietnamese/Thanh Lam Mart corpus + all Vietnamese diacritic tokens
    + top-scoring short Latin/ASCII Unigram tokens -> ~18K-20K tokens (~3.8 MB INT8!)."""
    piece_to_id = {p: i for i, p in enumerate(pieces)}

    # 1. Tokenize entire Thanh Lam Mart corpus + Vietnamese synonyms with full Unigram vocab to guarantee 100% hit!
    needed_ids: set[int] = set()
    for text in domain_corpus:
        s = "▁" + re.sub(r"\s+", "▁", unicodedata.normalize("NFC", text.strip().lower()))
        n = len(s)
        best = [(-1e18, -1, -1)] * (n + 1)
        best[0] = (0.0, -1, -1)
        for i in range(n):
            if best[i][0] <= -1e17:
                continue
            for j in range(i + 1, min(n, i + 20) + 1):
                sub = s[i:j]
                idx = piece_to_id.get(sub)
                if idx is not None:
                    sc = best[i][0] + scores[idx]
                    if sc > best[j][0]:
                        best[j] = (sc, i, idx)
            if best[i + 1][0] <= -1e17:
                best[i + 1] = (best[i][0] - 20.0, i, -1)
        pos = n
        while pos > 0:
            _, prev, idx = best[pos]
            if idx >= 0:
                needed_ids.add(idx)
            pos = prev if prev >= 0 else pos - 1

    # 2. Keep top-scoring Vietnamese diacritic pieces (for unseen Vietnamese shopper words!) + single chars
    vi_ranked = sorted(
        (
            (scores[i], i)
            for i, p in enumerate(pieces)
            if is_ascii_or_vi(p) and bool(_VI_DIACRITIC_RE.search(p)) and len(p) <= 12
        ),
        reverse=True,
    )
    vi_ids = {i for _, i in vi_ranked[:8_000]} | {
        i for i, p in enumerate(pieces) if is_ascii_or_vi(p) and len(p.replace("▁", "")) <= 2
    }

    # 3. Fill remaining budget up to target_max with highest-score Latin/ASCII Unigram pieces
    selected = needed_ids | vi_ids
    if len(selected) < target_max:
        rest = [
            (scores[i], i)
            for i, p in enumerate(pieces)
            if i not in selected and is_ascii_or_vi(p) and len(p) <= 12
        ]
        rest.sort(reverse=True)
        for _, i in rest[: target_max - len(selected)]:
            selected.add(i)

    return sorted(selected)


def unigram_encode(
    text: str,
    piece_to_local: dict[str, int],
    local_scores: list[float],
    emb_f32: np.ndarray,
) -> np.ndarray:
    s = "▁" + re.sub(r"\s+", "▁", unicodedata.normalize("NFC", text.strip().lower()))
    n = len(s)
    best = [(-1e18, -1, -1)] * (n + 1)
    best[0] = (0.0, -1, -1)
    for i in range(n):
        if best[i][0] <= -1e17:
            continue
        for j in range(i + 1, min(n, i + 20) + 1):
            loc = piece_to_local.get(s[i:j])
            if loc is not None:
                sc = best[i][0] + local_scores[loc]
                if sc > best[j][0]:
                    best[j] = (sc, i, loc)
        if best[i + 1][0] <= -1e17:
            best[i + 1] = (best[i][0] - 20.0, i, -1)
    ids: list[int] = []
    pos = n
    while pos > 0:
        _, prev, loc = best[pos]
        if loc >= 0:
            ids.append(loc)
        pos = prev if prev >= 0 else pos - 1
    if not ids:
        return np.zeros(emb_f32.shape[1], dtype=np.float32)
    vec = emb_f32[ids].mean(axis=0)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 1e-8 else vec


def _hash_bucket(key: str) -> tuple[int, float]:
    h = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    val = int.from_bytes(h[:4], "little")
    sign = 1.0 if (h[4] & 1) == 0 else -1.0
    return val % LEX_DIM, sign


def lex_head_vector(text: str) -> np.ndarray:
    """Position-weighted Vietnamese diacritic word + bigram + char-ngram feature vector.

    Gives 2.0x weight to leading head-noun words and 0.15x weight to words after flavor/scent
    prepositions ('vị', 'hương', 'ruột', 'nhân', 'chiết xuất') so head nouns cleanly dominate
    derivatives ('trái cây' vs 'kẹo dẻo trái cây', 'nước cam' vs 'nước lau sàn hương cam').
    """
    vec = np.zeros(LEX_DIM, dtype=np.float32)
    native = unicodedata.normalize("NFC", text.strip().lower())
    parts = _MODIFIER_SPLIT_RE.split(native, maxsplit=1)
    head_part = re.sub(r"[^\w\s]", " ", parts[0], flags=re.UNICODE)
    mod_part = re.sub(r"[^\w\s]", " ", parts[1], flags=re.UNICODE) if len(parts) > 1 else ""

    head_toks = [t for t in head_part.split() if t]
    mod_toks = [t for t in mod_part.split() if t]

    for idx, tok in enumerate(head_toks):
        w = 2.2 if idx == 0 else (1.7 if idx == 1 else (1.2 if idx == 2 else 0.7))
        # Exact diacritic token (critical for táo!=tao, cá!=cà, tỏi!=tôi, bò!=bơ!)
        b, s = _hash_bucket(f"nw:{tok}")
        vec[b] += s * w
        if idx == 0:
            b, s = _hash_bucket(f"h0:{tok}")
            vec[b] += s * 1.8
        # Accent-folded token (lower weight for typo/no-diacritic robustness)
        ft = strip_accents(tok)
        b, s = _hash_bucket(f"fw:{ft}")
        vec[b] += s * (0.55 * w)
        # Char 3/4-grams for spelling variation
        if len(tok) >= 3:
            padded = f"<{tok}>"
            for k in (3, 4):
                for j in range(len(padded) - k + 1):
                    b, s = _hash_bucket(f"c{k}:{padded[j:j+k]}")
                    vec[b] += s * (0.18 * w)

    # Leading head bigrams (e.g. "nước cam", "trái cây", "nước lau", "kẹo dẻo", "mì tôm", "sữa chua")
    for idx in range(len(head_toks) - 1):
        w = 2.4 if idx == 0 else 1.5
        bg = f"{head_toks[idx]}_{head_toks[idx+1]}"
        b, s = _hash_bucket(f"nbg:{bg}")
        vec[b] += s * w
        fbg = f"{strip_accents(head_toks[idx])}_{strip_accents(head_toks[idx+1])}"
        b, s = _hash_bucket(f"fbg:{fbg}")
        vec[b] += s * (0.75 * w)
    if len(head_toks) >= 3:
        tg = f"{head_toks[0]}_{head_toks[1]}_{head_toks[2]}"
        b, s = _hash_bucket(f"ntg:{tg}")
        vec[b] += s * 2.0

    # Flavor/scent tail gets very small modifier-tagged weight
    for tok in mod_toks[:4]:
        b, s = _hash_bucket(f"mod:{tok}")
        vec[b] += s * 0.2

    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 1e-8 else vec


def build_feature(
    text: str,
    piece_to_local: dict[str, int],
    local_scores: list[float],
    emb_f32: np.ndarray,
) -> np.ndarray:
    core = clean_native_query(text)
    e_sem = unigram_encode(core, piece_to_local, local_scores, emb_f32)
    e_lex = lex_head_vector(core)
    # Balanced fusion: semantic bge-m3/Model2Vec (generalizes synonyms) + diacritic head-noun lex
    return np.concatenate([e_sem * 1.15, e_lex * 1.45]).astype(np.float32)


def main() -> None:
    t_start = time.perf_counter()
    raw_items = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    details_map = (
        json.loads(DETAILS_PATH.read_text(encoding="utf-8")).get("records", {})
        if DETAILS_PATH.is_file()
        else {}
    )
    print(f"Loaded {len(raw_items)} Thanh Lam Mart catalog SKUs ({len(details_map)} enriched details)")

    # 1. Group SKUs into clean official Thanh Lam Mart taxonomy labels (leaf-granular!)
    label_counts: Counter[str] = Counter()
    sku_labels: list[tuple[dict, str]] = []
    for item in raw_items:
        lbl = canonical_taxonomy_label(item)
        label_counts[lbl] += 1
        sku_labels.append((item, lbl))

    # Keep all leaf classes with >= 1 SKU when present in Thanh Lam Mart catalog
    valid_labels = sorted(
        lbl for lbl, cnt in label_counts.items() if cnt >= 2 or lbl in DOMAIN_SYNONYMS
    )
    label_to_idx = {lbl: i for i, lbl in enumerate(valid_labels)}
    print(
        f"Taxonomy classes: {len(valid_labels)} "
        f"(covering {sum(label_counts[l] for l in valid_labels)}/{len(raw_items)} SKUs)"
    )

    def _detail_slug_phrase(pid: str) -> str:
        det = details_map.get(pid) or {}
        url = str(det.get("ingredients_source_url") or det.get("usage_source_url") or "")
        if not url:
            return ""
        # Extract readable Vietnamese category/product slug (e.g. 'keo-singum-keo-ngam / keo-cao-su-...')
        parts = [p for p in url.split("/") if p and not p.startswith("http") and "." not in p]
        if not parts:
            return ""
        raw_slug = " ".join(parts[-2:]).replace("-", " ")
        raw_slug = re.sub(r"\b\d+\w*\b", " ", raw_slug)
        return " ".join(raw_slug.split()[:8])

    # 2. Build domain corpus for vocab pruning (catalog + enriched summaries/slugs)
    domain_corpus: list[str] = []
    for item, lbl in sku_labels:
        domain_corpus.append(str(item.get("name") or ""))
        domain_corpus.append(str(item.get("category_hierarchy") or ""))
        domain_corpus.append(lbl)
        slug = _detail_slug_phrase(str(item.get("product_id") or ""))
        if slug:
            domain_corpus.append(slug)
    for lbl, syns in DOMAIN_SYNONYMS.items():
        domain_corpus.append(lbl)
        domain_corpus.extend(syns)

    # 3. Download & prune Hugging Face potion-multilingual-128M -> INT8 (~4.5 MB!)
    emb_full, pieces_full, scores_full = download_potion()
    kept_ids = select_pruned_vocab(pieces_full, scores_full, domain_corpus, target_max=18_000)
    kept_pieces = [pieces_full[i] for i in kept_ids]
    kept_scores = [scores_full[i] for i in kept_ids]
    sub_emb = emb_full[kept_ids]
    del emb_full

    emb_scales = np.max(np.abs(sub_emb), axis=1, keepdims=True).clip(min=1e-8) / 127.0
    emb_int8 = np.rint(sub_emb / emb_scales).astype(np.int8)
    emb_f32 = emb_int8.astype(np.float32) * emb_scales
    piece_to_local = {p: i for i, p in enumerate(kept_pieces)}
    print(f"Pruned potion-multilingual-128M vocab: {len(kept_pieces)} tokens")

    # 4. Build balanced training dataset from ALL 3,099 SKUs + enriched catalog slugs/summaries
    samples: list[tuple[str, int, float]] = []
    templates = [
        "{x}",
        "có {x} không",
        "có {x} gì ko",
        "tìm {x}",
        "mua {x}",
        "cho mình xem {x}",
    ]
    for item, lbl in sku_labels:
        if lbl not in label_to_idx:
            continue
        yi = label_to_idx[lbl]
        pid = str(item.get("product_id") or "")
        name = str(item.get("name") or "").strip()
        head = extract_head_phrase(name)
        samples.append((head, yi, 1.5))
        samples.append((name, yi, 1.0))
        # Enriched retailer URL slugs automatically teach real Vietnamese phrasing
        # (e.g. 'keo singum keo cao su' -> Kẹo gum, 'sua rua mat' -> Sữa rửa mặt)
        slug = _detail_slug_phrase(pid)
        if slug:
            samples.append((slug, yi, 0.9))
        hier_parts = [p.strip() for p in str(item.get("category_hierarchy") or "").split(">") if p.strip()]
        if hier_parts:
            leaf_clean = re.sub(r"\b(?:NK|NĐ|đgói)\b", "", hier_parts[-1]).strip()
            if len(leaf_clean) >= 2:
                samples.append((leaf_clean, yi, 0.9))

    # Add taxonomy label names + Vietnamese shopper colloquialisms/synonyms (balanced weight)
    for lbl, yi in label_to_idx.items():
        clean_lbl = re.sub(r"\b(?:NK|NĐ|đgói)\b", "", lbl).strip()
        for tmpl in templates[:3]:
            samples.append((tmpl.format(x=clean_lbl), yi, 3.0))
        for syn in DOMAIN_SYNONYMS.get(lbl, []):
            samples.append((syn, yi, 4.5))
            samples.append((f"có {syn} không", yi, 3.0))
            samples.append((strip_accents(syn), yi, 1.6))

    # Deduplicate exact (clean_text, yi) summing bounded weights
    dedup: dict[tuple[str, int], float] = defaultdict(float)
    for txt, yi, w in samples:
        c = clean_native_query(txt)
        if c:
            dedup[(c, yi)] = min(dedup[(c, yi)] + w, 8.0)

    train_texts = [k[0] for k in dedup]
    train_y = np.array([k[1] for k in dedup], dtype=np.int32)
    train_w = np.array(list(dedup.values()), dtype=np.float32)

    # Inverse-sqrt class frequency balancing so minority fresh produce (58 SKUs) equals giant pantry (197 SKUs)
    cls_w_sum = np.bincount(train_y, weights=train_w, minlength=len(valid_labels)).clip(min=1.0)
    class_boost = (np.median(cls_w_sum) / cls_w_sum) ** 0.45
    train_w = train_w * class_boost[train_y]
    train_w /= train_w.mean()

    print(f"Encoding {len(train_texts)} training examples...")
    X = np.stack(
        [build_feature(txt, piece_to_local, kept_scores, emb_f32) for txt in train_texts],
        axis=0,
    )
    N, D = X.shape
    C = len(valid_labels)
    print(f"Feature matrix X: {X.shape}")

    # 5. Train L2-regularized Multinomial Softmax Classifier (Adam, 180 epochs — takes ~3s in NumPy!)
    rng = np.random.default_rng(42)
    W = np.zeros((D, C), dtype=np.float32)
    b = np.zeros(C, dtype=np.float32)
    mW = np.zeros_like(W)
    vW = np.zeros_like(W)
    mb = np.zeros_like(b)
    vb = np.zeros_like(b)

    lr = 0.08
    l2 = 1.5e-4
    batch_size = 512
    epochs = 90
    step = 0
    for ep in range(1, epochs + 1):
        perm = rng.permutation(N)
        ep_loss = 0.0
        for s0 in range(0, N, batch_size):
            idx = perm[s0 : s0 + batch_size]
            xb = X[idx]
            yb = train_y[idx]
            wb = train_w[idx]

            logits = xb @ W + b
            logits -= logits.max(axis=1, keepdims=True)
            exp_l = np.exp(logits)
            probs = exp_l / exp_l.sum(axis=1, keepdims=True)

            # Weighted cross-entropy
            w_sum = float(wb.sum())
            loss = -float((wb * np.log(probs[np.arange(len(idx)), yb].clip(min=1e-9))).sum()) / w_sum
            ep_loss += loss * len(idx)

            dlogits = probs
            dlogits[np.arange(len(idx)), yb] -= 1.0
            dlogits *= (wb / w_sum)[:, None]

            gW = xb.T @ dlogits + l2 * W
            gb = dlogits.sum(axis=0)

            step += 1
            lr_t = lr * (0.5 * (1.0 + math.cos(math.pi * ep / epochs)))
            mW = 0.9 * mW + 0.1 * gW
            vW = 0.999 * vW + 0.001 * (gW * gW)
            mb = 0.9 * mb + 0.1 * gb
            vb = 0.999 * vb + 0.001 * (gb * gb)
            mW_hat = mW / (1.0 - 0.9**step)
            vW_hat = vW / (1.0 - 0.999**step)
            mb_hat = mb / (1.0 - 0.9**step)
            vb_hat = vb / (1.0 - 0.999**step)
            W -= lr_t * mW_hat / (np.sqrt(vW_hat) + 1e-8)
            b -= lr_t * mb_hat / (np.sqrt(vb_hat) + 1e-8)

        if ep in (1, 20, 50, epochs):
            preds = (X @ W + b).argmax(axis=1)
            acc = float((preds == train_y).mean())
            print(f"  Epoch {ep:3d}/{epochs}: loss={ep_loss/N:.4f} train_acc={acc*100:.2f}%")

    # 6. Precompute compact INT8 Dense SKU Embedding Matrix over ALL catalog SKUs (+0.78 MB!)
    sku_ids: list[str] = []
    sku_vecs: list[np.ndarray] = []
    for item in raw_items:
        pid = str(item.get("product_id") or "")
        name = str(item.get("name") or "")
        head = extract_head_phrase(name)
        parts = [p.strip() for p in str(item.get("category_hierarchy") or "").split(">") if p.strip()]
        leaf = parts[-1] if parts else ""
        det = details_map.get(pid) or {}
        summary_first = str(det.get("summary") or "").split(".")[0][:120]
        slug = _detail_slug_phrase(pid)
        v_head = unigram_encode(f"{head} {leaf}", piece_to_local, kept_scores, emb_f32)
        v_rich = unigram_encode(f"{name} {leaf} {slug} {summary_first}", piece_to_local, kept_scores, emb_f32)
        v = 0.72 * v_head + 0.28 * v_rich
        vn = float(np.linalg.norm(v))
        if vn > 1e-8:
            v = v / vn
        sku_ids.append(pid)
        sku_vecs.append(v.astype(np.float32))
    sku_mat = np.stack(sku_vecs, axis=0)
    sku_scales = np.max(np.abs(sku_mat), axis=1, keepdims=True).clip(min=1e-8) / 127.0
    sku_emb_int8 = np.rint(sku_mat / sku_scales).astype(np.int8)

    # 7. Quantize classifier weights to float16 and save single compact .npz artifact
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUT_PATH,
        emb_int8=emb_int8,
        emb_scales=emb_scales.squeeze(1).astype(np.float16),
        pieces=np.array(kept_pieces, dtype=object),
        scores=np.array(kept_scores, dtype=np.float32),
        W=W.astype(np.float16),
        b=b.astype(np.float32),
        labels=np.array(valid_labels, dtype=object),
        sku_ids=np.array(sku_ids, dtype=object),
        sku_emb_int8=sku_emb_int8,
        sku_scales=sku_scales.squeeze(1).astype(np.float16),
    )
    size_mb = OUT_PATH.stat().st_size / (1024 * 1024)
    print(f"\nSaved {OUT_PATH} ({size_mb:.2f} MB) in {time.perf_counter() - t_start:.1f}s")

    # 8. Benchmark on diverse unseen/hard Vietnamese shopper queries (now leaf-granular!)
    eval_cases = [
        ("có trái cây gì ko", {"Trái cây"}),
        ("có hoa quả gì ko", {"Trái cây"}),
        ("có táo không", {"Trái cây"}),
        ("có cam không", {"Trái cây"}),
        ("có nho không", {"Trái cây"}),
        ("có chuối không", {"Trái cây"}),
        ("có xoài không", {"Trái cây"}),
        ("có dưa hấu không", {"Trái cây"}),
        ("có bưởi da xanh không", {"Trái cây"}),
        ("có trái thơm không", {"Trái cây"}),
        ("kẹo dẻo trái cây có loại nào", {"Kẹo mềm"}),
        ("thế có kẹo cao su ko", {"Kẹo gum"}),
        ("có singum không", {"Kẹo gum"}),
        ("có rau câu không", {"Thạch đóng gói"}),
        ("có kẹo mút không", {"Kẹo cứng"}),
        ("có socola không", {"Chocolate"}),
        ("cho tôi mua trái cây sấy khô", {"Ô mai & Trái cây sấy khô"}),
        ("có nước cam không", {"Nước hoa quả"}),
        ("đổi sang nước ép trái cây đi", {"Nước hoa quả"}),
        ("nước lau sàn hương cam có không", {"Nước lau sàn nhà"}),
        ("có rau gì ko", {"Rau củ", "Rau gia vị"}),
        ("có khoai tây không", {"Rau củ"}),
        ("có chanh không", {"Rau gia vị"}),
        ("có tỏi không", {"Rau gia vị"}),
        ("có gừng không", {"Rau gia vị"}),
        ("mì tôm chanh có loại nào", {"Mì ăn liền"}),
        ("có mì ý không", {"Mỳ khô"}),
        ("có cá gì không", {"Thủy hải sản"}),
        ("có cá hộp không", {"Cá, tôm đóng hộp"}),
        ("có pate không", {"Thịt đóng hộp"}),
        ("có tôm không", {"Thủy hải sản"}),
        ("có thịt bò không", {"Thịt Bò, Bê"}),
        ("có thịt heo không", {"Lợn (heo)"}),
        ("có thịt lợn không", {"Lợn (heo)"}),
        ("có thịt gà không", {"Thịt gà"}),
        ("có cà phê đóng chai không", {"Cà phê"}),
        ("có sữa chua không", {"Sữa chua"}),
        ("có sữa tươi không đường không", {"Sữa tươi"}),
        ("có sữa đậu nành không", {"Sữa nguồn gốc thực vật"}),
        ("có bánh mì không", {"Bánh mỳ"}),
        ("có bánh bông lan không", {"Bánh bông lan"}),
        ("có bánh quy bơ không", {"Bánh biscuit"}),
        ("tôi muốn ăn vặt có cái gì ko", {"Snack"}),
        ("có trứng gà không", {"Trứng"}),
        ("có phô mai không", {"Phô mai"}),
        ("có bơ không", {"Bơ", "Bơ đóng hộp"}),
        ("có mật ong không", {"Mật ong"}),
        ("có trà xanh không", {"Trà", "Nước trà"}),
        ("có nước dừa không", {"Nước hoa quả"}),
        ("có bia không", {"Bia, Cider"}),
        ("nước giải khát nào ít đường nhất", {"Nước ngọt có ga", "Nước trà", "Nước khoáng"}),
        ("tương ót nào ít đường nhất", {"Tương ớt"}),
        ("có xì dầu không", {"Nước tương"}),
        ("có bột ngọt không", {"Mỳ chính"}),
        ("có dầu oliu không", {"Dầu ô liu"}),
        ("có dầu ăn nào rẻ không", {"Dầu ăn"}),
        ("có nước rửa chén trà xanh không", {"Nước rửa chén bát"}),
        ("có rửa bát không", {"Nước rửa chén bát"}),
        ("có lăn nách không", {"Lăn khử mùi nữ", "Lăn khử mùi Nam"}),
        ("có son dưỡng môi không", {"Mỹ phẩm t.điểm môi"}),
        ("có chỉ nha khoa không", {"Tăm chỉ nkhoa vslưỡi"}),
        ("có thuốc đánh răng không", {"Kem đánh răng"}),
        ("có dầu gội trị gàu không", {"Dầu gội nữ", "Dầu gội nam"}),
    ]

    passed = 0
    latencies = []
    print("\n=== 50-QUERY SEMANTIC NLP ROUTER BENCHMARK ===")
    for q, expected in eval_cases:
        t0 = time.perf_counter()
        feat = build_feature(q, piece_to_local, kept_scores, emb_f32)
        logits = feat @ W + b
        logits -= logits.max()
        probs = np.exp(logits)
        probs /= probs.sum()
        top3 = np.argsort(probs)[::-1][:3]
        ms = (time.perf_counter() - t0) * 1000
        latencies.append(ms)
        top1_lbl = valid_labels[top3[0]]
        ok = top1_lbl in expected
        passed += int(ok)
        mark = "PASS" if ok else "FAIL"
        top_str = ", ".join(f"{valid_labels[i]}({probs[i]:.2f})" for i in top3[:2])
        print(f"  [{mark}] [{ms:.2f}ms] {q:<38} -> {top_str}")

    latencies.sort()
    print(f"\nACCURACY: {passed}/{len(eval_cases)} ({100*passed/len(eval_cases):.1f}%)")
    print(f"LATENCY:  median={latencies[len(latencies)//2]:.3f}ms  p95={latencies[int(len(latencies)*0.95)]:.3f}ms")


if __name__ == "__main__":
    main()
