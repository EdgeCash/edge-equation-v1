"""Phase 20 compliance hardening: expanded FORBIDDEN_TERMS + ledger footer
whitelist so 'Bet within your means' survives."""
import pytest

from edge_equation.compliance import (
    DISCLAIMER_TEXT,
    FORBIDDEN_TERMS,
    LEDGER_FOOTER_RE,
    compliance_test,
)


def _with_footer(text: str, footer: str = None) -> str:
    footer = footer or (
        "Season Ledger: 68-49-3 +8.45 units +7.0 ROI | "
        "Bet within your means. Problem? Call 1-800-GAMBLER."
    )
    return f"{text}\n\n{DISCLAIMER_TEXT}\n{footer}"


# ------------------------------------------------ forbidden-term expansion


def test_new_phase20_forbidden_terms_present():
    for term in ("smash", "smashes", "lotto", "lottos",
                 "play", "plays", "sharp", "signal", "signals", "value"):
        assert term in FORBIDDEN_TERMS


def test_compliance_flags_smashes():
    text = _with_footer("Yankees smashes vs. lefty tonight.")
    report = compliance_test(text, require_ledger_footer=True)
    assert report.ok is False
    assert any("smash" in v.lower() for v in report.violations)


def test_compliance_flags_sharp():
    text = _with_footer("Sharp move on the total to 9.5.")
    report = compliance_test(text, require_ledger_footer=True)
    assert report.ok is False


def test_compliance_flags_value():
    text = _with_footer("Good value on Judge over 0.5 HR.")
    report = compliance_test(text, require_ledger_footer=True)
    assert report.ok is False


def test_compliance_flags_signals():
    text = _with_footer("Late-night signals on KBO totals.")
    report = compliance_test(text, require_ledger_footer=True)
    assert report.ok is False


# ----------------------------------------------- ledger-footer whitelist


def test_ledger_footer_regex_matches_spec_text():
    text = "Season Ledger: 68-49-3 +8.45 units +7.0 ROI | Bet within your means. Problem? Call 1-800-GAMBLER."
    assert LEDGER_FOOTER_RE.search(text) is not None


def test_ledger_footer_bet_word_does_not_trip_compliance():
    # The only occurrence of "bet" is inside the whitelisted footer.
    body = "Judge over 0.5 (+280).\nGrade A+."
    text = _with_footer(body)
    report = compliance_test(text, require_ledger_footer=True)
    assert report.ok is True, report.violations


def test_ledger_footer_required_when_flag_set():
    body = "Judge over 0.5 (+280).\nGrade A+."
    text = f"{body}\n\n{DISCLAIMER_TEXT}"   # missing footer
    report = compliance_test(text, require_ledger_footer=True)
    assert report.ok is False
    assert any("season ledger footer" in v.lower() for v in report.violations)


def test_ledger_footer_optional_by_default():
    body = "Judge over 0.5 (+280).\nGrade A+."
    text = f"{body}\n\n{DISCLAIMER_TEXT}"
    report = compliance_test(text)
    assert report.ok is True


def test_bet_outside_footer_still_trips():
    # A "bet" reference OUTSIDE the whitelisted footer must still fail.
    body = "This is a strong bet on Judge."
    text = _with_footer(body)
    report = compliance_test(text, require_ledger_footer=True)
    assert report.ok is False
    assert any("forbidden term: 'bet'" in v.lower() for v in report.violations)


def test_gambler_phone_does_not_trip_gamble_term():
    # "Call 1-800-GAMBLER." is part of the whitelisted footer.
    body = "Judge over 0.5 (+280).\nGrade A+."
    text = _with_footer(body)
    report = compliance_test(text, require_ledger_footer=True)
    assert report.ok is True, report.violations


def test_signed_and_negative_units_roi_parse():
    footer = (
        "Season Ledger: 12-15-1 -3.20 units -1.8 ROI | "
        "Bet within your means. Problem? Call 1-800-GAMBLER."
    )
    # Body avoids the tighter Phase 20 forbidden terms (no "play").
    text = f"Analytical summary.\n\n{DISCLAIMER_TEXT}\n{footer}"
    report = compliance_test(text, require_ledger_footer=True)
    assert report.ok is True, report.violations


def test_classic_forbidden_terms_still_flagged():
    # Ensure the Phase 18 list wasn't lost in the Phase 20 expansion.
    for term in ("pick", "lock", "sportsbook", "parlay", "wager", "gamble"):
        text = _with_footer(f"Tonight's {term} of the night.")
        report = compliance_test(text, require_ledger_footer=True)
        assert report.ok is False, f"expected {term!r} to be forbidden"
