from flask import Flask, render_template, send_from_directory
import os

from customer_dashboard import customer_dashboard_bp
from admin_dashboard import admin_dashboard_bp
from login import login_bp
from signup import signup_bp
from admin_customers import admin_customers_bp
from admin_services import admin_services_bp  # Includes upload route
from deposit import deposit_bp
from checkout import checkout_bp
from orders import orders_bp
from transactions import transactions_bp
from customer_profile import customer_profile_bp
from complaints import complaints_bp
from referral import referral_bp
from admin_orders import admin_orders_bp
from admin_transactions import admin_transactions_bp
from admin_complaints import admin_complaints_bp
from admin_referrals import admin_referrals_bp
from admin_balance import admin_balance_bp
from admin_wassce_checker import admin_wassce_checker_bp
from purchases import purchases_bp
from purchase_checker import purchase_checker_bp
from admin_purchases import admin_purchases_bp
from settings import settings_bp


# === Configuration ===
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')

def create_app():
    app = Flask(__name__)
    app.secret_key = "your-secret-key"  # 🔐 Move this to an environment variable in production

    # Ensure the upload folder exists
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)

    # Register Blueprints
    app.register_blueprint(customer_dashboard_bp)
    app.register_blueprint(admin_dashboard_bp)
    app.register_blueprint(login_bp)
    app.register_blueprint(signup_bp)
    app.register_blueprint(admin_customers_bp)
    app.register_blueprint(admin_services_bp)
    app.register_blueprint(deposit_bp)
    app.register_blueprint(checkout_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(customer_profile_bp)
    app.register_blueprint(complaints_bp)
    app.register_blueprint(referral_bp)
    app.register_blueprint(admin_orders_bp)
    app.register_blueprint(admin_transactions_bp)
    app.register_blueprint(admin_complaints_bp)
    app.register_blueprint(admin_referrals_bp)
    app.register_blueprint(admin_balance_bp)
    app.register_blueprint(admin_wassce_checker_bp)
    app.register_blueprint(purchase_checker_bp)
    app.register_blueprint(purchases_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(admin_purchases_bp)



    # Home route
    @app.route("/")
    def home():
        return render_template("index.html")

    # Serve uploaded files
    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        return send_from_directory(UPLOAD_FOLDER, filename)

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
