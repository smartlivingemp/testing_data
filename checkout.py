# checkout.py — Toppily shared-bundle flow only (network_id + shared_bundle)
from flask import Blueprint, request, jsonify, session
from bson import ObjectId
from datetime import datetime
import os, uuid, random, requests, certifi, traceback, json, hashlib

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from db import db

checkout_bp = Blueprint("checkout", __name__)

# MongoDB Collections
balances_col = db["balances"]
orders_col = db["orders"]
transactions_col = db["transactions"]
services_col = db["services"]

# ===== Toppily config =====
TOPPILY_URL = "https://toppily.com/api/v1/buy-other-package"
TOPPILY_API_KEY = os.getenv("TOPPILY_API_KEY", "").strip()  # <- set this in your env

# CF fallback (temporary). Prefer OFF once provider gives an API host/whitelist.
USE_CLOUDSCRAPER_FALLBACK = True

# Fallback map (used only if DB lookup fails)
NETWORK_ID_FALLBACK = {
    "MTN": 3,        # <- adjust to your real IDs
    "VODAFONE": 2,
    "AIRTELTIGO": 1,
}

# ===== HTTP session (certifi CA, retries, UA) =====
def _make_session() -> requests.Session:
    s = requests.Session()
    s.verify = certifi.where()  # trust store from certifi
    s.headers.update({
        "User-Agent": "NanDataApp/1.0 (+server)",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    retries = Retry(
        total=3,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods={"GET", "POST"},
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s

_http = _make_session()

# ===== Tiny JSON logger =====
def jlog(event: str, **kv):
    rec = {"evt": event, **kv}
    try:
        print(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        print(f"[LOG_FALLBACK] {event} {kv}")

# ===== Helpers =====
def generate_order_id():
    return f"NAN{random.randint(10000, 99999)}"

def _money(v):
    try:
        return float(v)
    except Exception:
        return 0.0

def _is_cloudflare_block(text: str, headers: dict, status: int) -> bool:
    if status not in (403, 503):
        return False
    # quick fingerprints
    snippet = (text or "")[:600]
    if ("Just a moment" in snippet) or ("challenge-platform" in snippet) or ("__cf_chl_" in snippet):
        return True
    try:
        # header check (case-insensitive)
        h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        if "cf-mitigated" in h or "server" in h and "cloudflare" in h["server"].lower():
            return True
    except Exception:
        pass
    return False

def _resp_debug(resp: requests.Response, body_text: str):
    redacted_headers = {}
    for k, v in resp.headers.items():
        lk = k.lower()
        redacted_headers[k] = "***" if lk in ("authorization", "cookie", "set-cookie", "x-api-key") else v
    return {
        "status": resp.status_code,
        "headers": redacted_headers,
        "body_len": len(body_text or ""),
        "body_sha256_16": hashlib.sha256((body_text or "").encode("utf-8", "ignore")).hexdigest()[:16],
        "body_snippet": (body_text or "")[:140].replace("\n", " "),
    }

def _post_requests(body):
    if not TOPPILY_API_KEY:
        raise RuntimeError("TOPPILY_API_KEY not set")
    headers = {"x-api-key": TOPPILY_API_KEY}
    resp = _http.post(TOPPILY_URL, headers=headers, json=body, timeout=30)
    text = resp.text or ""
    try:
        data = resp.json()
    except Exception:
        data = {"raw": text}
    ok = resp.ok and bool(data.get("success"))
    return ok, data, resp, text

def _post_cloudscraper(body):
    try:
        import cloudscraper
    except Exception as e:
        return False, {"success": False, "error": f"cloudscraper not installed: {e}"}, None, ""
    headers = {"x-api-key": TOPPILY_API_KEY, "Accept": "application/json", "Content-Type": "application/json"}
    scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
    resp = scraper.post(TOPPILY_URL, headers=headers, json=body, timeout=60)
    text = resp.text or ""
    try:
        data = resp.json()
    except Exception:
        data = {"raw": text}
    ok = resp.ok and bool(data.get("success"))
    return ok, data, resp, text

# ===== Resolve fields for shared-bundle =====
def _resolve_network_id(item: dict, value_obj: dict):
    # 1) from payload
    nid = (item or {}).get("network_id") or (value_obj or {}).get("network_id")
    if nid not in (None, "", []):
        try:
            return int(nid)
        except Exception:
            pass

    # 2) from services collection via serviceId
    svc_id = (item or {}).get("serviceId")
    if svc_id:
        try:
            svc_doc = services_col.find_one({"_id": ObjectId(svc_id)}, {"network_id": 1, "name": 1, "network": 1})
            if svc_doc and "network_id" in svc_doc and svc_doc["network_id"] not in (None, ""):
                return int(svc_doc["network_id"])
            guess = (svc_doc.get("name") or svc_doc.get("network") or "").strip().upper() if svc_doc else ""
            if guess and guess in NETWORK_ID_FALLBACK:
                return int(NETWORK_ID_FALLBACK[guess])
        except Exception:
            pass

    # 3) from serviceName
    name = (item.get("serviceName") or "").strip().upper()
    if name in NETWORK_ID_FALLBACK:
        return int(NETWORK_ID_FALLBACK[name])

    return None

def _resolve_shared_bundle(item: dict, value_obj: dict):
    vol = (value_obj or {}).get("volume")
    if vol not in (None, "", []):
        try:
            return int(vol)
        except Exception:
            pass
    sb = (item or {}).get("shared_bundle")
    if sb not in (None, "", []):
        try:
            return int(sb)
        except Exception:
            pass
    return None

# ===== Call Toppily with shared-bundle body =====
def _send_toppily_shared_bundle(phone: str, network_id: int, shared_bundle: int, trx_ref: str,
                                order_id: str, debug_events: list):
    if not TOPPILY_API_KEY:
        err = {"success": False, "message": "API key not set", "http_status": 500}
        jlog("toppily_config_error", order_id=order_id, trx_ref=trx_ref)
        return False, err

    # Log the body we will send (phone partially masked)
    masked = phone[:3] + "***" + phone[-2:] if phone else ""
    body = {"recipient_msisdn": phone, "network_id": int(network_id), "shared_bundle": int(shared_bundle), "trx_ref": trx_ref}
    jlog("toppily_request_body", order_id=order_id, trx_ref=trx_ref,
         body={"recipient_msisdn": masked, "network_id": body["network_id"], "shared_bundle": body["shared_bundle"], "trx_ref": trx_ref})

    try:
        ok, data, resp, text = _post_requests(body)
        dbg = _resp_debug(resp, text)
        blocked = _is_cloudflare_block(text, resp.headers, resp.status_code)
        payload = {**data, "http_status": resp.status_code}
        if blocked:
            payload.setdefault("error", "Cloudflare challenge blocked the request")
            payload["blocked_by_cloudflare"] = True
        jlog("toppily_call", order_id=order_id, trx_ref=trx_ref, ok=ok,
             status=resp.status_code, blocked_by_cloudflare=blocked, debug=dbg)
        debug_events.append({"when": datetime.utcnow(), "stage": "primary",
                             "ok": ok, "blocked_by_cloudflare": blocked, "debug": dbg})

        if blocked and USE_CLOUDSCRAPER_FALLBACK:
            ok2, data2, resp2, text2 = _post_cloudscraper(body)
            if resp2 is not None:
                dbg2 = _resp_debug(resp2, text2)
                blocked2 = _is_cloudflare_block(text2, resp2.headers, resp2.status_code)
                payload2 = {**data2, "http_status": resp2.status_code, "note": "cloudscraper fallback used"}
                if blocked2:
                    payload2.setdefault("error", "Cloudflare challenge blocked the request (cloudscraper)")
                    payload2["blocked_by_cloudflare"] = True
                jlog("toppily_cloudscraper", order_id=order_id, trx_ref=trx_ref, ok=ok2,
                     status=resp2.status_code, blocked_by_cloudflare=blocked2, debug=dbg2)
                debug_events.append({"when": datetime.utcnow(), "stage": "cloudscraper",
                                     "ok": ok2, "blocked_by_cloudflare": blocked2, "debug": dbg2})
                return (ok2 and not blocked2), payload2

        return (ok and not blocked), payload

    except requests.RequestException as e:
        jlog("toppily_network_error", order_id=order_id, trx_ref=trx_ref, error=str(e))
        return False, {"success": False, "error": str(e), "http_status": 599}

# ===== Route =====
@checkout_bp.route("/checkout", methods=["POST"])
def process_checkout():
    try:
        # Auth
        if "user_id" not in session or session.get("role") != "customer":
            jlog("checkout_auth_fail", session_keys=list(session.keys()))
            return jsonify({"success": False, "message": "Not authorized"}), 401

        try:
            user_id = ObjectId(session["user_id"])
        except Exception:
            return jsonify({"success": False, "message": "Invalid user ID"}), 400

        data = request.get_json(silent=True) or {}
        cart = data.get("cart", [])
        method = data.get("method", "wallet")
        jlog("checkout_incoming", payload=data)

        if not cart or not isinstance(cart, list):
            return jsonify({"success": False, "message": "Cart is empty or invalid"}), 400

        total_requested = sum(_money(item.get("amount")) for item in cart)
        if total_requested <= 0:
            return jsonify({"success": False, "message": "Total amount must be greater than zero"}), 400

        order_id = generate_order_id()

        bal_doc = balances_col.find_one({"user_id": user_id}) or {}
        current_balance = _money(bal_doc.get("amount", 0))
        jlog("checkout_balance", order_id=order_id, balance=current_balance, total=total_requested)
        if current_balance < total_requested:
            return jsonify({"success": False, "message": "❌ Insufficient wallet balance"}), 400

        results, total_success_amount, debug_events = [], 0.0, []

        for idx, item in enumerate(cart, start=1):
            phone = (item.get("phone") or "").strip()
            value_obj = item.get("value_obj") or {}   # expects {"volume": 1000, ...}
            amt = _money(item.get("amount"))

            network_id = _resolve_network_id(item, value_obj)
            shared_bundle = _resolve_shared_bundle(item, value_obj)

            if not phone or network_id is None or shared_bundle is None:
                results.append({
                    "phone": phone, "amount": amt, "value": item.get("value"),
                    "value_obj": value_obj, "api_status": "skipped",
                    "api_response": {
                        "error": "Missing phone, network_id, or shared_bundle (volume).",
                        "got": {"phone": bool(phone), "network_id": network_id, "shared_bundle": shared_bundle}
                    }
                })
                continue

            trx_ref = f"{order_id}_{idx}_{uuid.uuid4().hex[:6]}"
            ok, payload = _send_toppily_shared_bundle(phone, network_id, shared_bundle, trx_ref, order_id, debug_events)

            results.append({
                "phone": phone, "amount": amt, "value": item.get("value"),
                "value_obj": value_obj, "serviceId": item.get("serviceId"),
                "serviceName": item.get("serviceName"), "trx_ref": trx_ref,
                "api_status": "success" if ok else "failed", "api_response": payload
            })
            if ok:
                total_success_amount += amt

        if len(debug_events) > 10:
            debug_events = debug_events[-10:]

        if total_success_amount <= 0:
            orders_col.insert_one({
                "user_id": user_id, "order_id": order_id, "items": results,
                "total_amount": total_requested, "charged_amount": 0.0,
                "status": "failed", "paid_from": method,
                "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
                "debug": {"events": debug_events}
            })
            blocked_any = any(x.get("api_response", {}).get("blocked_by_cloudflare") for x in results)
            return jsonify({
                "success": False,
                "message": "No items were processed successfully. You were not charged.",
                "order_id": order_id,
                "blocked_by_cloudflare": bool(blocked_any),
                "details": results
            }), 502

        balances_col.update_one(
            {"user_id": user_id},
            {"$inc": {"amount": -total_success_amount}, "$set": {"updated_at": datetime.utcnow()}},
            upsert=True
        )

        status = "completed" if total_success_amount == total_requested else "partial"
        orders_col.insert_one({
            "user_id": user_id, "order_id": order_id, "items": results,
            "total_amount": total_requested, "charged_amount": total_success_amount,
            "status": status, "paid_from": method,
            "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
            "debug": {"events": debug_events}
        })

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
        jlog("checkout_uncaught", error=traceback.format_exc())
        return jsonify({"success": False, "message": "Server error"}), 500
