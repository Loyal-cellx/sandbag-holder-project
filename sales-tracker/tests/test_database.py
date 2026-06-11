"""Unit tests for the database layer."""


def test_get_stats_empty(db):
    stats = db.get_stats()
    assert stats["total_sales"] == 0
    assert stats["total_revenue"] == 0
    assert stats["total_profit"] == 0
    assert stats["by_week"] == []
    assert stats["avg_days_between_sales"] is None
    assert stats["days_since_last_sale"] is None


def test_get_stats_single_sale(db):
    db.add_sale("2026-06-01", 69.0, "Texas", "Amazon", "first")
    stats = db.get_stats()
    assert stats["total_sales"] == 1
    assert stats["total_revenue"] == 69.0
    # $49 profit on a $69 sell price.
    assert stats["total_profit"] == 49.0
    assert len(stats["by_week"]) >= 1


def test_get_stats_year_boundary_weeks(db):
    # The %Y-%W -> Monday round-trip is fragile across the Dec/Jan boundary.
    # These dates straddle the 2025/2026 week-numbering seam.
    db.add_sale("2025-12-29", 69.0, "Texas", "Amazon")
    db.add_sale("2026-01-05", 69.0, "Texas", "Amazon")
    stats = db.get_stats()  # must not raise on the strptime("%Y-%W-%w") round-trip
    assert stats["total_sales"] == 2
    # Weeks are reconstructed contiguously from the first Monday to this week.
    assert len(stats["by_week"]) >= 2


def test_profit_uses_module_margin(db):
    db.add_sale("2026-06-01", 100.0, "Texas", "Amazon")
    sales = db.get_all_sales()
    assert sales[0]["profit"] == round(100.0 * db.PROFIT_MARGIN, 2)


def test_milestones_progression(db):
    db.add_sale("2026-06-01", 500.0, "Texas", "Amazon")
    data = db.get_milestones()
    hit_slugs = {m["slug"] for m in data["milestones"] if m["hit"]}
    assert "first_sale" in hit_slugs
    assert "rev_500" in hit_slugs
    assert data["total_hit"] >= 2
    assert data["next_milestone"] is not None


def test_delete_sale_rowcount(db):
    db.add_sale("2026-06-01", 69.0, "Texas", "Amazon")
    sale_id = db.get_all_sales()[0]["id"]
    assert db.delete_sale(sale_id) == 1
    assert db.delete_sale(sale_id) == 0  # already gone


def test_update_sale_missing_id(db):
    assert db.update_sale(9999, amount=10.0) == 0


def test_update_sale_existing(db):
    db.add_sale("2026-06-01", 69.0, "Texas", "Amazon")
    sale_id = db.get_all_sales()[0]["id"]
    assert db.update_sale(sale_id, amount=138.0) == 1
    assert db.get_sale(sale_id)["amount"] == 138.0


def test_get_sale_profit_and_missing(db):
    db.add_sale("2026-06-01", 69.0, "Texas", "Amazon")
    sale_id = db.get_all_sales()[0]["id"]
    assert db.get_sale(sale_id)["profit"] == 49.0
    assert db.get_sale(9999) is None
