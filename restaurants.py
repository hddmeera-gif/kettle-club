DISCOUNT_PARTNERS = [
    {
        "id": "msheireb-roastery",
        "name": "Msheireb Roastery",
        "kind": "restaurant",
        "cuisine": "Specialty Coffee",
        "area": "Msheireb Downtown",
        "discount": 20,
        "blurb": "Small-batch roasts and pastries a five-minute walk from the metro.",
        "emoji": "☕",
        "gradient": "linear-gradient(135deg, #6b4226, #b98b5e)",
        "lat": 25.2867, "lng": 51.5310,
        "rating": 4.6, "avg_price_qr": 35,
    },
    {
        "id": "souq-grill-house",
        "name": "Souq Grill House",
        "kind": "restaurant",
        "cuisine": "Qatari & Grill",
        "area": "Souq Waqif",
        "discount": 15,
        "blurb": "Charcoal grills and slow-cooked machboos in the heart of the souq.",
        "emoji": "\U0001F357",
        "gradient": "linear-gradient(135deg, #7a2e2e, #c96f0f)",
        "lat": 25.2881, "lng": 51.5325,
        "rating": 4.4, "avg_price_qr": 60,
    },
    {
        "id": "pearl-bay-bistro",
        "name": "Pearl Bay Bistro",
        "kind": "restaurant",
        "cuisine": "Mediterranean",
        "area": "The Pearl-Qatar",
        "discount": 25,
        "blurb": "Waterfront tables, fresh mezze, and a rotating catch of the day.",
        "emoji": "\U0001F957",
        "gradient": "linear-gradient(135deg, #12494a, #2f7d5b)",
        "lat": 25.3700, "lng": 51.5500,
        "rating": 4.7, "avg_price_qr": 90,
    },
    {
        "id": "katara-kabab-corner",
        "name": "Katara Kabab Corner",
        "kind": "restaurant",
        "cuisine": "Middle Eastern",
        "area": "Katara Cultural Village",
        "discount": 10,
        "blurb": "Family-run kabab spot tucked behind the amphitheatre.",
        "emoji": "\U0001F357",
        "gradient": "linear-gradient(135deg, #8a4b12, #e8871e)",
        "lat": 25.3600, "lng": 51.5270,
        "rating": 4.2, "avg_price_qr": 45,
    },
    {
        "id": "corniche-cafe-lounge",
        "name": "Corniche Café Lounge",
        "kind": "restaurant",
        "cuisine": "Café & Desserts",
        "area": "West Bay Corniche",
        "discount": 20,
        "blurb": "Skyline views and a dessert case that empties out by sunset.",
        "emoji": "\U0001F370",
        "gradient": "linear-gradient(135deg, #4a1f4d, #a24ba6)",
        "lat": 25.3220, "lng": 51.5290,
        "rating": 4.5, "avg_price_qr": 55,
    },
    {
        "id": "al-wakra-seafood-table",
        "name": "Al Wakra Seafood Table",
        "kind": "restaurant",
        "cuisine": "Seafood",
        "area": "Al Wakra",
        "discount": 15,
        "blurb": "Daily-caught hammour and shrimp, five minutes from the old souq.",
        "emoji": "\U0001F990",
        "gradient": "linear-gradient(135deg, #0a3d4d, #1c8fa6)",
        "lat": 25.1659, "lng": 51.6038,
        "rating": 4.3, "avg_price_qr": 70,
    },
]

GROCERY_PARTNERS = [
    {
        "id": "pearl-fresh-grocers",
        "name": "Pearl Fresh Grocers",
        "kind": "grocery",
        "cuisine": "Supermarket",
        "area": "The Pearl-Qatar",
        "discount": 15,
        "blurb": "Everyday basket essentials with a produce aisle restocked daily.",
        "emoji": "\U0001F6D2",
        "gradient": "linear-gradient(135deg, #1f5c3f, #57a06a)",
        "lat": 25.3715, "lng": 51.5480,
        "rating": 4.4, "avg_price_qr": 120,
    },
    {
        "id": "msheireb-market-basket",
        "name": "Msheireb Market Basket",
        "kind": "grocery",
        "cuisine": "Supermarket",
        "area": "Msheireb Downtown",
        "discount": 10,
        "blurb": "Compact downtown grocer, good for a quick weekly top-up.",
        "emoji": "\U0001F6D2",
        "gradient": "linear-gradient(135deg, #35502e, #7ea55a)",
        "lat": 25.2850, "lng": 51.5295,
        "rating": 4.1, "avg_price_qr": 95,
    },
    {
        "id": "corniche-corner-grocery",
        "name": "Corniche Corner Grocery",
        "kind": "grocery",
        "cuisine": "Supermarket",
        "area": "West Bay Corniche",
        "discount": 20,
        "blurb": "Bulk-buy friendly, with a bakery counter that sells out by evening.",
        "emoji": "\U0001F6D2",
        "gradient": "linear-gradient(135deg, #21403f, #4f8f7c)",
        "lat": 25.3230, "lng": 51.5315,
        "rating": 4.5, "avg_price_qr": 140,
    },
    {
        "id": "al-wakra-family-mart",
        "name": "Al Wakra Family Mart",
        "kind": "grocery",
        "cuisine": "Supermarket",
        "area": "Al Wakra",
        "discount": 12,
        "blurb": "Family-sized packs and the cheapest produce prices on this list.",
        "emoji": "\U0001F6D2",
        "gradient": "linear-gradient(135deg, #2c4a1f, #6d9a3f)",
        "lat": 25.1680, "lng": 51.6010,
        "rating": 4.0, "avg_price_qr": 85,
    },
]

ALL_PLACES = DISCOUNT_PARTNERS + GROCERY_PARTNERS


def redeem_code(user_id, place_id):
    slug = place_id.upper().replace("-", "")[:6]
    return "KETTLE-%s-%04d" % (slug, user_id)


BASELINE_WEEKLY_BASKET_QR = 300.0


def grocery_comparison():
    """This week's grocery run: named partner stores vs. a flat baseline
    basket, sorted best savings first."""
    rows = []
    for g in GROCERY_PARTNERS:
        save = round(BASELINE_WEEKLY_BASKET_QR * g["discount"] / 100.0, 2)
        rows.append({"name": g["name"], "area": g["area"], "save_qr": save})
    rows.sort(key=lambda r: -r["save_qr"])
    projected_monthly = round(sum(r["save_qr"] for r in rows[:1]) * 4.33, 2) if rows else 0
    return rows, projected_monthly


def trip_cheap_eats(limit=3):
    """Cheapest-first restaurant partners, for a trip-mode suggestion panel."""
    return sorted(DISCOUNT_PARTNERS, key=lambda r: r["avg_price_qr"])[:limit]
