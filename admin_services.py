from flask import Blueprint, render_template, session, redirect, url_for, request, flash, jsonify, Request
from flask import current_app
from db import db
from datetime import datetime
from bson import ObjectId
from werkzeug.utils import secure_filename
import os
import json  # ✅ for parsing JSON from offers_value[]

admin_services_bp = Blueprint("admin_services", __name__)
services_col = db["services"]

# ---- File upload config ----
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}
UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploads")  # match app.py

def _ensure_upload_folder():
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# ---- Auth helper ----
def _require_admin():
    return session.get("role") == "admin"

# ---- Helpers ----
def _to_float(s):
    try:
        return float(s)
    except Exception:
        return None

def _to_int(s):
    try:
        return int(s)
    except Exception:
        return None

def _format_volume(vol_mb):
    """
    Format volume in MB → 'XGB' or 'YMB'.
    1000MB -> 1GB, 1500MB -> 1.5GB, 500MB -> 500MB.
    """
    if vol_mb is None:
        return "-"
    try:
        vol_mb = float(vol_mb)
    except Exception:
        return "-"
    if vol_mb >= 1000:
        gb = vol_mb / 1000.0
        if abs(gb - round(gb)) < 1e-9:
            return f"{int(round(gb))}GB"
        return f"{gb:.2f}GB"
    return f"{int(vol_mb)}MB"

def _value_from_text(value_text):
    """
    Accept either:
      - Plain text (e.g. '1GB', 'MTN Mashup')  -> return string
      - JSON string like '{"id":10,"volume":10000}' -> return dict {'id':10,'volume':10000}
    If JSON parse fails, return original string.
    """
    if not value_text:
        return ""
    vt = value_text.strip()
    # Quick check for likely JSON dict
    if vt.startswith("{") and vt.endswith("}"):
        try:
            data = json.loads(vt)
            # normalize id/volume types if present
            if isinstance(data, dict):
                if "id" in data:
                    data["id"] = _to_int(data["id"])
                if "volume" in data:
                    data["volume"] = _to_int(data["volume"])
                return data
        except Exception:
            pass
    return vt

def _compute_value_text(value):
    """
    Build a friendly display string for templates:
      - If value is dict with 'volume' (MB) and optional 'id' -> '10GB (Pkg 10)'
      - Else -> the raw value (string)
    """
    if isinstance(value, dict):
        vol = value.get("volume")
        pkg_id = value.get("id")
        vol_str = _format_volume(vol)
        if pkg_id:
            return f"{vol_str} (Pkg {pkg_id})"
        return vol_str
    # fallback to plain string
    return value or "-"

# ---- Offers parser ----
def _parse_offers(req: Request):
    """
    Parse offers arrays from form into a normalized list of dicts.
    Now supports 'offers_value[]' as either plain text or JSON with {'id', 'volume'}.
    """
    amounts = req.form.getlist("offers_amount[]")
    values = req.form.getlist("offers_value[]")
    profits = req.form.getlist("offers_profit[]")

    offers = []
    n = max(len(amounts), len(values), len(profits))
    for i in range(n):
        amount = (amounts[i] if i < len(amounts) else "").strip()
        value_raw = (values[i] if i < len(values) else "").strip()
        profit = (profits[i] if i < len(profits) else "").strip()

        # Skip completely empty row
        if not amount and not value_raw and not profit:
            continue

        # Try to parse value_raw (JSON dict allowed)
        value = _value_from_text(value_raw)

        offers.append({
            "amount": _to_float(amount),
            "value": value,            # may be dict {'id','volume'} or string
            "profit": _to_float(profit)
        })

    return offers

# =======================
#      PAGE ROUTES
# =======================
@admin_services_bp.route("/admin/services", methods=["GET"])
def manage_services():
    if not _require_admin():
        return redirect(url_for("login.login"))

    services = list(services_col.find({}).sort([("_id", -1)]))

    # Attach helper fields for the template
    for s in services:
        s["_id_str"] = str(s["_id"])
        # Build a computed 'value_text' for each offer (so Jinja can show GB neatly)
        if isinstance(s.get("offers"), list):
            for of in s["offers"]:
                val = of.get("value")
                of["value_text"] = _compute_value_text(val)

    return render_template("admin_services.html", services=services)

@admin_services_bp.route("/admin/services/create", methods=["POST"])
def create_service():
    if not _require_admin():
        return redirect(url_for("login.login"))

    service_name = (request.form.get("service_name") or "").strip()
    image_url = (request.form.get("image_url") or "").strip()

    if not service_name:
        flash("Service name is required.", "danger")
        return redirect(url_for("admin_services.manage_services"))

    if not image_url:
        flash("Please upload/select an image for the service.", "danger")
        return redirect(url_for("admin_services.manage_services"))

    offers = _parse_offers(request)

    doc = {
        "name": service_name,
        "image_url": image_url,
        "offers": offers,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    services_col.insert_one(doc)
    flash("Service added successfully.", "success")
    return redirect(url_for("admin_services.manage_services"))

@admin_services_bp.route("/admin/services/<service_id>/update", methods=["POST"])
def update_service(service_id):
    if not _require_admin():
        return redirect(url_for("login.login"))

    try:
        _id = ObjectId(service_id)
    except Exception:
        flash("Invalid service id.", "danger")
        return redirect(url_for("admin_services.manage_services"))

    service = services_col.find_one({"_id": _id})
    if not service:
        flash("Service not found.", "danger")
        return redirect(url_for("admin_services.manage_services"))

    service_name = (request.form.get("service_name") or "").strip()
    image_url = (request.form.get("image_url") or "").strip()

    if not service_name:
        flash("Service name is required.", "danger")
        return redirect(url_for("admin_services.manage_services"))

    if not image_url:
        flash("Please upload/select an image for the service.", "danger")
        return redirect(url_for("admin_services.manage_services"))

    offers = _parse_offers(request)

    update_doc = {
        "name": service_name,
        "image_url": image_url,
        "offers": offers,
        "updated_at": datetime.utcnow()
    }

    services_col.update_one({"_id": _id}, {"$set": update_doc})
    flash("Service updated successfully.", "success")
    return redirect(url_for("admin_services.manage_services"))

@admin_services_bp.route("/admin/services/<service_id>/delete", methods=["POST"])
def delete_service(service_id):
    if not _require_admin():
        return redirect(url_for("login.login"))

    try:
        _id = ObjectId(service_id)
    except Exception:
        flash("Invalid service id.", "danger")
        return redirect(url_for("admin_services.manage_services"))

    svc = services_col.find_one({"_id": _id})
    res = services_col.delete_one({"_id": _id})

    if res.deleted_count:
        try:
            if svc and svc.get("image_url", "").startswith("/uploads/"):
                _ensure_upload_folder()
                fname = svc["image_url"].replace("/uploads/", "")
                fpath = os.path.join(UPLOAD_FOLDER, fname)
                if os.path.isfile(fpath):
                    os.remove(fpath)
        except Exception:
            pass

        flash("Service deleted.", "info")
    else:
        flash("Service not found or already deleted.", "warning")

    return redirect(url_for("admin_services.manage_services"))

# =======================
#   FILE UPLOAD API
# =======================
@admin_services_bp.route("/upload_service_image", methods=["POST"])
def upload_service_image():
    if not _require_admin():
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    if "image" not in request.files:
        return jsonify({"success": False, "error": "No file part 'image'"}), 400

    file = request.files["image"]
    if not file or file.filename.strip() == "":
        return jsonify({"success": False, "error": "No selected file"}), 400

    if not _allowed_file(file.filename):
        return jsonify({"success": False, "error": "Invalid file type"}), 400

    _ensure_upload_folder()

    filename = secure_filename(file.filename)
    target_path = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(target_path):
        name, ext = os.path.splitext(filename)
        filename = f"{name}_{int(datetime.utcnow().timestamp())}{ext}"
        target_path = os.path.join(UPLOAD_FOLDER, filename)

    file.save(target_path)
    file_url = f"/uploads/{filename}"
    return jsonify({"success": True, "url": file_url}), 200
