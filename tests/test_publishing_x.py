from edge_equation.publishing.x_publisher import XPublisher, MAX_LEN


def _card(headline="Daily Edge", n_picks=2):
    return {
        "card_type": "daily_edge",
        "headline": headline,
        "subhead": "Today's model-graded plays.",
        "picks": [
            {"market_type": "ML", "selection": "BOS", "grade": "A", "edge": "0.049167"},
            {"market_type": "Total", "selection": "Over 9.5", "grade": "C", "edge": None},
        ][:n_picks],
        "tagline": "Facts. Not Feelings.",
    }


def test_x_publisher_dry_run():
    pub = XPublisher()
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True
    assert result.target == "x"
    assert result.message_id == "dry-run"
    assert result.error is None


def test_x_publisher_non_dry_run():
    pub = XPublisher()
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is True
    assert result.target == "x"
    assert result.message_id is not None
    assert result.message_id.startswith("x-")
    assert len(result.message_id) > len("x-")


def test_x_publisher_truncates_long_text():
    long_headline = "H" * 500
    pub = XPublisher()
    # Build text via the internal formatter and confirm truncation
    text = pub._format_text({"headline": long_headline, "picks": [], "tagline": ""})
    assert len(text) > MAX_LEN, "precondition: test setup must produce over-length text"
    truncated = pub._truncate(text, MAX_LEN)
    assert len(truncated) <= MAX_LEN
    assert truncated.endswith("…")


def test_x_publisher_short_text_not_truncated():
    pub = XPublisher()
    text = pub._format_text(_card())
    truncated = pub._truncate(text, MAX_LEN)
    assert truncated == text
    assert "…" not in truncated


def test_x_publisher_accepts_credentials():
    pub = XPublisher(api_key="k", api_secret="s")
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True
