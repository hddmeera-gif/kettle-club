import os
import secrets
from datetime import date, datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

import db
import kettle_logic as kl
import subscription as sub
import restaurants as rest
import table_mode as tm

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

db.init_db()


def get_conn():
    if "conn" not in g:
        g.conn = db.get_db()
    return g.conn


@app.teardown_appcontext
def close_conn(exc):
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    if "user" in g:
        return g.user
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if user:
        user, message = sub.process_billing(conn, user)
        if message:
            flash(message)
    g.user = user
    return user


@app.context_processor
def inject_user():
    user = current_user()
    unread = 0
    is_landlord = False
    my_kettles = []
    if user:
        conn = get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as c FROM notifications WHERE user_id=? AND read=0", (user["id"],)
        ).fetchone()
        unread = row["c"]
        landlord_row = conn.execute(
            "SELECT COUNT(*) as c FROM kettles WHERE landlord_id=?", (user["id"],)
        ).fetchone()
        is_landlord = landlord_row["c"] > 0
        my_kettles = conn.execute(
            """SELECT DISTINCT k.* FROM kettles k
               JOIN kettle_members km ON km.kettle_id = k.id
               WHERE km.user_id = ? AND km.status != 'left'
               ORDER BY k.name""",
            (user["id"],),
        ).fetchall()
    return {
        "current_user": user, "unread_count": unread, "is_landlord": is_landlord, "my_kettles": my_kettles,
    }


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please sign in first.")
            return redirect(url_for("auth"))
        return f(*args, **kwargs)

    return wrapper


def member_or_404(conn, kettle_id, user_id):
    return conn.execute(
        "SELECT * FROM kettle_members WHERE kettle_id=? AND user_id=?", (kettle_id, user_id)
    ).fetchone()


# ---------------------------------------------------------------- landing --

@app.route("/")
def landing():
    if current_user():
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


# ------------------------------------------------------------------- auth --

@app.route("/auth", methods=["GET", "POST"])
def auth():
    if current_user():
        return redirect(url_for("dashboard"))

    mode = request.args.get("mode", "signin")

    if request.method == "POST":
        mode = request.form.get("mode", "signin")
        conn = get_conn()

        if mode == "signup":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            card_number = request.form.get("card_number", "").strip()

            if not name or not email or not password:
                flash("Please fill in your name, email, and password.")
                return render_template("auth.html", mode="signup")

            existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                flash("An account with that email already exists.")
                return render_template("auth.html", mode="signin")

            last4 = card_number[-4:] if len(card_number) >= 4 else "0000"
            conn.execute(
                "INSERT INTO users (name, email, password_hash, card_last4, card_balance, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, email, generate_password_hash(password, method="pbkdf2:sha256"), last4, 4000.0, date.today().isoformat()),
            )
            conn.commit()
            user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            session["user_id"] = user["id"]
            flash("Welcome to The Kettle, %s." % name.split(" ")[0])
            return redirect(url_for("dashboard"))

        elif mode == "google":
            # Mocked Google sign-in: create/find a demo Google user.
            email = "you@gmail.com"
            user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if not user:
                conn.execute(
                    "INSERT INTO users (name, email, password_hash, card_last4, card_balance, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("You (Google)", email, generate_password_hash("google-oauth", method="pbkdf2:sha256"), "0000", 4000.0, date.today().isoformat()),
                )
                conn.commit()
                user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            session["user_id"] = user["id"]
            flash("Signed in with Google (demo).")
            return redirect(url_for("dashboard"))

        else:  # signin
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if not user or not check_password_hash(user["password_hash"], password):
                flash("Incorrect email or password.")
                return render_template("auth.html", mode="signin")
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))

    return render_template("auth.html", mode=mode)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


# -------------------------------------------------------------- dashboard --

@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_conn()
    user = current_user()
    totals = kl.dashboard_totals(conn, user["id"])
    notifications = conn.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 5", (user["id"],)
    ).fetchall()
    return render_template("dashboard.html", totals=totals, notifications=notifications)


@app.route("/notifications")
@login_required
def notifications():
    conn = get_conn()
    user = current_user()
    conn.execute("UPDATE notifications SET read=1 WHERE user_id=?", (user["id"],))
    conn.commit()
    rows = conn.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC", (user["id"],)
    ).fetchall()
    audio_by_nudge = {}
    for r in rows:
        if r["custom_nudge_id"]:
            cn = conn.execute("SELECT audio_data FROM custom_nudges WHERE id=?", (r["custom_nudge_id"],)).fetchone()
            if cn and cn["audio_data"]:
                audio_by_nudge[r["custom_nudge_id"]] = cn["audio_data"]
    return render_template("notifications.html", notifications=rows, audio_by_nudge=audio_by_nudge)


# ------------------------------------------------------------- ai helper --

@app.route("/ai-helper")
@login_required
def ai_helper():
    conn = get_conn()
    user = current_user()
    totals = kl.dashboard_totals(conn, user["id"])
    insights = None
    grocery_rows, grocery_monthly, cheap_eats = None, None, None
    has_trip = any(k["kettle"]["type"] == "trip" for k in totals["kettles"])
    if user["subscription_status"] in ("trial", "active"):
        insights = sub.generate_insights(user, totals)
        grocery_rows, grocery_monthly = rest.grocery_comparison()
        if has_trip:
            cheap_eats = rest.trip_cheap_eats()
    return render_template(
        "ai_helper.html", totals=totals, insights=insights,
        perks=sub.PERKS, trial_days=sub.TRIAL_DAYS, price=sub.SUB_PRICE_QR,
        grocery_rows=grocery_rows, grocery_monthly=grocery_monthly, cheap_eats=cheap_eats, has_trip=has_trip,
    )


@app.route("/ai-helper/start-trial", methods=["POST"])
@login_required
def start_trial():
    conn = get_conn()
    user = current_user()
    if user["subscription_status"] in ("trial", "active"):
        flash("You're already on Kettle AI.")
        return redirect(url_for("ai_helper"))
    trial_end = sub.start_trial(conn, user["id"])
    flash(
        "Your %d-day free trial of Kettle AI has started. QR %.2f/month after — cancel anytime."
        % (sub.TRIAL_DAYS, sub.SUB_PRICE_QR)
    )
    return redirect(url_for("ai_helper"))


@app.route("/ai-helper/cancel", methods=["POST"])
@login_required
def cancel_trial():
    conn = get_conn()
    user = current_user()
    sub.cancel(conn, user["id"])
    flash("Kettle AI subscription canceled.")
    return redirect(url_for("ai_helper"))


# ------------------------------------------------------- restaurant perks --

@app.route("/restaurants")
@login_required
def restaurant_discounts():
    user = current_user()
    unlocked = user["subscription_status"] in ("trial", "active")
    codes = {}
    if unlocked:
        for r in rest.DISCOUNT_PARTNERS:
            codes[r["id"]] = rest.redeem_code(user["id"], r["id"])
    return render_template(
        "restaurants.html", partners=rest.DISCOUNT_PARTNERS, unlocked=unlocked, codes=codes,
    )


@app.route("/nearby")
@login_required
def nearby():
    conn = get_conn()
    user = current_user()
    unlocked = user["subscription_status"] in ("trial", "active")

    if not user["location_prompted"]:
        conn.execute(
            "INSERT INTO notifications (user_id, kettle_id, message, created_at) VALUES (?, ?, ?, ?)",
            (user["id"], None,
             "Allow location access on Nearby Deals to find the most affordable spots around you.",
             datetime.now().isoformat()),
        )
        conn.execute("UPDATE users SET location_prompted=1 WHERE id=?", (user["id"],))
        conn.commit()

    places = []
    for p in rest.ALL_PLACES:
        entry = dict(p)
        entry["code"] = rest.redeem_code(user["id"], p["id"]) if unlocked else None
        places.append(entry)

    return render_template("nearby.html", places=places, unlocked=unlocked)


# --------------------------------------------------------- nudge market --

NUDGE_PRICE_QR = 7.0


@app.route("/nudges")
@login_required
def nudge_market():
    conn = get_conn()
    listings = conn.execute(
        """SELECT cn.*, u.name AS creator_name FROM custom_nudges cn
           JOIN users u ON u.id = cn.creator_id ORDER BY cn.created_at DESC"""
    ).fetchall()
    return render_template("nudges.html", listings=listings, price=NUDGE_PRICE_QR)


@app.route("/nudges/create", methods=["POST"])
@login_required
def create_nudge():
    conn = get_conn()
    user = current_user()
    message = request.form.get("message", "").strip()
    audio_data = request.form.get("audio_data", "").strip() or None
    if not message:
        flash("Write something for your nudge first.")
        return redirect(url_for("nudge_market"))
    if len(message) > 140:
        flash("Keep your nudge under 140 characters.")
        return redirect(url_for("nudge_market"))
    if audio_data and len(audio_data) > 3_000_000:
        flash("That recording is too long — keep it short.")
        return redirect(url_for("nudge_market"))
    if user["card_balance"] < NUDGE_PRICE_QR:
        flash("You need QR %.2f in your card balance to publish a nudge." % NUDGE_PRICE_QR)
        return redirect(url_for("nudge_market"))

    conn.execute("UPDATE users SET card_balance = card_balance - ? WHERE id=?", (NUDGE_PRICE_QR, user["id"]))
    conn.execute(
        "INSERT INTO custom_nudges (creator_id, message, price_qr, created_at, audio_data) VALUES (?, ?, ?, ?, ?)",
        (user["id"], message, NUDGE_PRICE_QR, datetime.now().isoformat(), audio_data),
    )
    conn.commit()
    flash("Your nudge is live in the marketplace. You'll earn QR %.2f every time someone sends it." % NUDGE_PRICE_QR)
    return redirect(url_for("nudge_market"))


# --------------------------------------------------------------- table mode --

@app.route("/table")
@login_required
def table_index():
    conn = get_conn()
    user = current_user()
    open_tables = conn.execute(
        """SELECT DISTINCT dt.* FROM dining_tables dt
           JOIN table_members tmem ON tmem.table_id = dt.id
           WHERE tmem.user_id = ? AND dt.status = 'open'
           ORDER BY dt.created_at DESC""",
        (user["id"],),
    ).fetchall()
    other_open = conn.execute(
        """SELECT dt.*, u.name AS host_name FROM dining_tables dt
           JOIN users u ON u.id = dt.created_by
           WHERE dt.status = 'open' AND dt.id NOT IN
             (SELECT table_id FROM table_members WHERE user_id = ?)
           ORDER BY dt.created_at DESC""",
        (user["id"],),
    ).fetchall()
    return render_template(
        "table_index.html", open_tables=open_tables, other_open=other_open, restaurants=rest.DISCOUNT_PARTNERS,
    )


@app.route("/table/new", methods=["POST"])
@login_required
def table_new():
    conn = get_conn()
    user = current_user()
    restaurant_id = request.form.get("restaurant_id")
    label = request.form.get("label", "").strip() or "Table"
    partner = next((r for r in rest.DISCOUNT_PARTNERS if r["id"] == restaurant_id), None)
    if not partner:
        flash("Pick a restaurant for this table.")
        return redirect(url_for("table_index"))

    now = datetime.now().isoformat()
    cur = conn.execute(
        "INSERT INTO dining_tables (restaurant_id, label, created_by, status, created_at) VALUES (?, ?, ?, 'open', ?)",
        (restaurant_id, label, user["id"], now),
    )
    table_id = cur.lastrowid
    conn.execute(
        "INSERT INTO table_members (table_id, user_id, status, joined_at) VALUES (?, ?, 'dining', ?)",
        (table_id, user["id"], now),
    )
    conn.commit()
    flash("Table opened. Share this page so others can scan in.")
    return redirect(url_for("table_detail", table_id=table_id))


@app.route("/table/<int:table_id>")
@login_required
def table_detail(table_id):
    conn = get_conn()
    user = current_user()
    table = conn.execute("SELECT * FROM dining_tables WHERE id=?", (table_id,)).fetchone()
    if not table:
        flash("That table doesn't exist.")
        return redirect(url_for("table_index"))

    partner = next((r for r in rest.DISCOUNT_PARTNERS if r["id"] == table["restaurant_id"]), None)
    my_membership = conn.execute(
        "SELECT * FROM table_members WHERE table_id=? AND user_id=?", (table_id, user["id"])
    ).fetchone()

    discount = partner["discount"] if (partner and user["subscription_status"] in ("trial", "active")) else 0
    menu = tm.menu_with_discount(discount)

    members = conn.execute(
        """SELECT tmem.*, u.name FROM table_members tmem
           JOIN users u ON u.id = tmem.user_id
           WHERE tmem.table_id=? ORDER BY tmem.joined_at""",
        (table_id,),
    ).fetchall()
    member_ids = [m["user_id"] for m in members]

    orders = conn.execute("SELECT * FROM table_orders WHERE table_id=?", (table_id,)).fetchall()
    my_orders = [o for o in orders if o["user_id"] == user["id"]]
    all_totals = tm.totals_from_orders(orders)
    my_total = all_totals.get(user["id"], 0)

    covers_i_owe = conn.execute(
        """SELECT tc.*, u.name AS payer_name FROM table_covers tc JOIN users u ON u.id = tc.payer_id
           WHERE tc.table_id=? AND tc.debtor_id=? AND tc.settled=0""",
        (table_id, user["id"]),
    ).fetchall()
    covers_owed_to_me = conn.execute(
        """SELECT tc.*, u.name AS debtor_name FROM table_covers tc JOIN users u ON u.id = tc.debtor_id
           WHERE tc.table_id=? AND tc.payer_id=? AND tc.settled=0""",
        (table_id, user["id"]),
    ).fetchall()

    return render_template(
        "table_detail.html", table=table, partner=partner, menu=menu, discount=discount,
        members=members, member_ids=member_ids, orders=orders, my_orders=my_orders, my_total=my_total,
        my_membership=my_membership, status_labels=tm.STATUS_LABELS, all_totals=all_totals,
        covers_i_owe=covers_i_owe, covers_owed_to_me=covers_owed_to_me,
    )


@app.route("/table/<int:table_id>/join", methods=["POST"])
@login_required
def table_join(table_id):
    conn = get_conn()
    user = current_user()
    table = conn.execute("SELECT * FROM dining_tables WHERE id=?", (table_id,)).fetchone()
    if not table or table["status"] != "open":
        flash("That table isn't open anymore.")
        return redirect(url_for("table_index"))
    existing = conn.execute(
        "SELECT 1 FROM table_members WHERE table_id=? AND user_id=?", (table_id, user["id"])
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO table_members (table_id, user_id, status, joined_at) VALUES (?, ?, 'dining', ?)",
            (table_id, user["id"], datetime.now().isoformat()),
        )
        conn.commit()
        flash("You're at the table. Sign in was automatic — pick your order below.")
    return redirect(url_for("table_detail", table_id=table_id))


@app.route("/table/<int:table_id>/status", methods=["POST"])
@login_required
def table_status(table_id):
    conn = get_conn()
    user = current_user()
    status = request.form.get("status")
    spending_cap = request.form.get("spending_cap")
    exclude_drinks = 1 if request.form.get("exclude_drinks") else 0

    if status in ("dining", "late_arrival", "left_early"):
        left_at = datetime.now().isoformat() if status == "left_early" else None
        conn.execute(
            "UPDATE table_members SET status=?, left_at=? WHERE table_id=? AND user_id=?",
            (status, left_at, table_id, user["id"]),
        )

    if "prefs" in request.form:
        cap_val = float(spending_cap) if spending_cap else None
        conn.execute(
            "UPDATE table_members SET spending_cap=?, exclude_drinks=? WHERE table_id=? AND user_id=?",
            (cap_val, exclude_drinks, table_id, user["id"]),
        )

    conn.commit()
    return redirect(url_for("table_detail", table_id=table_id))


@app.route("/table/<int:table_id>/order", methods=["POST"])
@login_required
def table_order(table_id):
    conn = get_conn()
    user = current_user()
    table = conn.execute("SELECT * FROM dining_tables WHERE id=?", (table_id,)).fetchone()
    if not table or table["status"] != "open":
        flash("That table is closed.")
        return redirect(url_for("table_index"))

    partner = next((r for r in rest.DISCOUNT_PARTNERS if r["id"] == table["restaurant_id"]), None)
    discount = partner["discount"] if (partner and user["subscription_status"] in ("trial", "active")) else 0
    menu = {m["name"]: m for m in tm.menu_with_discount(discount)}

    item_name = request.form.get("item_name")
    if item_name not in menu:
        flash("Pick an item from the menu.")
        return redirect(url_for("table_detail", table_id=table_id))
    item = menu[item_name]

    members = conn.execute("SELECT * FROM table_members WHERE table_id=?", (table_id,)).fetchall()
    members_by_id = {m["user_id"]: m for m in members}
    existing_orders = conn.execute("SELECT * FROM table_orders WHERE table_id=?", (table_id,)).fetchall()

    requested = set(int(x) for x in request.form.getlist("shared_with"))
    requested.add(user["id"])
    now = datetime.now().isoformat()

    charges = tm.split_with_caps(
        item["price_qr"], item["category"], requested, user["id"], members_by_id, existing_orders, now,
    )
    shared_str = ",".join(str(u) for u in sorted(charges.keys()))

    dropped = requested - set(charges.keys())
    for uid, amount in charges.items():
        conn.execute("UPDATE users SET card_balance = card_balance - ? WHERE id=?", (amount, uid))
        diner = conn.execute("SELECT subscription_status FROM users WHERE id=?", (uid,)).fetchone()
        points = int(amount) * (2 if diner["subscription_status"] in ("trial", "active") else 1)
        conn.execute("UPDATE users SET loyalty_points = loyalty_points + ? WHERE id=?", (points, uid))
        if uid != user["id"]:
            conn.execute(
                "INSERT INTO notifications (user_id, kettle_id, message, created_at) VALUES (?, ?, ?, ?)",
                (uid, None, "%s at %s — you were charged QR %.2f (split %d ways)." % (
                    item_name, table["label"], amount, len(charges)), now),
            )

    conn.execute(
        "INSERT INTO table_orders (table_id, user_id, item_name, category, price_qr, shared_with, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (table_id, user["id"], item_name, item["category"], item["price_qr"], shared_str, now),
    )
    conn.commit()

    my_charge = charges.get(user["id"], 0)
    msg = "%s added — QR %.2f charged to your card" % (item_name, my_charge)
    if len(charges) > 1:
        msg += " (split %d ways)." % len(charges)
    else:
        msg += "."
    if dropped:
        msg += " Excluded (cap reached or opted out): %d diner(s)." % len(dropped)
    flash(msg)
    return redirect(url_for("table_detail", table_id=table_id))


@app.route("/table/<int:table_id>/order/<int:order_id>/remove", methods=["POST"])
@login_required
def table_order_remove(table_id, order_id):
    conn = get_conn()
    user = current_user()
    order = conn.execute(
        "SELECT * FROM table_orders WHERE id=? AND table_id=? AND user_id=?", (order_id, table_id, user["id"])
    ).fetchone()
    if order:
        sharers = [int(x) for x in (order["shared_with"] or str(order["user_id"])).split(",") if x]
        refund = order["price_qr"] / len(sharers)
        for uid in sharers:
            conn.execute("UPDATE users SET card_balance = card_balance + ? WHERE id=?", (refund, uid))
            diner = conn.execute("SELECT subscription_status FROM users WHERE id=?", (uid,)).fetchone()
            points = int(refund) * (2 if diner["subscription_status"] in ("trial", "active") else 1)
            conn.execute("UPDATE users SET loyalty_points = MAX(0, loyalty_points - ?) WHERE id=?", (points, uid))
        conn.execute("DELETE FROM table_orders WHERE id=?", (order_id,))
        conn.commit()
        flash("%s removed and refunded." % order["item_name"])
    return redirect(url_for("table_detail", table_id=table_id))


@app.route("/table/<int:table_id>/close", methods=["POST"])
@login_required
def table_close(table_id):
    conn = get_conn()
    user = current_user()
    table = conn.execute("SELECT * FROM dining_tables WHERE id=?", (table_id,)).fetchone()
    if not table:
        return redirect(url_for("table_index"))
    is_member = conn.execute(
        "SELECT 1 FROM table_members WHERE table_id=? AND user_id=?", (table_id, user["id"])
    ).fetchone()
    if not is_member:
        flash("Only diners at this table can close it.")
        return redirect(url_for("table_detail", table_id=table_id))
    if table["status"] != "open":
        return redirect(url_for("table_detail", table_id=table_id))

    orders = conn.execute("SELECT * FROM table_orders WHERE table_id=?", (table_id,)).fetchall()
    totals = tm.totals_from_orders(orders)
    for uid, amount in totals.items():
        conn.execute(
            "INSERT INTO notifications (user_id, kettle_id, message, created_at) VALUES (?, ?, ?, ?)",
            (uid, None, "Table closed at %s — your final total was QR %.2f. Already charged as you ordered." % (table["label"], amount),
             datetime.now().isoformat()),
        )

    conn.execute("UPDATE dining_tables SET status='closed', closed_at=? WHERE id=?", (datetime.now().isoformat(), table_id))
    conn.commit()
    flash("Table closed. Everyone was already charged as they ordered — nothing left to settle.")
    return redirect(url_for("table_index"))


@app.route("/table/<int:table_id>/cover", methods=["POST"])
@login_required
def table_cover(table_id):
    conn = get_conn()
    user = current_user()
    debtor_id = int(request.form.get("debtor_id"))
    amount = float(request.form.get("amount", 0) or 0)
    if amount <= 0:
        flash("Enter an amount to cover.")
        return redirect(url_for("table_detail", table_id=table_id))
    if amount > user["card_balance"]:
        flash("That's more than your card balance.")
        return redirect(url_for("table_detail", table_id=table_id))

    debtor = conn.execute("SELECT * FROM users WHERE id=?", (debtor_id,)).fetchone()
    table = conn.execute("SELECT * FROM dining_tables WHERE id=?", (table_id,)).fetchone()
    conn.execute("UPDATE users SET card_balance = card_balance - ? WHERE id=?", (amount, user["id"]))
    conn.execute("UPDATE users SET card_balance = card_balance + ? WHERE id=?", (amount, debtor_id))
    conn.execute(
        "INSERT INTO table_covers (table_id, payer_id, debtor_id, amount, created_at) VALUES (?, ?, ?, ?, ?)",
        (table_id, user["id"], debtor_id, amount, datetime.now().isoformat()),
    )
    conn.execute(
        "INSERT INTO notifications (user_id, kettle_id, message, created_at) VALUES (?, ?, ?, ?)",
        (debtor_id, None, "%s covered QR %.2f of your bill at %s — you now owe them directly." % (user["name"], amount, table["label"]),
         datetime.now().isoformat()),
    )
    conn.commit()
    flash("You covered QR %.2f for %s." % (amount, debtor["name"]))
    return redirect(url_for("table_detail", table_id=table_id))


@app.route("/table/<int:table_id>/settle-cover/<int:cover_id>", methods=["POST"])
@login_required
def table_settle_cover(table_id, cover_id):
    conn = get_conn()
    user = current_user()
    cover = conn.execute("SELECT * FROM table_covers WHERE id=?", (cover_id,)).fetchone()
    if not cover or cover["debtor_id"] != user["id"] or cover["settled"]:
        flash("Nothing to settle.")
        return redirect(url_for("table_detail", table_id=table_id))
    if cover["amount"] > user["card_balance"]:
        flash("That's more than your card balance.")
        return redirect(url_for("table_detail", table_id=table_id))
    conn.execute("UPDATE users SET card_balance = card_balance - ? WHERE id=?", (cover["amount"], user["id"]))
    conn.execute("UPDATE users SET card_balance = card_balance + ? WHERE id=?", (cover["amount"], cover["payer_id"]))
    conn.execute("UPDATE table_covers SET settled=1 WHERE id=?", (cover_id,))
    conn.commit()
    flash("Settled up.")
    return redirect(url_for("table_detail", table_id=table_id))


# ------------------------------------------------------------ new kettle --

@app.route("/kettle/new", methods=["GET", "POST"])
@login_required
def new_kettle():
    conn = get_conn()
    user = current_user()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        ktype = request.form.get("type")
        today = date.today().isoformat()

        if not name or ktype not in ("household", "trip"):
            flash("Please name your kettle and choose a type.")
            return render_template("new_kettle.html")

        if ktype == "household":
            cur = conn.execute(
                "INSERT INTO kettles (name, type, created_by, created_at) VALUES (?, ?, ?, ?)",
                (name, ktype, user["id"], today),
            )
            kettle_id = cur.lastrowid
            for cat in kl.CATEGORIES:
                amt = float(request.form.get(cat, 0) or 0)
                conn.execute(
                    "INSERT INTO charges (kettle_id, category, amount) VALUES (?, ?, ?)",
                    (kettle_id, cat, amt),
                )
        else:
            start = request.form.get("start_date") or today
            end = request.form.get("end_date") or today
            try:
                start_d = date.fromisoformat(start)
                end_d = date.fromisoformat(end)
            except ValueError:
                flash("That start or end date doesn't look right. Please pick valid dates.")
                return render_template("new_kettle.html")
            if end_d < start_d:
                flash("The trip's end date can't be before its start date.")
                return render_template("new_kettle.html")
            total_amount = float(request.form.get("total_amount", 0) or 0)
            cur = conn.execute(
                "INSERT INTO kettles (name, type, created_by, start_date, end_date, total_amount, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, ktype, user["id"], start, end, total_amount, today),
            )
            kettle_id = cur.lastrowid

        creator_join_date = start if ktype == "trip" else today
        conn.execute(
            "INSERT INTO kettle_members (kettle_id, user_id, join_date, status) VALUES (?, ?, ?, 'active')",
            (kettle_id, user["id"], creator_join_date),
        )
        conn.commit()
        flash("Kettle '%s' is on the boil." % name)
        return redirect(url_for("kettle_detail", kettle_id=kettle_id))

    return render_template("new_kettle.html")


# --------------------------------------------------------- kettle detail --

@app.route("/kettle/<int:kettle_id>")
@login_required
def kettle_detail(kettle_id):
    conn = get_conn()
    user = current_user()
    kettle = kl.get_kettle(conn, kettle_id)
    if not kettle:
        flash("Kettle not found.")
        return redirect(url_for("dashboard"))

    kl.maybe_finalize_leaving_members(conn, kettle_id)

    members = kl.get_members(conn, kettle_id)
    my_membership = member_or_404(conn, kettle_id, user["id"])
    if not my_membership:
        flash("You're not a member of that kettle.")
        return redirect(url_for("dashboard"))

    balances = kl.kettle_balances(conn, kettle_id)
    charges = kl.get_charges(conn, kettle_id) if kettle["type"] == "household" else None

    month = kl.current_month_key()
    eom_rows = conn.execute(
        "SELECT user_id FROM pay_by_eom WHERE kettle_id=? AND month=?", (kettle_id, month)
    ).fetchall()
    eom_pressed = {r["user_id"] for r in eom_rows}

    members_view = []
    for m in members:
        bal = balances.get(m["user_id"], {"share": 0, "paid": 0, "covered": 0, "owed": 0})
        members_view.append({
            "member": m,
            "balance": bal,
            "eom": m["user_id"] in eom_pressed,
            "is_me": m["user_id"] == user["id"],
        })
    members_view.sort(key=lambda x: (-x["balance"]["owed"]))

    other_users = conn.execute(
        """SELECT * FROM users WHERE id NOT IN
           (SELECT user_id FROM kettle_members WHERE kettle_id=? AND status != 'left')""",
        (kettle_id,),
    ).fetchall()

    landlord = None
    if kettle["landlord_id"]:
        landlord = conn.execute("SELECT * FROM users WHERE id=?", (kettle["landlord_id"],)).fetchone()

    custom_nudges = conn.execute(
        """SELECT cn.*, u.name AS creator_name FROM custom_nudges cn
           JOIN users u ON u.id = cn.creator_id ORDER BY cn.created_at DESC"""
    ).fetchall()

    return render_template(
        "kettle_detail.html",
        kettle=kettle,
        members_view=members_view,
        charges=charges,
        categories=kl.CATEGORIES,
        other_users=other_users,
        my_owed=balances.get(user["id"], {}).get("owed", 0),
        today=date.today().isoformat(),
        landlord=landlord,
        custom_nudges=custom_nudges,
    )


@app.route("/kettle/<int:kettle_id>/charges", methods=["POST"])
@login_required
def update_charges(kettle_id):
    conn = get_conn()
    kettle = kl.get_kettle(conn, kettle_id)
    if not kettle or kettle["type"] != "household":
        return redirect(url_for("kettle_detail", kettle_id=kettle_id))

    before, base_charges = kl.household_shares(conn, kettle_id)
    for cat in kl.CATEGORIES:
        amt = float(request.form.get(cat, 0) or 0)
        conn.execute(
            "UPDATE charges SET amount=? WHERE kettle_id=? AND category=?", (amt, kettle_id, cat)
        )
    conn.commit()

    after, _ = kl.household_shares(conn, kettle_id)
    notify_members(conn, kettle_id, None,
                    "The %s kettle's charges were updated — check your new share." % kettle["name"])
    flash("Charges updated. Everyone's split has been recalculated.")
    return redirect(url_for("kettle_detail", kettle_id=kettle_id))


@app.route("/kettle/<int:kettle_id>/assign-landlord", methods=["POST"])
@login_required
def assign_landlord(kettle_id):
    conn = get_conn()
    user = current_user()
    kettle = kl.get_kettle(conn, kettle_id)
    if not kettle or kettle["type"] != "household":
        flash("Only household kettles can have a landlord.")
        return redirect(url_for("kettle_detail", kettle_id=kettle_id))
    if not member_or_404(conn, kettle_id, user["id"]):
        flash("You're not a member of that kettle.")
        return redirect(url_for("dashboard"))

    email = request.form.get("landlord_email", "").strip().lower()
    landlord = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not landlord:
        flash("No Kettle account found for that email — they need to sign up first.")
        return redirect(url_for("kettle_detail", kettle_id=kettle_id))

    tenant_row = member_or_404(conn, kettle_id, landlord["id"])
    if tenant_row and tenant_row["status"] != "left":
        flash("A current tenant can't also be the landlord.")
        return redirect(url_for("kettle_detail", kettle_id=kettle_id))

    conn.execute("UPDATE kettles SET landlord_id=? WHERE id=?", (landlord["id"], kettle_id))
    conn.execute(
        "INSERT INTO notifications (user_id, kettle_id, message, created_at) VALUES (?, ?, ?, ?)",
        (landlord["id"], kettle_id,
         "You were added as the landlord for %s. You'll see payment status only — never individual purchases." % kettle["name"],
         datetime.now().isoformat()),
    )
    conn.commit()
    flash("%s is now the landlord for this kettle." % landlord["name"])
    return redirect(url_for("kettle_detail", kettle_id=kettle_id))


@app.route("/kettle/<int:kettle_id>/remove-landlord", methods=["POST"])
@login_required
def remove_landlord(kettle_id):
    conn = get_conn()
    user = current_user()
    if not member_or_404(conn, kettle_id, user["id"]):
        flash("You're not a member of that kettle.")
        return redirect(url_for("dashboard"))
    conn.execute("UPDATE kettles SET landlord_id=NULL WHERE id=?", (kettle_id,))
    # No landlord left to approve pending exits, so they're released automatically.
    conn.execute("UPDATE kettle_members SET status='left' WHERE kettle_id=? AND status='leave_pending'", (kettle_id,))
    conn.commit()
    flash("Landlord removed from this kettle. Any pending exits were released.")
    return redirect(url_for("kettle_detail", kettle_id=kettle_id))


@app.route("/landlord")
@login_required
def landlord_dashboard():
    conn = get_conn()
    user = current_user()
    kettles = kl.landlord_kettles(conn, user["id"])
    data = []
    for k in kettles:
        kl.maybe_finalize_leaving_members(conn, k["id"])
        tenants = kl.tenant_payment_status(conn, k["id"])
        paid_count = sum(1 for t in tenants if t["paid_up"])
        data.append({"kettle": k, "tenants": tenants, "paid_count": paid_count, "total": len(tenants)})
    return render_template("landlord_dashboard.html", data=data)


def notify_members(conn, kettle_id, exclude_user_id, message, sound_preset=None, custom_nudge_id=None):
    members = kl.get_members(conn, kettle_id)
    now = datetime.now().isoformat()
    for m in members:
        if exclude_user_id and m["user_id"] == exclude_user_id:
            continue
        conn.execute(
            "INSERT INTO notifications (user_id, kettle_id, message, created_at, sound_preset, custom_nudge_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (m["user_id"], kettle_id, message, now, sound_preset, custom_nudge_id),
        )
    conn.commit()


# ---------------------------------------------------------- add a member --

@app.route("/kettle/<int:kettle_id>/add_member", methods=["GET", "POST"])
@login_required
def add_member(kettle_id):
    conn = get_conn()
    kettle = kl.get_kettle(conn, kettle_id)
    if not kettle:
        return redirect(url_for("dashboard"))

    other_users = conn.execute(
        """SELECT * FROM users WHERE id NOT IN
           (SELECT user_id FROM kettle_members WHERE kettle_id=? AND status != 'left')""",
        (kettle_id,),
    ).fetchall()

    if request.method == "POST":
        step = request.form.get("step", "preview")
        target_id = int(request.form.get("user_id"))
        join_date = request.form.get("join_date") or date.today().isoformat()
        if kettle["type"] == "trip":
            try:
                date.fromisoformat(join_date)
            except ValueError:
                flash("That join date doesn't look right. Please pick a valid date.")
                return redirect(url_for("add_member", kettle_id=kettle_id))
        target = conn.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()

        if kettle["type"] == "household":
            before, base = kl.household_shares(conn, kettle_id)
            after, _ = kl.household_shares(conn, kettle_id, hypothetical_extra_member=True)
            preview = {"before": before, "after": after, "new_member_share": {
                c: round(base[c] / (len(before) + 1), 2) for c in kl.CATEGORIES
            }}
        else:
            before = kl.trip_shares(conn, kettle_id)
            phantom = {"user_id": "preview", "join_date": join_date, "left_date": None, "status": "active"}
            after_incl = kl.trip_shares(conn, kettle_id, extra_member=phantom)
            preview = {"before": before, "after": after_incl, "new_member_share": {"total": after_incl.get("preview", 0)}}

        if step == "confirm":
            conn.execute(
                "INSERT INTO kettle_members (kettle_id, user_id, join_date, status) VALUES (?, ?, ?, 'active')",
                (kettle_id, target_id, join_date),
            )
            conn.commit()
            notify_members(
                conn, kettle_id, target_id,
                "%s joined the %s kettle — the split just updated. See your new share." % (target["name"], kettle["name"]),
            )
            conn.execute(
                "INSERT INTO notifications (user_id, kettle_id, message, created_at) VALUES (?, ?, ?, ?)",
                (target_id, kettle_id, "You were added to the %s kettle." % kettle["name"], datetime.now().isoformat()),
            )
            conn.commit()
            flash("%s added. Everyone's been notified of the new split." % target["name"])
            return redirect(url_for("kettle_detail", kettle_id=kettle_id))

        members = kl.get_members(conn, kettle_id)
        id_to_name = {m["user_id"]: m["name"] for m in members}
        return render_template(
            "add_member_preview.html", kettle=kettle, target=target, join_date=join_date,
            preview=preview, id_to_name=id_to_name, categories=kl.CATEGORIES,
        )

    return render_template("add_member.html", kettle=kettle, other_users=other_users, today=date.today().isoformat())


# ------------------------------------------------------------------ pay --

@app.route("/kettle/<int:kettle_id>/pay", methods=["POST"])
@login_required
def pay(kettle_id):
    conn = get_conn()
    user = current_user()
    amount = float(request.form.get("amount", 0) or 0)
    if amount <= 0:
        flash("Enter an amount to pay.")
        return redirect(url_for("kettle_detail", kettle_id=kettle_id))
    if amount > user["card_balance"]:
        flash("That's more than your card balance.")
        return redirect(url_for("kettle_detail", kettle_id=kettle_id))

    conn.execute(
        "INSERT INTO payments (kettle_id, user_id, amount, note, created_at) VALUES (?, ?, ?, ?, ?)",
        (kettle_id, user["id"], amount, "payment", datetime.now().isoformat()),
    )
    conn.execute("UPDATE users SET card_balance = card_balance - ? WHERE id=?", (amount, user["id"]))
    conn.execute("UPDATE users SET loyalty_points = loyalty_points + ? WHERE id=?",
                 (int(amount) * (2 if user["subscribed"] else 1), user["id"]))
    conn.commit()
    kl.maybe_finalize_leaving_members(conn, kettle_id)
    flash("Paid QR %.2f into the kettle." % amount)
    return redirect(url_for("kettle_detail", kettle_id=kettle_id))


@app.route("/kettle/<int:kettle_id>/cover", methods=["POST"])
@login_required
def cover(kettle_id):
    conn = get_conn()
    user = current_user()
    debtor_id = int(request.form.get("debtor_id"))
    amount = float(request.form.get("amount", 0) or 0)
    if amount <= 0:
        flash("Enter an amount to cover.")
        return redirect(url_for("kettle_detail", kettle_id=kettle_id))
    if amount > user["card_balance"]:
        flash("That's more than your card balance.")
        return redirect(url_for("kettle_detail", kettle_id=kettle_id))

    debtor = conn.execute("SELECT * FROM users WHERE id=?", (debtor_id,)).fetchone()
    conn.execute(
        "INSERT INTO covers (kettle_id, payer_id, debtor_id, amount, created_at) VALUES (?, ?, ?, ?, ?)",
        (kettle_id, user["id"], debtor_id, amount, datetime.now().isoformat()),
    )
    conn.execute("UPDATE users SET card_balance = card_balance - ? WHERE id=?", (amount, user["id"]))
    conn.commit()
    conn.execute(
        "INSERT INTO notifications (user_id, kettle_id, message, created_at) VALUES (?, ?, ?, ?)",
        (debtor_id, kettle_id, "%s covered QR %.2f for you — you now owe them directly." % (user["name"], amount),
         datetime.now().isoformat()),
    )
    conn.commit()
    kl.maybe_finalize_leaving_members(conn, kettle_id)
    flash("You covered QR %.2f for %s. It'll show they owe you." % (amount, debtor["name"]))
    return redirect(url_for("kettle_detail", kettle_id=kettle_id))


@app.route("/settle_debt/<int:cover_id>", methods=["POST"])
@login_required
def settle_debt(cover_id):
    conn = get_conn()
    user = current_user()
    c = conn.execute("SELECT * FROM covers WHERE id=?", (cover_id,)).fetchone()
    if not c or c["debtor_id"] != user["id"]:
        flash("Not found.")
        return redirect(url_for("dashboard"))
    if c["amount"] > user["card_balance"]:
        flash("That's more than your card balance.")
        return redirect(url_for("dashboard"))
    conn.execute("UPDATE users SET card_balance = card_balance - ? WHERE id=?", (c["amount"], user["id"]))
    conn.execute("UPDATE users SET card_balance = card_balance + ? WHERE id=?", (c["amount"], c["payer_id"]))
    conn.execute("UPDATE covers SET settled=1 WHERE id=?", (cover_id,))
    conn.commit()
    flash("Debt settled.")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------- nudge --

@app.route("/kettle/<int:kettle_id>/nudge/<int:target_id>", methods=["POST"])
@login_required
def nudge(kettle_id, target_id):
    conn = get_conn()
    user = current_user()
    kettle = kl.get_kettle(conn, kettle_id)
    target = conn.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
    month = kl.current_month_key()
    pressed = conn.execute(
        "SELECT 1 FROM pay_by_eom WHERE kettle_id=? AND user_id=? AND month=?",
        (kettle_id, target_id, month),
    ).fetchone()
    if pressed:
        flash("They've committed to pay by end of month — nudging is off for them.")
        return redirect(url_for("kettle_detail", kettle_id=kettle_id))

    nudge_id = request.form.get("nudge_id")
    sound_preset = request.form.get("sound_preset") or None
    custom_nudge_id = None
    _, emoji, escalation_note = kl.nudge_escalation(conn, kettle_id, target_id)
    tail = " (%s)" % escalation_note if escalation_note else ""
    message = "%s %s got nudged about their balance in %s%s. (sender anonymous)" % (emoji, target["name"], kettle["name"], tail)

    if nudge_id:
        custom = conn.execute("SELECT * FROM custom_nudges WHERE id=?", (nudge_id,)).fetchone()
        if not custom:
            flash("That custom nudge no longer exists.")
            return redirect(url_for("kettle_detail", kettle_id=kettle_id))
        if user["card_balance"] < custom["price_qr"]:
            flash("You don't have enough balance to send that paid nudge.")
            return redirect(url_for("kettle_detail", kettle_id=kettle_id))
        conn.execute("UPDATE users SET card_balance = card_balance - ? WHERE id=?", (custom["price_qr"], user["id"]))
        if custom["creator_id"] != user["id"]:
            conn.execute("UPDATE users SET card_balance = card_balance + ? WHERE id=?", (custom["price_qr"], custom["creator_id"]))
        conn.execute("UPDATE custom_nudges SET times_used = times_used + 1 WHERE id=?", (custom["id"],))
        custom_nudge_id = custom["id"]
        sound_preset = None
        kind = "audio nudge" if custom["audio_data"] else "nudge"
        message = "\U0001F4AC %s got a paid %s in %s: “%s”%s (sender anonymous)" % (
            target["name"], kind, kettle["name"], custom["message"], tail,
        )

    conn.execute(
        "INSERT INTO nudges (kettle_id, to_user_id, from_user_id, created_at) VALUES (?, ?, NULL, ?)",
        (kettle_id, target_id, datetime.now().isoformat()),
    )
    notify_members(conn, kettle_id, user["id"], message, sound_preset=sound_preset, custom_nudge_id=custom_nudge_id)
    conn.commit()
    flash("Nudge sent to the whole kettle." if not nudge_id else "Paid nudge sent to the whole kettle for QR %.2f." % custom["price_qr"])
    return redirect(url_for("kettle_detail", kettle_id=kettle_id))


@app.route("/kettle/<int:kettle_id>/pay_by_eom", methods=["POST"])
@login_required
def pay_by_eom(kettle_id):
    conn = get_conn()
    user = current_user()
    month = kl.current_month_key()
    existing = conn.execute(
        "SELECT 1 FROM pay_by_eom WHERE kettle_id=? AND user_id=? AND month=?",
        (kettle_id, user["id"], month),
    ).fetchone()
    if existing:
        conn.execute(
            "DELETE FROM pay_by_eom WHERE kettle_id=? AND user_id=? AND month=?",
            (kettle_id, user["id"], month),
        )
        flash("Pay-by-end-of-month commitment removed.")
    else:
        conn.execute(
            "INSERT INTO pay_by_eom (kettle_id, user_id, month, pressed_at) VALUES (?, ?, ?, ?)",
            (kettle_id, user["id"], month, datetime.now().isoformat()),
        )
        flash("Got it — nobody can nudge you as long as you pay by month end.")
    conn.commit()
    return redirect(url_for("kettle_detail", kettle_id=kettle_id))


# ------------------------------------------------------------------ leave --

@app.route("/kettle/<int:kettle_id>/leave", methods=["POST"])
@login_required
def leave_kettle(kettle_id):
    conn = get_conn()
    user = current_user()
    kettle = kl.get_kettle(conn, kettle_id)
    balances = kl.kettle_balances(conn, kettle_id)
    owed = balances.get(user["id"], {}).get("owed", 0)
    today = date.today().isoformat()
    gated = kettle["type"] == "household" and kettle["landlord_id"]

    if owed <= 0.01:
        if gated:
            conn.execute(
                "UPDATE kettle_members SET status='leave_pending', left_date=? WHERE kettle_id=? AND user_id=?",
                (today, kettle_id, user["id"]),
            )
            conn.commit()
            conn.execute(
                "INSERT INTO notifications (user_id, kettle_id, message, created_at) VALUES (?, ?, ?, ?)",
                (kettle["landlord_id"], kettle_id,
                 "%s is settled up and wants to leave %s — your approval is needed." % (user["name"], kettle["name"]),
                 datetime.now().isoformat()),
            )
            conn.commit()
            flash("You're settled up. Your landlord needs to approve your exit before you're fully off the kettle.")
            return redirect(url_for("kettle_detail", kettle_id=kettle_id))

        conn.execute(
            "UPDATE kettle_members SET status='left', left_date=? WHERE kettle_id=? AND user_id=?",
            (today, kettle_id, user["id"]),
        )
        conn.commit()
        notify_members(conn, kettle_id, user["id"], "%s left the %s kettle. Splits have updated." % (user["name"], kettle["name"]))
        flash("You've left the kettle.")
        return redirect(url_for("dashboard"))
    else:
        conn.execute(
            "UPDATE kettle_members SET status='leaving', left_date=? WHERE kettle_id=? AND user_id=?",
            (today, kettle_id, user["id"]),
        )
        conn.commit()
        notify_members(
            conn, kettle_id, user["id"],
            "%s asked to leave the %s kettle but still owes QR %.2f — they stay listed until it's settled." % (user["name"], kettle["name"], owed),
        )
        flash("You still owe QR %.2f, so you'll stay listed and liable until it clears." % owed)
        return redirect(url_for("kettle_detail", kettle_id=kettle_id))


@app.route("/kettle/<int:kettle_id>/landlord/approve-leave/<int:target_id>", methods=["POST"])
@login_required
def approve_leave(kettle_id, target_id):
    conn = get_conn()
    user = current_user()
    kettle = kl.get_kettle(conn, kettle_id)
    if not kettle or kettle["landlord_id"] != user["id"]:
        flash("You don't have landlord access to that kettle.")
        return redirect(url_for("landlord_dashboard"))

    target = conn.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
    conn.execute(
        "UPDATE kettle_members SET status='left' WHERE kettle_id=? AND user_id=? AND status='leave_pending'",
        (kettle_id, target_id),
    )
    conn.commit()
    conn.execute(
        "INSERT INTO notifications (user_id, kettle_id, message, created_at) VALUES (?, ?, ?, ?)",
        (target_id, kettle_id, "Your landlord approved your exit from %s. You're off the kettle." % kettle["name"],
         datetime.now().isoformat()),
    )
    conn.commit()
    notify_members(conn, kettle_id, target_id, "%s left %s. Splits have updated." % (target["name"], kettle["name"]))
    flash("%s has been approved to leave and is off the kettle." % target["name"])
    return redirect(url_for("landlord_dashboard"))


@app.route("/kettle/<int:kettle_id>/landlord/deny-leave/<int:target_id>", methods=["POST"])
@login_required
def deny_leave(kettle_id, target_id):
    conn = get_conn()
    user = current_user()
    kettle = kl.get_kettle(conn, kettle_id)
    if not kettle or kettle["landlord_id"] != user["id"]:
        flash("You don't have landlord access to that kettle.")
        return redirect(url_for("landlord_dashboard"))

    target = conn.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
    conn.execute(
        "UPDATE kettle_members SET status='active' WHERE kettle_id=? AND user_id=? AND status='leave_pending'",
        (kettle_id, target_id),
    )
    conn.commit()
    conn.execute(
        "INSERT INTO notifications (user_id, kettle_id, message, created_at) VALUES (?, ?, ?, ?)",
        (target_id, kettle_id, "Your landlord did not approve your exit from %s — you're still an active member." % kettle["name"],
         datetime.now().isoformat()),
    )
    conn.commit()
    flash("%s's exit was denied — they're still an active member." % target["name"])
    return redirect(url_for("landlord_dashboard"))


@app.route("/kettle/<int:kettle_id>/landlord/release-early/<int:target_id>", methods=["POST"])
@login_required
def release_early(kettle_id, target_id):
    conn = get_conn()
    user = current_user()
    kettle = kl.get_kettle(conn, kettle_id)
    if not kettle or kettle["landlord_id"] != user["id"]:
        flash("You don't have landlord access to that kettle.")
        return redirect(url_for("landlord_dashboard"))

    target = conn.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
    balances = kl.kettle_balances(conn, kettle_id)
    owed = balances.get(target_id, {}).get("owed", 0)
    conn.execute(
        "UPDATE kettle_members SET status='left', left_date=? WHERE kettle_id=? AND user_id=?",
        (date.today().isoformat(), kettle_id, target_id),
    )
    conn.commit()
    note = " (QR %.2f still outstanding, waived by landlord)" % owed if owed > 0.01 else ""
    conn.execute(
        "INSERT INTO notifications (user_id, kettle_id, message, created_at) VALUES (?, ?, ?, ?)",
        (target_id, kettle_id, "Your landlord released you early from %s%s." % (kettle["name"], note),
         datetime.now().isoformat()),
    )
    conn.commit()
    notify_members(
        conn, kettle_id, None,
        "%s was released early from %s by the landlord%s." % (target["name"], kettle["name"], note),
    )
    flash("%s has been released early." % target["name"])
    return redirect(url_for("landlord_dashboard"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
