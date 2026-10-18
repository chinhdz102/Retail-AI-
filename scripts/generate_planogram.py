from __future__ import annotations

import argparse
import csv
import html
import json
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from backend.data.catalog_import import WinMartRow, analyze_dataset

ROOT = Path(__file__).resolve().parents[1]
PLANOGRAM_VERSION = "taxonomy-blocks-v1"
BAYS_PER_AISLE = 8
SHELF_LEVELS = 5
SLOTS_PER_LEVEL = 8
SLOTS_PER_BAY = SHELF_LEVELS * SLOTS_PER_LEVEL
SLOTS_PER_AISLE = BAYS_PER_AISLE * SLOTS_PER_BAY
# L1 is the bottom shelf and L5 is the top shelf. Fill the central, easy-to-see
# levels first, but give every SKU exactly one location; there is no sales ranking.
LEVEL_ORDER = (3, 2, 4, 1, 5)

CSV_FIELDS = (
    "product_id",
    "name",
    "brand",
    "department",
    "sub_department",
    "category_code",
    "category_name",
    "aisle",
    "bay",
    "shelf_level",
    "slot",
    "location_code",
    "placement_status",
    "source_shelf_placeholder",
    "exclusion_reason",
)


def sort_text(value: str | None) -> str:
    text = (value or "").casefold().replace("đ", "d")
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def safe_text(value: str | None) -> str:
    return html.escape(value or "", quote=True)


def svg_points(points: tuple[tuple[float, float], ...]) -> str:
    return " ".join(f"{x:g},{y:g}" for x, y in points)


def svg_text(
    x: int | float,
    y: int | float,
    text: str,
    color: str,
    size: int,
    *,
    weight: str | None = None,
    anchor: str | None = None,
) -> str:
    weight_attribute = f' font-weight="{weight}"' if weight else ""
    anchor_attribute = f' text-anchor="{anchor}"' if anchor else ""
    return (
        f'<text x="{x}" y="{y}" fill="{color}" '
        'font-family="Segoe UI,Arial,sans-serif" '
        f'font-size="{size}"{weight_attribute}{anchor_attribute}>'
        f"{safe_text(text)}</text>"
    )


def category_key(row: WinMartRow) -> tuple[str, str, str, str]:
    return (
        row.department or "",
        row.sub_department or "",
        row.category_code or "",
        row.category_name or "",
    )


def location_code(aisle: int, bay: int, level: int, slot: int) -> str:
    return f"A{aisle:02d}-B{bay:02d}-L{level:02d}-P{slot:02d}"


def aisle_sign_lines(sub_department: str) -> tuple[str, ...]:
    normalized = sort_text(sub_department).replace(",", "")
    if normalized == "thuc pham cong nghe":
        return "THỰC PHẨM", "CÔNG NGHỆ"
    if normalized == "thuc pham tuoi song che bien":
        return "THỰC PHẨM", "TƯƠI SỐNG,", "CHẾ BIẾN"
    return (sub_department.upper(),)


def start_aisle(
    department: str,
    sub_department: str,
    aisle_number: int,
    aisle_summary: dict[int, dict[str, Any]],
) -> int:
    aisle_summary[aisle_number] = {
        "aisle": f"A{aisle_number:02d}",
        "department": department,
        "sub_department": sub_department,
        "product_count": 0,
        "category_codes": set(),
        "bays": set(),
    }
    return 0


def assign_products(
    accepted_rows: list[WinMartRow],
) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, Any]]]:
    families: dict[tuple[str, str, str, str], list[WinMartRow]] = defaultdict(list)
    for row in accepted_rows:
        families[category_key(row)].append(row)

    ordered_families = sorted(
        families.items(),
        key=lambda item: (
            sort_text(item[0][0]),
            sort_text(item[0][1]),
            sort_text(item[0][3]),
            item[0][2],
        ),
    )
    assignments: dict[str, dict[str, Any]] = {}
    aisle_summary: dict[int, dict[str, Any]] = {}
    aisle_number = 0
    current_group: tuple[str, str] | None = None
    used_in_aisle = 0

    for (department, sub_department, category_code, category_name), family_rows in (
        ordered_families
    ):
        group = (department, sub_department)
        if group != current_group:
            current_group = group
            aisle_number += 1
            used_in_aisle = start_aisle(
                department, sub_department, aisle_number, aisle_summary
            )

        family_rows.sort(
            key=lambda row: (
                sort_text(row.brand or ""),
                sort_text(row.name),
                row.product_id,
            )
        )
        # Keep a category block together where it fits. A family larger than one
        # aisle is split only at an aisle boundary.
        if (
            used_in_aisle
            and used_in_aisle + len(family_rows) > SLOTS_PER_AISLE
        ):
            aisle_number += 1
            used_in_aisle = start_aisle(
                department, sub_department, aisle_number, aisle_summary
            )

        for row in family_rows:
            if used_in_aisle == SLOTS_PER_AISLE:
                aisle_number += 1
                used_in_aisle = start_aisle(
                    department, sub_department, aisle_number, aisle_summary
                )

            bay_index, local_position = divmod(used_in_aisle, SLOTS_PER_BAY)
            horizontal_slot, vertical_index = divmod(local_position, SHELF_LEVELS)
            shelf_level = LEVEL_ORDER[vertical_index]
            slot = horizontal_slot + 1
            bay = bay_index + 1
            aisle_id = f"A{aisle_number:02d}"
            assigned = {
                "product_id": row.product_id,
                "name": row.name,
                "brand": row.brand,
                "department": department,
                "sub_department": sub_department,
                "category_code": category_code,
                "category_name": category_name,
                "aisle": aisle_id,
                "bay": f"B{bay:02d}",
                "shelf_level": f"L{shelf_level:02d}",
                "slot": f"P{slot:02d}",
                "location_code": location_code(
                    aisle_number, bay, shelf_level, slot
                ),
                "placement_status": "RECOMMENDED_SYNTHETIC_PLANOGRAM",
                "source_shelf_placeholder": row.shelf,
                "exclusion_reason": None,
            }
            assignments[row.product_id] = assigned
            summary = aisle_summary[aisle_number]
            summary["product_count"] += 1
            summary["category_codes"].add(category_code)
            summary["bays"].add(bay)
            used_in_aisle += 1

    return assignments, aisle_summary


def summarize_aisles(
    aisle_summary: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    for number, details in sorted(aisle_summary.items()):
        result.append(
            {
                "aisle": details["aisle"],
                "department": details["department"],
                "sub_department": details["sub_department"],
                "product_count": details["product_count"],
                "category_codes": sorted(details["category_codes"]),
                "bay_count": len(details["bays"]),
                "bay_numbers": sorted(details["bays"]),
                "capacity_slots": BAYS_PER_AISLE * SLOTS_PER_BAY,
                "utilization": round(
                    details["product_count"] / (BAYS_PER_AISLE * SLOTS_PER_BAY),
                    3,
                ),
            }
        )
    return result


def svg_overview(
    assignments: dict[str, dict[str, Any]],
    aisles: list[dict[str, Any]],
    output_path: Path,
    dataset_version: str | None = None,
) -> None:
    width = 1560
    columns = 4
    card_width = 370
    card_height = 194
    top = 164
    rows = max(1, (len(aisles) + columns - 1) // columns)
    height = top + rows * card_height + 75
    palette = (
        "#38bdf8",
        "#34d399",
        "#fbbf24",
        "#a78bfa",
        "#fb7185",
        "#22d3ee",
        "#f97316",
        "#84cc16",
        "#e879f9",
        "#60a5fa",
        "#2dd4bf",
        "#c084fc",
    )
    category_colors: dict[str, str] = {}
    for assignment in assignments.values():
        code = assignment["category_code"]
        if code not in category_colors:
            category_colors[code] = palette[len(category_colors) % len(palette)]

    elements = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">'
        ),
        (
            '<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
            '<stop stop-color="#101827"/><stop offset="1" '
            'stop-color="#17283b"/></linearGradient><filter id="shadow" '
            'x="-20%" y="-20%" width="150%" height="160%">'
            '<feDropShadow dx="0" dy="5" stdDeviation="5" '
            'flood-color="#020617" flood-opacity=".28"/>'
            '</filter></defs>'
        ),
        f'<rect width="{width}" height="{height}" fill="url(#bg)"/>',
        svg_text(
            40,
            54,
            "WINMART · SƠ ĐỒ KỆ ĐỀ XUẤT",
            "#f8fafc",
            30,
            weight="700",
        ),
        svg_text(
            40,
            88,
            f"{len(assignments):,} SKU · {len(aisles)} aisle · 5 tầng kệ · "
            "8 bay/aisle · nhóm theo ngành và category",
            "#b8c7d9",
            16,
        ),
        svg_text(
            40,
            116,
            "Màu thể hiện các khối category · sơ đồ minh hoạ theo vị trí đề "
            "xuất, chưa phải bản vẽ thi công",
            "#fbbf24",
            14,
        ),
        svg_text(
            40,
            142,
            "Đầu dãy có mã và tên nhóm hàng · Mã vị trí: Axx-Bxx-Lxx-Pxx · L1 dưới, L5 trên",
            "#d5e2f0",
            13,
        ),
        *(
            [
                svg_text(
                    40,
                    158,
                    f"Nguồn: catalog WinMart · dataset version {dataset_version}",
                    "#91a8c0",
                    9,
                )
            ]
            if dataset_version
            else []
        ),
    ]

    for index, aisle in enumerate(aisles):
        col = index % columns
        line = index // columns
        x = 28 + col * card_width
        y = top + line * card_height
        title = f'{aisle["aisle"]} · {aisle["sub_department"]}'
        elements.append(
            f'<rect x="{x}" y="{y}" width="350" height="174" '
            'rx="13" fill="#1e2b3c" stroke="#35465c" '
            'filter="url(#shadow)"/>'
        )
        elements.append(
            svg_text(x + 14, y + 23, title[:42], "#f1f5f9", 14, weight="650")
        )
        elements.append(
            svg_text(
                x + 14,
                y + 43,
                f'{aisle["product_count"]} SKU · {aisle["bay_count"]} bay · '
                f'{len(aisle["category_codes"])} category',
                "#a9bbd0",
                12,
            )
        )

        rack_x = x + 40
        rack_y = y + 61
        rack_width = 226
        rack_height = 83
        depth_x = 15
        depth_y = -10
        top_face = (
            (rack_x, rack_y),
            (rack_x + depth_x, rack_y + depth_y),
            (rack_x + rack_width + depth_x, rack_y + depth_y),
            (rack_x + rack_width, rack_y),
        )
        elements.append(
            f'<polygon points="{svg_points(top_face)}" '
            'fill="#45566d" stroke="#8799ae" stroke-width="1"/>'
        )
        right_face = (
            (rack_x + rack_width, rack_y),
            (rack_x + rack_width + depth_x, rack_y + depth_y),
            (rack_x + rack_width + depth_x, rack_y + rack_height + depth_y),
            (rack_x + rack_width, rack_y + rack_height),
        )
        elements.append(
            f'<polygon points="{svg_points(right_face)}" '
            'fill="#29394e" stroke="#71839a" stroke-width="1"/>'
        )
        bay_width = rack_width / BAYS_PER_AISLE
        cell_width = bay_width / SLOTS_PER_LEVEL
        level_height = rack_height / SHELF_LEVELS
        aisle_assignments = [
            value for value in assignments.values() if value["aisle"] == aisle["aisle"]
        ]
        slot_by_location = {
            (int(value["bay"][1:]), int(value["shelf_level"][1:]), int(value["slot"][1:])): value
            for value in aisle_assignments
        }
        for bay in range(1, BAYS_PER_AISLE + 1):
            bx = rack_x + (bay - 1) * bay_width
            elements.append(
                f'<rect x="{bx:.2f}" y="{rack_y}" '
                f'width="{bay_width:.2f}" height="{rack_height}" '
                'fill="#d5dde6" stroke="#78879a" stroke-width="1.2"/>'
            )
            for level in range(1, SHELF_LEVELS + 1):
                ly = rack_y + (SHELF_LEVELS - level) * level_height
                elements.append(
                    svg_text(
                        rack_x - 5,
                        ly + level_height * 0.68,
                        f"L{level}",
                        "#aebdd0",
                        8,
                        anchor="end",
                    )
                )
                elements.append(
                    f'<line x1="{bx:.2f}" y1="{ly:.2f}" '
                    f'x2="{bx + bay_width:.2f}" y2="{ly:.2f}" '
                    'stroke="#8191a5" stroke-width="1.1"/>'
                )
                for slot in range(1, SLOTS_PER_LEVEL + 1):
                    value = slot_by_location.get((bay, level, slot))
                    if value is None:
                        continue
                    sx = bx + (slot - 1) * cell_width + 0.8
                    sy = ly + 1.4
                    tooltip = safe_text(
                        f'{value["name"]} · {value["product_id"]} · '
                        f'{value["location_code"]}'
                    )
                    elements.append(
                        f'<rect x="{sx:.2f}" y="{sy:.2f}" '
                        f'width="{cell_width - 1.5:.2f}" '
                        f'height="{level_height - 2.5:.2f}" rx="1.2" '
                        f'fill="{category_colors[value["category_code"]]}" '
                        'stroke="#173044" stroke-width=".35">'
                        f"<title>{tooltip}</title></rect>"
                    )
            elements.append(
                svg_text(
                    bx + bay_width / 2,
                    rack_y + rack_height + 14,
                    f"B{bay:02d}",
                    "#aebdd0",
                    8,
                    anchor="middle",
                )
            )
        bottom_face = (
            (rack_x, rack_y + rack_height),
            (rack_x + rack_width, rack_y + rack_height),
            (rack_x + rack_width + depth_x, rack_y + rack_height + depth_y),
            (rack_x + depth_x, rack_y + rack_height + depth_y),
        )
        elements.append(
            f'<polygon points="{svg_points(bottom_face)}" '
            'fill="#35465c" stroke="#71839a" stroke-width="1"/>'
        )

        # Put a visible aisle code at the open end of every rack. The aisle
        # title above the rack carries the department name; this endcap sign
        # makes the locator code easy to spot in the 3D map.
        sign_x = rack_x + rack_width + 2
        sign_y = rack_y + 17
        sign_width = 80
        sign_height = 56
        sign_lines = aisle_sign_lines(aisle["sub_department"])
        elements.append(
            f'<rect x="{sign_x}" y="{sign_y}" width="{sign_width}" height="{sign_height}" '
            'rx="4" fill="#0f766e" stroke="#5eead4" stroke-width="1.2"/>'
        )
        elements.append(
            svg_text(sign_x + sign_width / 2, sign_y + 15, aisle["aisle"], "#ffffff", 11,
                     weight="700", anchor="middle")
        )
        first_label_y = sign_y + 42 - 8 * (len(sign_lines) - 1) / 2
        for line_number, label in enumerate(sign_lines):
            elements.append(
                svg_text(
                    sign_x + sign_width / 2,
                    first_label_y + line_number * 8,
                    label,
                    "#ccfbf1",
                    5.5,
                    anchor="middle",
                )
            )

    source_shelf_note = (
        "Vị trí shelf gốc trong catalog là placeholder mô phỏng; "
        "cột này được giữ riêng để đối chiếu."
    )
    elements.append(
        svg_text(
            40,
            height - 35,
            f"{source_shelf_note} · Tầng L1 ở dưới, L5 ở trên.",
            "#94a9c0",
            13,
        )
    )
    elements.append("</svg>")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(elements), encoding="utf-8")


def validate_assignments(
    accepted_rows: list[WinMartRow],
    assignments: dict[str, dict[str, Any]],
    aisles: list[dict[str, Any]],
) -> None:
    expected_ids = {row.product_id for row in accepted_rows}
    assigned_ids = set(assignments)
    if expected_ids != assigned_ids:
        raise ValueError(
            f"Assignment mismatch: {len(expected_ids - assigned_ids)} missing, "
            f"{len(assigned_ids - expected_ids)} unexpected"
        )
    locations = [item["location_code"] for item in assignments.values()]
    if len(locations) != len(set(locations)):
        raise ValueError("Two products were assigned to the same location")
    for aisle in aisles:
        if aisle["product_count"] > SLOTS_PER_AISLE:
            raise ValueError(f'{aisle["aisle"]} exceeds its defined capacity')
        if aisle["bay_count"] > BAYS_PER_AISLE:
            raise ValueError(f'{aisle["aisle"]} exceeds its defined bay count')


def generate(
    input_path: Path,
    json_path: Path,
    csv_path: Path,
    svg_path: Path,
) -> dict[str, Any]:
    schema_rows, quarantined, report = analyze_dataset(input_path)
    if not report["valid"]:
        raise ValueError(f"Catalog validation failed: {report['fatal_errors'][:3]}")

    quarantine_by_id = {
        item["product_id"]: item["reason"] for item in quarantined
    }
    accepted_rows = [
        row for row in schema_rows if row.product_id not in quarantine_by_id
    ]
    assigned, aisle_summary = assign_products(accepted_rows)
    aisles = summarize_aisles(aisle_summary)
    validate_assignments(accepted_rows, assigned, aisles)

    assignments: list[dict[str, Any]] = []
    all_rows = json.loads(input_path.read_text(encoding="utf-8"))
    for source_row in all_rows:
        product_id = source_row["product_id"]
        if product_id in assigned:
            assignments.append(assigned[product_id])
        else:
            assignments.append(
                {
                    "product_id": product_id,
                    "name": source_row["name"],
                    "brand": source_row.get("brand"),
                    "department": source_row.get("department"),
                    "sub_department": source_row.get("sub_department"),
                    "category_code": source_row.get("category_code"),
                    "category_name": source_row.get("category_name"),
                    "aisle": None,
                    "bay": None,
                    "shelf_level": None,
                    "slot": None,
                    "location_code": None,
                    "placement_status": "EXCLUDED_FROM_PLANOGRAM",
                    "source_shelf_placeholder": source_row.get("shelf"),
                    "exclusion_reason": quarantine_by_id.get(
                        product_id, "NOT_ACCEPTED_BY_CATALOG_IMPORTER"
                    ),
                }
            )

    root = {
        "schema_version": 1,
        "planogram_version": PLANOGRAM_VERSION,
        "source_dataset": input_path.name,
        "source_dataset_sha256": report["dataset_version"],
        "source_shelf_note": (
            "The catalog's existing shelf values are crawler-generated placeholders "
            "(shelf_source=SIMULATED), not verified store locations."
        ),
        "strategy": {
            "ordering": [
                "department",
                "sub_department",
                "category family",
                "brand",
                "product name",
            ],
            "aisle_capacity_bays": BAYS_PER_AISLE,
            "bay_capacity_levels": SHELF_LEVELS,
            "bay_capacity_slots_per_level": SLOTS_PER_LEVEL,
            "slot_policy": "one unique locator per accepted SKU; not a facing or unit count",
            "level_fill_order": [f"L{level:02d}" for level in LEVEL_ORDER],
            "physical_dimensions": "illustrative only; not present in the source data",
        },
        "limitations": [
            (
                "No POS sales velocity, margin, transaction baskets, package "
                "dimensions, weight, cold-chain, or fixture dimensions were used."
            ),
            (
                "The output is a taxonomy-based draft locator, not a "
                "profit-optimal or installation-ready planogram."
            ),
            "Products rejected by the existing catalog importer receive no recommended location.",
        ],
        "counts": {
            "catalog_rows": report["rows_total"],
            "assigned_products": len(assigned),
            "excluded_quarantine_products": len(quarantined),
            "aisles": len(aisles),
            "categories": report["category_count"],
        },
        "aisles": aisles,
        "assignments": assignments,
    }

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(assignments)
    svg_overview(assigned, aisles, svg_path, report["dataset_version"])
    return root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate taxonomy-based, explicitly provisional shelf locators."
    )
    parser.add_argument("--input", type=Path, default=ROOT / "data/winmart_products.json")
    parser.add_argument(
        "--json-output",
        type=Path,
        default=ROOT / "data/winmart_planogram_assignments.json",
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        default=ROOT / "data/winmart_planogram_assignments.csv",
    )
    parser.add_argument(
        "--svg-output",
        type=Path,
        default=ROOT / "data/winmart_planogram_3d.svg",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = generate(
        args.input.resolve(),
        args.json_output.resolve(),
        args.csv_output.resolve(),
        args.svg_output.resolve(),
    )
    print(
        json.dumps(
            {
                "counts": result["counts"],
                "outputs": [
                    str(args.json_output),
                    str(args.csv_output),
                    str(args.svg_output),
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
