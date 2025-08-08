from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from db import db
from werkzeug.security import check_password_hash

login_bp = Blueprint("login", __name__)
users_col = db["users"]

@login_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        # Find by username only
        user = users_col.find_one({"username": username})

        if not user or not check_password_hash(user["password"], password):
            flash("❌ Invalid username or password", "danger")
            return render_template("login.html")

        # Reset and set common session data
        session.clear()
        session["user_id"] = str(user["_id"])
        session["username"] = user["username"]
        session["role"] = user.get("role", "customer")

        # Role-based redirect
        if session["role"] == "admin":
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard.admin_dashboard"))
        else:
            session["customer_logged_in"] = True
            return redirect(url_for("customer_dashboard.customer_dashboard"))

    return render_template("login.html")

@login_bp.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully", "info")
    return redirect(url_for("login.login"))
