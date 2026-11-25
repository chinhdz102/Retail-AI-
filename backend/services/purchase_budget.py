"""Arithmetic observations for a selector; never choose products or infer portions."""

from __future__ import annotations


def purchase_budget_context(
    budget: int | None, rows: list[dict], required_roles: list[int],
    picks: list[dict] | None = None,
) -> dict:
    if budget is None:
        return {}
    minima = {
        role: min((row["price_vnd"] for row in rows
                   if role in row.get("search_indexes", [])), default=None)
        for role in required_roles
    }
    complete = all(price is not None for price in minima.values())
    lower_bound = sum(price for price in minima.values() if price is not None)
    result = {
        "limit_vnd": budget,
        "one_unit_per_required_role_lower_bound_vnd": lower_bound if complete else None,
        "required_roles_without_candidates": [role for role, price in minima.items()
                                               if price is None],
        "scope": "Arithmetic only, not proof of function, compatibility, portions or duration. "
                 "Quantities must still follow the customer; do not fill the budget. "
                 "Change picks/quantities if over budget, then rewrite their explanation.",
        "candidate_quantity_bounds": [],
    }
    for row in rows:
        bounds = []
        for role in row.get("search_indexes", []):
            other_minima = [price for index, price in minima.items() if index != role]
            if any(price is None for price in other_minima):
                continue
            remaining = budget - sum(other_minima)
            price = row["price_vnd"]
            maximum = 0 if remaining < 0 else min(20, remaining // price) if price else 20
            bounds.append({"search_index": role, "max_quantity": maximum})
        result["candidate_quantity_bounds"].append({
            "candidate": row["candidate"], "roles": bounds,
        })
    if picks is not None:
        products = {row["candidate"]: row for row in rows}
        costs = [{"candidate": pick["candidate"], "quantity": pick["quantity"],
                  "unit_price_vnd": products[pick["candidate"]]["price_vnd"],
                  "line_total_vnd": products[pick["candidate"]]["price_vnd"] * pick["quantity"]}
                 for pick in picks if pick["candidate"] in products]
        result["previous_pick_costs"] = costs
        if len(costs) == len(picks):
            total = sum(line["line_total_vnd"] for line in costs)
            result.update(previous_total_vnd=total, exceeds_by_vnd=max(0, total - budget))
    return result
