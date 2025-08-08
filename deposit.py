from flask import Blueprint, render_template, session, redirect, url_for, request, flash
from bson import ObjectId
from datetime import datetime
import requests

from db import db

deposit_bp = Blueprint("deposit", __name__)

balances_col = db["balances"]
transactions_col = db["transactions"]
users_col = db["users"]

# 1. Show deposit page
@deposit_bp.route("/deposit")
def deposit_page():
    if session.get("role") != "customer" or "user_id" not in session:
        return redirect(url_for("login.login"))

    # Try to get email from session or fallback to DB
    email = session.get("email")
    if not email:
        user = users_col.find_one({"_id": ObjectId(session["user_id"])})
        email = user.get("email", "") if user else ""

    return render_template("deposit.html", user_id=session["user_id"], email=email)


# 2. Verify Paystack transaction
@deposit_bp.route("/verify_transaction")
def verify_transaction():
    reference = request.args.get("reference")
    amount = float(request.args.get("amount", 0))
    user_id = session.get("user_id")

    if not reference or not user_id or amount <= 0:
        flash("❌ Invalid deposit request", "danger")
        return redirect(url_for("customer_dashboard.customer_dashboard"))

    # ✅ Use your actual secret key (test mode)
    headers = {
        "Authorization": "Bearer sk_test_da4f5960a63125d3757f7a81047aa61b2843f19b"
    }
    url = f"https://api.paystack.co/transaction/verify/{reference}"

    try:
        response = requests.get(url, headers=headers)
        result = response.json()
        print("🧾 Paystack Verification Response:", result)  # Debug output

        if result.get("status") and result["data"].get("status") == "success":
            # 1. Update balance
            balances_col.update_one(
                {"user_id": ObjectId(user_id)},
                {
                    "$inc": {"amount": amount},
                    "$set": {"updated_at": datetime.utcnow()}
                }
            )

            # 2. Save transaction
            transactions_col.insert_one({
                "user_id": ObjectId(user_id),
                "amount": amount,
                "reference": reference,
                "status": "success",
                "type": "deposit",
                "gateway": "Paystack",
                "currency": "GHS",
                "verified_at": datetime.utcnow()
            })

            flash("✅ Deposit successful! Your balance has been updated.", "success")
        else:
            fail_msg = result.get("message", "Verification failed.")
            flash(f"❌ Payment verification failed: {fail_msg}", "danger")

    except Exception as e:
        print("❌ Paystack Exception:", str(e))
        flash("❌ Could not verify payment. Please check your internet connection or try again.", "danger")

    return redirect(url_for("customer_dashboard.customer_dashboard"))
