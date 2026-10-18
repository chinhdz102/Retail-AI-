from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# These anchor points place compact aisle-code plaques directly over the visible
# endcap faces. The image is illustrative; only the aisle codes and names come
# from the catalog planogram. Picture coordinates are presentation data.
TAG_ANCHORS: dict[str, tuple[int, int]] = {
    # A01 belongs to the textile wall display. A02-A04 are the three
    # individual cosmetics gondolas immediately to its right.
    "A01": (275, 330),
    "A02": (347, 342),
    "A03": (477, 370),
    "A04": (604, 343),
    "A05": (891, 257),
    "A06": (431, 673),
    "A07": (596, 598),
    "A08": (714, 573),
    "A09": (861, 538),
    "A10": (996, 498),
    "A11": (1130, 459),
    "A12": (1230, 426),
    "A13": (1377, 417),
}

AISLE_COLORS = {
    "Dệt may": ("#86198f", "#f0abfc"),
    "Hoá mỹ phẩm": ("#1d4ed8", "#93c5fd"),
    "Homeline": ("#c2410c", "#fdba74"),
    "Thực phẩm công nghệ": ("#b91c1c", "#fca5a5"),
    "Thực phẩm Tươi sống, Chế biến": ("#15803d", "#86efac"),
}


def generate_endcap_overlay(
    planogram_path: Path,
    base_image_path: Path,
    output_path: Path,
) -> dict[str, object]:
    planogram = json.loads(planogram_path.read_text(encoding="utf-8"))
    aisles = planogram.get("aisles", [])
    aisle_codes = {aisle["aisle"] for aisle in aisles}
    if aisle_codes != set(TAG_ANCHORS):
        raise ValueError(
            "The concept image label layout needs positions for exactly these aisles: "
            f"{sorted(aisle_codes)}"
        )

    base_href = Path(base_image_path).resolve().as_uri()
    elements = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1536" height="1024" '
        'viewBox="0 0 1536 1024">',
        '<defs><filter id="tag-shadow" x="-25%" y="-25%" width="150%" '
        'height="160%"><feDropShadow dx="0" dy="3" stdDeviation="3" '
        'flood-color="#020617" flood-opacity=".6"/></filter></defs>',
        f'<image href="{base_href}" x="0" y="0" width="1536" height="1024"/>',
    ]

    for aisle in aisles:
        code = aisle["aisle"]
        name = aisle["sub_department"]
        x, y = TAG_ANCHORS[code]
        fill, stroke = AISLE_COLORS.get(name, ("#0f766e", "#5eead4"))
        width = 42
        height = 21
        title = html.escape(f'{code} · {name}', quote=True)
        elements.extend(
            [
                f'<g filter="url(#tag-shadow)"><title>{title}</title>',
                f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
                f'rx="3" fill="{fill}" fill-opacity=".98" stroke="#0f172a" '
                'stroke-width="2.2"/>',
                f'<rect x="{x + 1.5}" y="{y + 1.5}" width="{width - 3}" '
                f'height="{height - 3}" rx="2" fill="none" stroke="{stroke}" '
                'stroke-width=".8"/>',
                f'<text x="{x + width / 2}" y="{y + 15}" fill="#ffffff" '
                'font-family="Segoe UI,Arial,sans-serif" font-size="11.5" '
                'font-weight="800" text-anchor="middle">'
                f'{html.escape(code)}</text>',
            ]
        )
        elements.append("</g>")

    elements.append("</svg>")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(elements), encoding="utf-8")
    return {
        "aisles": len(aisles),
        "source_dataset_sha256": planogram["source_dataset_sha256"],
        "output": str(output_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add catalog-derived labels to every aisle endcap in the 3D concept."
    )
    parser.add_argument(
        "--planogram",
        type=Path,
        default=ROOT / "data" / "winmart_planogram_assignments.json",
    )
    parser.add_argument(
        "--base-image",
        type=Path,
        default=ROOT / "data" / "winmart_planogram_3d_labeled.png",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "winmart_planogram_3d_each_aisle.svg",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(
        json.dumps(
            generate_endcap_overlay(
                args.planogram.resolve(),
                args.base_image.resolve(),
                args.output.resolve(),
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
