from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from database import db_init, add_sale, get_all_sales, get_stats, get_distinct_locations
from datetime import date
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-in-env")

VALID_PLATFORMS = {"Amazon", "eBay"}


@app.route("/")
def index():
    sales = get_all_sales()
    stats = get_stats()
    return render_template("index.html", sales=sales, stats=stats)


@app.route("/log", methods=["GET", "POST"])
def log_sale():
    if request.method == "POST":
        sale_date = request.form.get("date", "").strip()
        raw_amount = request.form.get("amount", "").strip()
        location = request.form.get("location", "").strip().title()
        platform = request.form.get("platform", "").strip()
        notes = request.form.get("notes", "").strip()

        # Validate
        try:
            amount = float(raw_amount)
            if amount <= 0:
                raise ValueError
        except ValueError:
            flash("Amount must be a positive number.")
            return redirect(url_for("log_sale"))

        if not location:
            flash("Location is required.")
            return redirect(url_for("log_sale"))

        if platform not in VALID_PLATFORMS:
            flash("Platform must be Amazon or eBay.")
            return redirect(url_for("log_sale"))

        add_sale(sale_date, amount, location, platform, notes)
        return redirect(url_for("index"))

    today = date.today().isoformat()
    locations = get_distinct_locations()
    return render_template("log_sale.html", today=today, locations=locations)


@app.route("/api/sales")
def api_sales():
    return jsonify(get_all_sales())


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


if __name__ == "__main__":
    db_init()
    port = int(os.getenv("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
