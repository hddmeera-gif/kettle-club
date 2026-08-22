MENU_ITEMS = [
    {"name": "Grilled Chicken Plate", "price_qr": 45, "category": "food"},
    {"name": "Hummus & Bread", "price_qr": 18, "category": "food"},
    {"name": "Grilled Seafood Platter", "price_qr": 65, "category": "food"},
    {"name": "Fresh Juice", "price_qr": 12, "category": "drink"},
    {"name": "Dessert Plate", "price_qr": 22, "category": "food"},
    {"name": "Soft Drink", "price_qr": 8, "category": "drink"},
    {"name": "Service Charge", "price_qr": 15, "category": "service"},
]

STATUS_LABELS = {
    "dining": "Dining",
    "late_arrival": "Late arrival",
    "left_early": "Left early",
}


def menu_with_discount(discount_percent):
    out = []
    for item in MENU_ITEMS:
        full = item["price_qr"]
        discounted = round(full * (1 - discount_percent / 100.0), 2) if discount_percent else full
        out.append({"name": item["name"], "category": item["category"], "full_price": full, "price_qr": discounted})
    return out


def _present_at(member, when):
    """Was this table member actually there at the time an order went in?"""
    if member["joined_at"] > when:
        return False
    if member["left_at"] and member["left_at"] < when:
        return False
    return True


def eligible_sharers(order_created_at, category, requested_ids, orderer_id, members_by_id):
    """Filter a requested share list down to who was actually present, and who
    hasn't opted out of drinks/service — the orderer is always kept regardless."""
    out = []
    for uid in requested_ids:
        if uid == orderer_id:
            out.append(uid)
            continue
        m = members_by_id.get(uid)
        if not m:
            continue
        if not _present_at(m, order_created_at):
            continue
        if category in ("drink", "service") and m["exclude_drinks"]:
            continue
        out.append(uid)
    return out or [orderer_id]


def totals_from_orders(orders):
    """Sum of what each member has already been charged, from settled order rows."""
    totals = {}
    for o in orders:
        sharers = [int(x) for x in (o["shared_with"] or str(o["user_id"])).split(",") if x]
        split = o["price_qr"] / len(sharers)
        for uid in sharers:
            totals[uid] = totals.get(uid, 0.0) + split
    return {uid: round(amt, 2) for uid, amt in totals.items()}


def split_with_caps(price, category, requested_ids, orderer_id, members_by_id, existing_orders, order_created_at):
    """Figure out who actually pays for this order, honoring presence, drink
    opt-outs, and per-person spending caps — capped members drop out and the
    rest of the table absorbs their share, same as the real thing."""
    sharers = eligible_sharers(order_created_at, category, requested_ids, orderer_id, members_by_id)
    existing_totals = totals_from_orders(existing_orders)

    while True:
        split = price / len(sharers)
        over_cap = None
        for uid in sharers:
            if uid == orderer_id:
                continue
            cap = members_by_id[uid]["spending_cap"]
            if cap is not None and existing_totals.get(uid, 0.0) + split > cap + 0.01:
                over_cap = uid
                break
        if over_cap is None or len(sharers) <= 1:
            break
        sharers = [u for u in sharers if u != over_cap]

    per_person = round(price / len(sharers), 2)
    return {uid: per_person for uid in sharers}
