# checkout.py
from flask import Blueprint, request, jsonify, session
from bson import ObjectId
from datetime import datetime
import os
import uuid
import random
import json
import requests
import certifi

from db import db

checkout_bp = Blueprint("checkout", __name__)

# MongoDB Collections
balances_col = db["balances"]
orders_col = db["orders"]
transactions_col = db["transactions"]
services_col = db["services"]  # to read network info if needed later

TOPPILY_URL = "https://toppily.com/api/v1/buy-other-package"
TOPPILY_API_KEY = os.getenv("TOPPILY_API_KEY", "68b5ec28de8abe4f99a77a5434e032fc78b8d2d8")

def generate_order_id():
    return f"NAN{random.randint(10000, 99999)}"

def _money(v):
    try:
        return float(v)
    except Exception:
        return 0.0

def _send_toppily_by_package(phone: str, package_id: int, trx_ref: str):
    """
    Send one purchase to Toppily using package_id flow.
    Returns (success: bool, payload: dict) where payload is the parsed/Raw response.
    """
    headers = {
        "x-api-key": TOPPILY_API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    body = {
        "recipient_msisdn": phone,
        "package_id": int(package_id),
        "trx_ref": trx_ref,
    }
    try:
        resp = requests.post(
            TOPPILY_URL,
            headers=headers,
            json=body,
            timeout=25,
            verify=certifi.where(),  # fix SSL on Windows/older envs
        )
        text = resp.text or ""
        # Try to decode JSON; if it fails, store raw string
        try:
            data = resp.json()
        except Exception:
            data = {"raw": text, "http_status": resp.status_code}

        # Toppily returns {"success": true/false, ...}
        ok = resp.ok and bool(data.get("success", False))
        return ok, data
    except requests.RequestException as e:
        return False, {"error": str(e)}

@checkout_bp.route("/checkout", methods=["POST"])
def process_checkout():
    # 🔐 Auth
    if "user_id" not in session or session.get("role") != "customer":
        return jsonify({"success": False, "message": "Not authorized"}), 401

    try:
        user_id = ObjectId(session["user_id"])
    except Exception:
        return jsonify({"success": False, "message": "Invalid user ID"}), 400

    data = request.get_json(silent=True) or {}
    cart = data.get("cart", [])
    method = data.get("method", "wallet")

    # 🛒 Validate cart
    if not cart or not isinstance(cart, list):
        return jsonify({"success": False, "message": "Cart is empty or invalid"}), 400

    # 💰 Compute totals from cart
    total_requested = 0.0
    for item in cart:
        total_requested += _money(item.get("amount"))

    if total_requested <= 0:
        return jsonify({"success": False, "message": "Total amount must be greater than zero"}), 400

    # 🧾 Generate order ID & per-item refs
    order_id = generate_order_id()

    # 🏦 Check wallet balance up-front (must cover full cart to proceed)
    bal_doc = balances_col.find_one({"user_id": user_id}) or {}
    current_balance = _money(bal_doc.get("amount", 0))
    if current_balance < total_requested:
        return jsonify({"success": False, "message": "❌ Insufficient wallet balance"}), 400

    # 🚀 Call Toppily for each item by package_id
    results = []
    total_success_amount = 0.0
    for idx, item in enumerate(cart, start=1):
        phone = (item.get("phone") or "").strip()
        # We stored parsed value in value_obj: {"id": <package_id>, "volume": <MB>}
        value_obj = item.get("value_obj") or {}
        pkg_id = value_obj.get("id")

        if not phone or not pkg_id:
            results.append({
                "phone": phone,
                "amount": _money(item.get("amount")),
                "value": item.get("value"),
                "value_obj": value_obj,
                "api_status": "skipped",
                "api_response": {"error": "Missing phone or package_id"}
            })
            continue

        trx_ref = f"{order_id}_{idx}_{uuid.uuid4().hex[:6]}"
        ok, payload = _send_toppily_by_package(phone, int(pkg_id), trx_ref)

        results.append({
            "phone": phone,
            "amount": _money(item.get("amount")),
            "value": item.get("value"),
            "value_obj": value_obj,
            "serviceId": item.get("serviceId"),
            "serviceName": item.get("serviceName"),
            "trx_ref": trx_ref,
            "api_status": "success" if ok else "failed",
            "api_response": payload
        })

        if ok:
            total_success_amount += _money(item.get("amount"))

    # 🧮 Nothing succeeded?
    if total_success_amount <= 0:
        # Log failed order
        order_doc = {
            "user_id": user_id,
            "order_id": order_id,
            "items": results,
            "total_amount": total_requested,
            "charged_amount": 0.0,
            "status": "failed",
            "paid_from": method,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        orders_col.insert_one(order_doc)
        return jsonify({
            "success": False,
            "message": "No items were processed successfully. You were not charged.",
            "order_id": order_id,
            "details": results
        }), 502

    # 💳 Charge only for successful items
    # (Since we pre-checked the full balance, we can safely deduct the smaller success sum now.)
    balances_col.update_one(
        {"user_id": user_id},
        {"$inc": {"amount": -total_success_amount}, "$set": {"updated_at": datetime.utcnow()}},
        upsert=True
    )

    # 🧾 Record order with per-item statuses
    status = "completed" if total_success_amount == total_requested else "partial"
    order_doc = {
        "user_id": user_id,
        "order_id": order_id,
        "items": results,
        "total_amount": total_requested,   # what user asked to buy
        "charged_amount": total_success_amount,  # what we actually charged
        "status": status,
        "paid_from": method,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    orders_col.insert_one(order_doc)

    # 🧾 Transaction record (only for charged amount)
    transactions_col.insert_one({
        "user_id": user_id,
        "amount": total_success_amount,
        "reference": order_id,
        "status": "success",
        "type": "purchase",
        "gateway": "Wallet",
        "currency": "GHS",
        "created_at": datetime.utcnow(),
        "verified_at": datetime.utcnow(),
        "meta": {"order_status": status}
    })

    # 🎉 Response
    msg = "✅ Order completed." if status == "completed" else "⚠️ Order partially completed. You were charged only for successful items."
    return jsonify({
        "success": True,
        "message": f"{msg} Order ID: {order_id}",
        "order_id": order_id,
        "status": status,
        "charged_amount": round(total_success_amount, 2),
        "items": results
    }), 200
