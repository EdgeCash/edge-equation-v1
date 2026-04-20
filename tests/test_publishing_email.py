from edge_equation.publishing.email_publisher import EmailPublisher


def _card():
    return {
        "card_type": "daily_edge",
        "headline": "Daily Edge",
        "subhead": "Today's plays.",
        "picks": [
            {"market_type": "ML", "selection": "BOS", "grade": "A", "edge": "0.049167",
             "kelly": "0.0324", "fair_prob": "0.618133"},
        ],
        "tagline": "Facts. Not Feelings.",
        "generated_at": "2026-04-20T09:00:00",
    }


def test_email_dry_run_success():
    pub = EmailPublisher()
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True
    assert result.target == "email"
    assert result.message_id == "dry-run"


def test_email_non_dry_run_returns_fake_id():
    pub = EmailPublisher()
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is True
    assert result.target == "email"
    assert result.message_id.startswith("email-")


def test_email_subject_includes_card_type_and_date():
    pub = EmailPublisher()
    subject = pub.build_subject(_card())
    assert "Edge Equation" in subject
    assert "daily_edge" in subject
    assert "2026-04-20" in subject


def test_email_body_contains_headline_and_tagline():
    pub = EmailPublisher()
    body = pub.build_body(_card())
    assert "Daily Edge" in body
    assert "Facts. Not Feelings." in body
    # Must reference the pick details
    assert "BOS" in body
    assert "Grade: A" in body


def test_email_custom_from_address():
    pub = EmailPublisher(from_address="custom@example.com")
    assert pub.from_address == "custom@example.com"
