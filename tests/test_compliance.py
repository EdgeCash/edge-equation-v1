from decimal import Decimal
import pytest

from edge_equation.compliance import (
    ComplianceReport,
    DISCLAIMER_TEXT,
    FORBIDDEN_TERMS,
    PublicModeSanitizer,
    compliance_test,
    inject_disclaimer,
)
from edge_equation.compliance.disclaimer import inject_into_card


# ------------------------------------------------ Sanitizer


def _sample_pick():
    return {
        "sport": "MLB",
        "market_type": "ML",
        "selection": "BOS",
        "grade": "A",
        "fair_prob": "0.553",
        "edge": "0.045",
        "kelly": "0.0085",
        "kelly_breakdown": {"kelly_final": "0.0085"},
        "metadata": {"raw_universal_sum": "0.085", "note": "keep me"},
        "line": {"odds": -132, "number": None},
    }


def test_sanitize_pick_strips_edge_kelly():
    clean = PublicModeSanitizer.sanitize_pick(_sample_pick())
    assert "edge" not in clean
    assert "kelly" not in clean
    assert "kelly_breakdown" not in clean
    # Non-forbidden fields pass through
    assert clean["fair_prob"] == "0.553"
    assert clean["selection"] == "BOS"


def test_sanitize_pick_strips_raw_universal_sum_from_metadata():
    clean = PublicModeSanitizer.sanitize_pick(_sample_pick())
    assert "raw_universal_sum" not in clean["metadata"]
    assert clean["metadata"]["note"] == "keep me"


def test_sanitize_card_strips_picks_and_summary():
    card = {
        "card_type": "daily_edge",
        "picks": [_sample_pick(), _sample_pick()],
        "summary": {"grade": "A", "edge": "0.05", "kelly": "0.01"},
        "tagline": "x",
    }
    clean = PublicModeSanitizer.sanitize_card(card)
    assert all("edge" not in p for p in clean["picks"])
    assert "edge" not in clean["summary"]
    assert "kelly" not in clean["summary"]
    assert "grade" in clean["summary"]  # grade is still allowed


def test_sanitize_text_redacts_edge_percentage():
    text = "This pick has edge 4.92% and Half-Kelly 0.0085"
    clean = PublicModeSanitizer.sanitize_text(text)
    assert "[redacted]" in clean
    assert "Kelly" not in clean


def test_sanitize_text_redacts_edge_colon_number():
    text = "Edge: 0.045 on the model."
    clean = PublicModeSanitizer.sanitize_text(text)
    assert "0.045" not in clean


def test_sanitize_text_does_not_match_wedge():
    # 'wedge' should NOT be redacted just because it contains 'edge'.
    text = "the wedge design"
    clean = PublicModeSanitizer.sanitize_text(text)
    assert clean == "the wedge design"


def test_list_leaks_reports_hits():
    text = "edge 0.05 and Kelly sizing"
    leaks = PublicModeSanitizer.list_leaks(text)
    assert len(leaks) >= 2


# ------------------------------------------------ Disclaimer


def test_inject_disclaimer_appends_once():
    out = inject_disclaimer("Hello world")
    assert DISCLAIMER_TEXT in out
    # Idempotent
    again = inject_disclaimer(out)
    assert again.count(DISCLAIMER_TEXT) == 1


def test_inject_disclaimer_handles_none():
    out = inject_disclaimer(None)
    assert DISCLAIMER_TEXT in out


def test_inject_into_card_appends_to_tagline():
    card = {"tagline": "Facts. Not Feelings."}
    out = inject_into_card(card)
    assert DISCLAIMER_TEXT in out["tagline"]


def test_inject_into_card_idempotent():
    card = {"tagline": DISCLAIMER_TEXT}
    out = inject_into_card(card)
    assert out["tagline"] == DISCLAIMER_TEXT


# ------------------------------------------------ compliance_test


def test_compliance_ok_on_clean_payload():
    payload = {
        "headline": "Model Highlight",
        "picks": [{"sport": "MLB", "selection": "BOS"}],
        "tagline": DISCLAIMER_TEXT,
    }
    report = compliance_test(payload)
    assert report.ok is True
    assert report.violations == []


def test_compliance_fails_on_bet_language():
    payload = {
        "headline": "Bet of the day",
        "tagline": DISCLAIMER_TEXT,
    }
    report = compliance_test(payload)
    assert report.ok is False
    assert any("bet" in v.lower() for v in report.violations)


def test_compliance_fails_on_pick_language():
    payload = {
        "headline": "Top pick of the week",
        "tagline": DISCLAIMER_TEXT,
    }
    report = compliance_test(payload)
    assert report.ok is False


def test_compliance_fails_on_sportsbook_language():
    payload = {
        "body": "DraftKings sportsbook has this at -110",
        "tagline": DISCLAIMER_TEXT,
    }
    report = compliance_test(payload)
    assert report.ok is False


def test_compliance_fails_on_unsanitized_edge_leak():
    payload = {
        "body": "This has edge 0.045",
        "tagline": DISCLAIMER_TEXT,
    }
    report = compliance_test(payload)
    assert report.ok is False
    assert any("leak" in v.lower() for v in report.violations)


def test_compliance_fails_on_missing_disclaimer():
    payload = {"headline": "Daily Edge"}
    report = compliance_test(payload)
    assert report.ok is False
    assert any("disclaimer" in v.lower() for v in report.violations)


def test_compliance_skips_disclaimer_when_requested():
    payload = {"headline": "Daily Edge"}
    report = compliance_test(payload, require_disclaimer=False)
    assert report.ok is True


def test_compliance_accepts_text_string():
    text = f"The model projects a close matchup tonight.\n\n{DISCLAIMER_TEXT}"
    report = compliance_test(text)
    assert report.ok is True


def test_compliance_forbidden_terms_constant():
    assert "bet" in FORBIDDEN_TERMS
    assert "sportsbook" in FORBIDDEN_TERMS
    assert "pick" in FORBIDDEN_TERMS


def test_compliance_reject_on_unsupported_type():
    report = compliance_test(12345)
    assert report.ok is False
    assert any("unsupported" in v.lower() for v in report.violations)


def test_report_to_dict_shape():
    report = ComplianceReport(ok=False, violations=["missing disclaimer"])
    d = report.to_dict()
    assert d["ok"] is False
    assert d["violations"] == ["missing disclaimer"]


def test_compliance_multiple_violations_reported():
    payload = {
        "headline": "Lock of the day with edge 4.5%",
        # missing disclaimer, has "lock", has "edge 4.5%"
    }
    report = compliance_test(payload)
    assert report.ok is False
    assert len(report.violations) >= 2
