from datetime import date, timedelta

TRIAL_DAYS = 14
SUB_PRICE_QR = 15.0
BILLING_CYCLE_DAYS = 30

PERKS = [
    "Double loyalty points on every kettle payment",
    "Extra discounts on groceries and trip pools",
    "Member pricing on outings booked through The Kettle",
    "Discounted table-mode ordering at partner restaurants",
]


def start_trial(conn, user_id):
    today = date.today()
    trial_end = today + timedelta(days=TRIAL_DAYS)
    conn.execute(
        "UPDATE users SET subscription_status='trial', trial_ends_at=?, next_billing_date=?, subscribed=1 WHERE id=?",
        (trial_end.isoformat(), trial_end.isoformat(), user_id),
    )
    conn.commit()
    return trial_end


def cancel(conn, user_id):
    conn.execute(
        "UPDATE users SET subscription_status='canceled', subscribed=0 WHERE id=?", (user_id,)
    )
    conn.commit()


def process_billing(conn, user):
    """Lazily 'runs' billing on read, since this demo has no background scheduler.
    Converts a finished trial into a paid charge, and rolls over paid cycles."""
    status = user["subscription_status"]
    if status not in ("trial", "active"):
        return user, None

    today = date.today()
    due_date_str = user["trial_ends_at"] if status == "trial" else user["next_billing_date"]
    if not due_date_str or today < date.fromisoformat(due_date_str):
        return user, None

    message = None
    if user["card_balance"] >= SUB_PRICE_QR:
        next_billing = today + timedelta(days=BILLING_CYCLE_DAYS)
        conn.execute(
            "UPDATE users SET card_balance = card_balance - ?, subscription_status='active', "
            "next_billing_date=? WHERE id=?",
            (SUB_PRICE_QR, next_billing.isoformat(), user["id"]),
        )
        if status == "trial":
            message = "Your free trial ended — QR %.2f charged, Kettle AI is now active." % SUB_PRICE_QR
        else:
            message = "QR %.2f charged for this month's Kettle AI subscription." % SUB_PRICE_QR
    else:
        conn.execute(
            "UPDATE users SET subscription_status='past_due', subscribed=0 WHERE id=?", (user["id"],)
        )
        message = "Your card balance is too low for the QR %.2f Kettle AI charge — subscription paused." % SUB_PRICE_QR

    conn.commit()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    return user, message


def generate_insights(user, totals):
    insights = []
    owed = totals["total_owed"]
    spent = totals["total_spent"]

    if owed > 0 and totals["kettles"]:
        biggest = max(totals["kettles"], key=lambda k: k["owed"])
        if biggest["owed"] > 0:
            insights.append(
                "Your biggest open balance is QR %.2f in %s — clear that first and you'll stop the most nudges."
                % (biggest["owed"], biggest["kettle"]["name"])
            )
        insights.append("You're carrying QR %.2f in open balances across all your kettles right now." % owed)
    else:
        insights.append("You're settled up everywhere right now — nothing pending across your kettles.")

    if spent > 0:
        insights.append(
            "You've put QR %.2f into kettles and covers so far — keeping payments small and frequent tends to avoid big end-of-month hits."
            % spent
        )

    if user["card_balance"] < 100:
        insights.append(
            "Your card balance is running low (QR %.2f) — worth topping up before your next kettle payment is due."
            % user["card_balance"]
        )

    trip_kettles = [k for k in totals["kettles"] if k["kettle"]["type"] == "trip"]
    if trip_kettles:
        insights.append(
            "You're in %d active trip kettle(s) — remember shares rebalance automatically as people join, so check back before the trip wraps."
            % len(trip_kettles)
        )

    insights.append(
        "Kettle AI is simulated in this demo — a real version would pull spending patterns to flag overspend before it happens."
    )
    return insights
