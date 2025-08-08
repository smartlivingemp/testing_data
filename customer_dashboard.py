from flask import Blueprint, render_template, session, redirect, url_for
from bson import ObjectId
from db import db

customer_dashboard_bp = Blueprint("customer_dashboard", __name__)
services_col = db["services"]
balances_col = db["balances"]
orders_col = db["orders"]

def _format_volume(vol_mb):
    try:
        v = float(vol_mb)
    except Exception:
        return "-"
    if v >= 1000:
        gb = v / 1000.0
        return f"{int(round(gb))}GB" if abs(gb - round(gb)) < 1e-9 else f"{gb:.2f}GB"
    return f"{int(v)}MB"

def _compute_value_text(value):
    # dict: {'volume': MB, 'id': package_id}
    if isinstance(value, dict):
        vol = value.get("volume")
        return _format_volume(vol) if vol else "-"
    # string fallback
    return value or "-"


@customer_dashboard_bp.route("/customer/dashboard")
def customer_dashboard():
    if session.get("role") != "customer":
        return redirect(url_for("login.login"))

    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login.login"))

    services = list(services_col.find().sort("created_at", -1))
    for s in services:
        s["_id_str"] = str(s["_id"])
        if isinstance(s.get("offers"), list):
            for of in s["offers"]:
                of["value_text"] = _compute_value_text(of.get("value"))

    # Balance
    balance_doc = balances_col.find_one({"user_id": ObjectId(user_id)})
    balance = balance_doc["amount"] if balance_doc else 0.00

    # Recent orders
    recent_orders = list(
        orders_col.find({"user_id": ObjectId(user_id)})
        .sort("created_at", -1)
        .limit(5)
    )

    return render_template(
        "customer_dashboard.html",
        services=services,
        balance=balance,
        recent_orders=recent_orders
    )
