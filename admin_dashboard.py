from flask import Blueprint, render_template, session, redirect, url_for

admin_dashboard_bp = Blueprint("admin_dashboard", __name__)

@admin_dashboard_bp.route("/admin/dashboard")
def admin_dashboard():
    # Protect route: only accessible if admin is logged in
    if not session.get("admin_logged_in"):
        return redirect(url_for("auth.login"))

    return render_template("admin_dashboard.html")
