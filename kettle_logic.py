from datetime import date, timedelta

CATEGORIES = ["rent", "utilities", "groceries"]


def parse_date(s):
    return date.fromisoformat(s)


def get_members(conn, kettle_id, include_left=False):
    rows = conn.execute(
        """SELECT km.*, u.name, u.email FROM kettle_members km
           JOIN users u ON u.id = km.user_id
           WHERE km.kettle_id = ?""",
        (kettle_id,),
    ).fetchall()
    if include_left:
        return rows
    return [r for r in rows if r["status"] != "left"]


def get_kettle(conn, kettle_id):
    return conn.execute("SELECT * FROM kettles WHERE id = ?", (kettle_id,)).fetchone()


def get_charges(conn, kettle_id):
    rows = conn.execute("SELECT * FROM charges WHERE kettle_id = ?", (kettle_id,)).fetchall()
    out = {c: 0.0 for c in CATEGORIES}
    for r in rows:
        out[r["category"]] = r["amount"]
    return out


def household_shares(conn, kettle_id, hypothetical_extra_member=False):
    """Return {user_id: {category: amount, total: amount}} evenly split among
    active (non-left) members. If hypothetical_extra_member is True, divides
    among current members + 1 phantom member, for preview purposes."""
    members = get_members(conn, kettle_id)
    charges = get_charges(conn, kettle_id)
    n = len(members) + (1 if hypothetical_extra_member else 0)
    n = max(n, 1)
    shares = {}
    for m in members:
        cat_shares = {c: round(charges[c] / n, 2) for c in CATEGORIES}
        shares[m["user_id"]] = {"categories": cat_shares, "total": round(sum(cat_shares.values()), 2)}
    return shares, charges


def trip_day_range(kettle):
    start = parse_date(kettle["start_date"])
    end = parse_date(kettle["end_date"])
    days = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    return days


def trip_shares(conn, kettle_id, extra_member=None):
    """Per-day proration. extra_member: optional dict {join_date: date, user_id: 'preview'}
    to compute a hypothetical preview without persisting anything."""
    kettle = get_kettle(conn, kettle_id)
    members = list(get_members(conn, kettle_id))
    if extra_member is not None:
        members = members + [extra_member]

    days = trip_day_range(kettle)
    total = kettle["total_amount"] or 0.0
    daily_rate = total / len(days) if days else 0.0

    totals = {}
    for m in members:
        key = m["user_id"] if not isinstance(m, dict) else m["user_id"]
        totals[key] = 0.0

    for d in days:
        present = []
        for m in members:
            join_d = parse_date(m["join_date"]) if not isinstance(m["join_date"], date) else m["join_date"]
            left_d = None
            if m["left_date"]:
                left_d = parse_date(m["left_date"]) if not isinstance(m["left_date"], date) else m["left_date"]
            status = m["status"] if "status" in (m.keys() if hasattr(m, "keys") else m) else m.get("status")
            if status == "left":
                continue
            if join_d <= d and (left_d is None or d <= left_d):
                present.append(m)
        if not present:
            continue
        per_person = daily_rate / len(present)
        for m in present:
            key = m["user_id"]
            totals[key] = totals.get(key, 0.0) + per_person

    return {k: round(v, 2) for k, v in totals.items()}


def payments_by_member(conn, kettle_id):
    rows = conn.execute(
        "SELECT user_id, SUM(amount) as total FROM payments WHERE kettle_id = ? GROUP BY user_id",
        (kettle_id,),
    ).fetchall()
    return {r["user_id"]: r["total"] for r in rows}


def covers_owed_by_member(conn, kettle_id):
    """Amount each debtor has had covered for them (unsettled), reduces their kettle balance."""
    rows = conn.execute(
        "SELECT debtor_id, SUM(amount) as total FROM covers WHERE kettle_id = ? AND settled = 0 GROUP BY debtor_id",
        (kettle_id,),
    ).fetchall()
    return {r["debtor_id"]: r["total"] for r in rows}


def kettle_balances(conn, kettle_id):
    """Returns {user_id: {'share': x, 'paid': x, 'covered': x, 'owed': x}}"""
    kettle = get_kettle(conn, kettle_id)
    if kettle["type"] == "household":
        shares, _ = household_shares(conn, kettle_id)
        share_totals = {uid: v["total"] for uid, v in shares.items()}
    else:
        share_totals = trip_shares(conn, kettle_id)

    paid = payments_by_member(conn, kettle_id)
    covered = covers_owed_by_member(conn, kettle_id)

    result = {}
    for uid, share in share_totals.items():
        p = paid.get(uid, 0.0)
        c = covered.get(uid, 0.0)
        owed = round(share - p - c, 2)
        result[uid] = {"share": round(share, 2), "paid": round(p, 2), "covered": round(c, 2), "owed": owed}
    return result


def personal_debts_owed_by(conn, user_id):
    """Debts this user owes to others via 'cover' (unsettled)."""
    rows = conn.execute(
        """SELECT c.*, u.name as payer_name, k.name as kettle_name FROM covers c
           JOIN users u ON u.id = c.payer_id
           JOIN kettles k ON k.id = c.kettle_id
           WHERE c.debtor_id = ? AND c.settled = 0""",
        (user_id,),
    ).fetchall()
    return rows


def personal_debts_owed_to(conn, user_id):
    """Debts others owe this user via 'cover' (unsettled)."""
    rows = conn.execute(
        """SELECT c.*, u.name as debtor_name, k.name as kettle_name FROM covers c
           JOIN users u ON u.id = c.debtor_id
           JOIN kettles k ON k.id = c.kettle_id
           WHERE c.payer_id = ? AND c.settled = 0""",
        (user_id,),
    ).fetchall()
    return rows


def dashboard_totals(conn, user_id):
    kettles = conn.execute(
        """SELECT DISTINCT k.* FROM kettles k
           JOIN kettle_members km ON km.kettle_id = k.id
           WHERE km.user_id = ? AND km.status != 'left'""",
        (user_id,),
    ).fetchall()

    total_owed = 0.0
    total_spent = 0.0
    per_kettle = []
    for k in kettles:
        bal = kettle_balances(conn, k["id"])
        mine = bal.get(user_id, {"owed": 0, "paid": 0})
        total_owed += max(mine["owed"], 0)
        total_spent += mine["paid"]
        per_kettle.append({"kettle": k, "owed": mine["owed"], "share": mine.get("share", 0)})

    debts_owed = personal_debts_owed_by(conn, user_id)
    for d in debts_owed:
        total_owed += d["amount"]

    covers_made = conn.execute(
        "SELECT SUM(amount) as t FROM covers WHERE payer_id = ?", (user_id,)
    ).fetchone()
    if covers_made["t"]:
        total_spent += covers_made["t"]

    return {
        "total_owed": round(total_owed, 2),
        "total_spent": round(total_spent, 2),
        "kettles": per_kettle,
        "debts_owed": debts_owed,
        "debts_to_me": personal_debts_owed_to(conn, user_id),
    }


def landlord_kettles(conn, user_id):
    return conn.execute("SELECT * FROM kettles WHERE landlord_id = ?", (user_id,)).fetchall()


def tenant_payment_status(conn, kettle_id):
    """Payment status only — no charge categories, no purchase history. This is
    the entire surface a landlord is allowed to see. Also flags anyone waiting
    on the landlord to approve their exit, since that decision is theirs alone."""
    balances = kettle_balances(conn, kettle_id)
    members = get_members(conn, kettle_id)
    out = []
    for m in members:
        owed = round(max(balances.get(m["user_id"], {}).get("owed", 0), 0), 2)
        out.append({
            "user_id": m["user_id"],
            "name": m["name"],
            "leaving": m["status"] == "leaving",
            "leave_pending": m["status"] == "leave_pending",
            "paid_up": owed <= 0.01,
            "owed": owed,
        })
    out.sort(key=lambda t: (t["leave_pending"], t["paid_up"], -t["owed"]), reverse=False)
    return out


def maybe_finalize_leaving_members(conn, kettle_id):
    """Auto-transition 'leaving' members once their balance clears. In a
    landlorded household kettle, clearing your balance doesn't free you —
    it moves you to 'leave_pending' until the landlord approves the exit.
    Everywhere else (no landlord, or a trip), a cleared balance means you're
    just gone."""
    kettle = get_kettle(conn, kettle_id)
    gated = kettle["type"] == "household" and kettle["landlord_id"]
    bal = kettle_balances(conn, kettle_id)
    members = get_members(conn, kettle_id)
    for m in members:
        if m["status"] == "leaving":
            owed = bal.get(m["user_id"], {}).get("owed", 0)
            if owed <= 0.01:
                new_status = "leave_pending" if gated else "left"
                conn.execute("UPDATE kettle_members SET status=? WHERE id=?", (new_status, m["id"]))
    conn.commit()


def current_month_key():
    return date.today().strftime("%Y-%m")


ESCALATION_TIERS = [
    (0, "\U0001F44B"),   # gentle
    (2, "⏰"),        # firmer
    (4, "\U0001F6A8"),    # losing patience
]


def nudge_escalation(conn, kettle_id, target_id):
    """How insistent this nudge should read, based on how many times this
    person has already been nudged in this kettle this month."""
    month = current_month_key()
    row = conn.execute(
        "SELECT COUNT(*) as c FROM nudges WHERE kettle_id=? AND to_user_id=? AND created_at LIKE ?",
        (kettle_id, target_id, month + "%"),
    ).fetchone()
    count = row["c"]
    if count >= 4:
        return count, ESCALATION_TIERS[2][1], "the kettle is losing patience — this is nudge #%d this month" % (count + 1)
    if count >= 2:
        return count, ESCALATION_TIERS[1][1], "this is nudge #%d this month" % (count + 1)
    return count, ESCALATION_TIERS[0][1], None
