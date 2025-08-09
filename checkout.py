# checkout.py
from flask import Blueprint, request, jsonify, session
from bson import ObjectId
from datetime import datetime
import os, uuid, random, time, requests, certifi, traceback
from db import db

checkout_bp = Blueprint("checkout", __name__)

# MongoDB Collections
balances_col = db["balances"]
orders_col = db["orders"]
transactions_col = db["transactions"]
services_col = db["services"]

# --- Toppily config (HARD-CODED) ---
TOPPILY_URL = "https://toppily.com/api/v1/buy-other-package"
TOPPILY_API_KEY = "REPLACE_WITH_YOUR_REAL_TOPPILY_API_KEY"  # <-- put your key here
TOPPILY_VERIFY_SSL = True       # primary behavior
ALLOW_INSECURE_TLS_FALLBACK = True  # if cert verify fails, retry once with verify=False

# Prefer certifi's CA bundle
try:
    ca_path = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", ca_path)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_path)
    print("[SSL] Using CA bundle:", ca_path)
except Exception as _e:
    print("[SSL] Could not set CA envs:", _e)

# --- Helpers ---
def generate_order_id():
    return f"NAN{random.randint(10000, 99999)}"

def _money(v):
    try:
        return float(v)
    except Exception:
        return 0.0

def _post_toppily(body, verify):
    headers = {
        "x-api-key": TOPPILY_API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        TOPPILY_URL,
        headers=headers,
        json=body,
        timeout=35,
        verify=verify,
    )
    text = resp.text or ""
    try:
        data = resp.json()
    except Exception:
        data = {"raw": text}
    payload = {**data, "http_status": resp.status_code}
    ok = resp.ok and bool(data.get("success", False))
    return ok, payload

def _send_toppily_by_package(phone: str, package_id: int, trx_ref: str):
    """
    Calls Toppily with {recipient_msisdn, package_id, trx_ref}.
    Returns (success: bool, payload: dict).
    """
    if not TOPPILY_API_KEY.strip():
        err = {"success": False, "message": "TOPPILY_API_KEY is not set.", "http_status": 500}
        print("[TOPPILY:CONFIG ERROR]", err)
        return False, err

    body = {
        "recipient_msisdn": phone,
        "package_id": int(package_id),
        "trx_ref": trx_ref,
    }

    # Try with SSL verification first
    try:
        verify_val = (certifi.where() if TOPPILY_VERIFY_SSL else False)
        ok, payload = _post_toppily(body, verify_val)
        print(f"[TOPPILY][verify={TOPPILY_VERIFY_SSL}] -> {payload}")
        return ok, payload

    except requests.exceptions.SSLError as e:
        err_text = f"SSL error: {e}"
        print("[TOPPILY:SSL ERROR primary]", err_text)

        # Optional insecure fallback (one retry) — use only to unblock while provider fixes TLS
        if ALLOW_INSECURE_TLS_FALLBACK:
            try:
                ok, payload = _post_toppily(body, False)  # verify=False
                payload.setdefault("note", "Insecure TLS fallback used (verify=False).")
                print("[TOPPILY:FALLBACK verify=False] ->", payload)
                return ok, payload
            except requests.RequestException as e2:
                err = {"success": False, "error": f"{err_text} / fallback failed: {e2}", "http_status": 597}
                return False, err
        else:
            err = {
                "success": False,
                "error": err_text,
                "hint": "TLS chain verification failed. Enable ALLOW_INSECURE_TLS_FALLBACK=True to retry with verify=False.",
                "http_status": 597,
            }
            return False, err

    except requests.RequestException as e:
        print("[TOPPILY:EXCEPTION]", e)
        return False, {"success": False, "error": str(e), "http_status": 599}

@checkout_bp.route("/checkout", methods=["POST"])
def process_checkout():
    try:
        # 🔐 Auth
        if "user_id" not in session or session.get("role") != "customer":
            print("[CHECKOUT] Auth failed. Session keys:", list(session.keys()))
            return jsonify({"success": False, "message": "Not authorized"}), 401

        # Validate session user id
        try:
            user_id = ObjectId(session["user_id"])
        except Exception:
            return jsonify({"success": False, "message": "Invalid user ID"}), 400

        data = request.get_json(silent=True) or {}
        cart = data.get("cart", [])
        method = data.get("method", "wallet")
        print("[CHECKOUT] Incoming payload:", data)

        # 🛒 Validate cart
        if not cart or not isinstance(cart, list):
            return jsonify({"success": False, "message": "Cart is empty or invalid"}), 400

        # 💰 Totals
        total_requested = sum(_money(item.get("amount")) for item in cart)
        if total_requested <= 0:
            return jsonify({"success": False, "message": "Total amount must be greater than zero"}), 400

        # 🧾 Order ref
        order_id = generate_order_id()

        # 🏦 Balance check
        bal_doc = balances_col.find_one({"user_id": user_id}) or {}
        current_balance = _money(bal_doc.get("amount", 0))
        print(f"[CHECKOUT] Balance={current_balance} TotalRequested={total_requested}")

        if current_balance < total_requested:
            return jsonify({"success": False, "message": "❌ Insufficient wallet balance"}), 400

        # 🚀 Call provider item-by-item
        results, total_success_amount = [], 0.0

        for idx, item in enumerate(cart, start=1):
            phone = (item.get("phone") or "").strip()
            value_obj = item.get("value_obj") or {}
            pkg_id = value_obj.get("id")
            amt = _money(item.get("amount"))

            if not phone or pkg_id in (None, "", []):
                results.append({
                    "phone": phone, "amount": amt, "value": item.get("value"),
                    "value_obj": value_obj, "api_status": "skipped",
                    "api_response": {"error": "Missing phone or package_id"}
                })
                continue

            try:
                pkg_id = int(pkg_id)
            except Exception:
                results.append({
                    "phone": phone, "amount": amt, "value": item.get("value"),
                    "value_obj": value_obj, "api_status": "skipped",
                    "api_response": {"error": f"package_id must be int, got {value_obj.get('id')!r}"} })
                continue

            trx_ref = f"{order_id}_{idx}_{uuid.uuid4().hex[:6]}"
            ok, payload = _send_toppily_by_package(phone, pkg_id, trx_ref)
            print(f"[CHECKOUT] Item {idx}: ok={ok} pkg_id={pkg_id} phone={phone} payload={payload}")

            results.append({
                "phone": phone, "amount": amt, "value": item.get("value"),
                "value_obj": value_obj, "serviceId": item.get("serviceId"),
                "serviceName": item.get("serviceName"), "trx_ref": trx_ref,
                "api_status": "success" if ok else "failed", "api_response": payload
            })
            if ok:
                total_success_amount += amt

        # 🧮 Nothing succeeded → no charge
        if total_success_amount <= 0:
            orders_col.insert_one({
                "user_id": user_id, "order_id": order_id, "items": results,
                "total_amount": total_requested, "charged_amount": 0.0,
                "status": "failed", "paid_from": method,
                "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
            })
            return jsonify({
                "success": False,
                "message": "No items were processed successfully. You were not charged.",
                "order_id": order_id,
                "details": results
            }), 502

        # 💳 Charge only successful total
        balances_col.update_one(
            {"user_id": user_id},
            {"$inc": {"amount": -total_success_amount}, "$set": {"updated_at": datetime.utcnow()}},
            upsert=True
        )

        # 🧾 Record order
        status = "completed" if total_success_amount == total_requested else "partial"
        orders_col.insert_one({
            "user_id": user_id, "order_id": order_id, "items": results,
            "total_amount": total_requested, "charged_amount": total_success_amount,
            "status": status, "paid_from": method,
            "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
        })

        # 🧾 Transaction record
        transactions_col.insert_one({
            "user_id": user_id, "amount": total_success_amount,
            "reference": order_id, "status": "success", "type": "purchase",
            "gateway": "Wallet", "currency": "GHS",
            "created_at": datetime.utcnow(), "verified_at": datetime.utcnow(),
            "meta": {"order_status": status}
        })

        msg = "✅ Order completed." if status == "completed" else \
              "⚠️ Order partially completed. You were charged only for successful items."
        return jsonify({
            "success": True, "message": f"{msg} Order ID: {order_id}",
            "order_id": order_id, "status": status,
            "charged_amount": round(total_success_amount, 2), "items": results
        }), 200

    except Exception:
        print("[CHECKOUT] Uncaught error:\n", traceback.format_exc())
        return jsonify({"success": False, "message": "Server error"}), 500
