# routes/orders.py
from flask import Blueprint, render_template, session, redirect, url_for
from bson import ObjectId
from db import db

orders_bp = Blueprint("orders", __name__)
orders_col = db["orders"]

@orders_bp.route("/customer/orders")
def view_orders():
    if session.get("role") != "customer":
        return redirect(url_for("login.login"))

    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login.login"))

    all_orders = list(
        orders_col.find({"user_id": ObjectId(user_id)})
        .sort("created_at", -1)
    )

    return render_template("orders.html", orders=all_orders)
