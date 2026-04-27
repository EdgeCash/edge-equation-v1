"""Tests for the NRFI/YRFI daily email report.

Pure-Python — no xgboost / fastapi dependency. Verifies card
construction, plain-text body rendering, dry-run mode, and recipient
precedence.
"""

from __future__ import annotations

from decimal import Decimal


# ---------------------------------------------------------------------------
# render_body — plain-text formatting
# ---------------------------------------------------------------------------

def _make_card_with_picks(n_nrfi=2, n_yrfi=1):
    picks = []
    for i in range(n_nrfi):
        picks.append({
            "game_id": f"MLB-2026-04-28-NYY-BOS-{i}",
            "market_type": "NRFI",
            "selection": "NRFI",
            "pct": 78.4 - i,
            "fair_prob": "0.7840",
            "color_band": "Deep Green",
            "color_hex": "#1b5e20",
            "signal": "STRONG_NRFI",
            "lambda_total": 0.49,
            "mc_band_pp": 5.2,
            "edge": "+6.1pp",
            "kelly": "1.20u",
            "grade": "A",
            "drivers": ["+14 home_p_xera", "+8 park_factor_runs"],
            "rendered": (f"NRFI 78.4% [Deep Green]  λ=0.49  MC ±5.2pp  "
                          f"edge +6.1pp  stake 1.20u\n  drivers: +14 home_p_xera"),
        })
    for i in range(n_yrfi):
        picks.append({
            "game_id": f"MLB-2026-04-28-COL-LAD-{i}",
            "market_type": "YRFI",
            "selection": "YRFI",
            "pct": 71.0,
            "fair_prob": "0.7100",
            "color_band": "Deep Green",
            "color_hex": "#1b5e20",
            "signal": "STRONG_NRFI",
            "lambda_total": 2.40,
            "mc_band_pp": 4.8,
            "edge": "+4.7pp",
            "kelly": "0.94u",
            "grade": "B",
            "drivers": ["+9 wx_temperature_f", "+6 park_factor_hr"],
            "rendered": (f"YRFI 71.0% [Deep Green]  λ=2.40  MC ±4.8pp  "
                          f"edge +4.7pp  stake 0.94u"),
        })
    return {
        "card_type": "nrfi-daily",
        "generated_at": "2026-04-28T14:00:00+00:00",
        "headline": "NRFI/YRFI Daily — 2026-04-28",
        "subhead": "Facts. Not Feelings.",
        "tagline": "Engine: ml.  Internal testing — not financial advice.",
        "engine": "ml",
        "target_date": "2026-04-28",
        "picks": picks,
    }


def test_render_body_groups_nrfi_then_yrfi():
    from nrfi.email_report import render_body
    card = _make_card_with_picks(n_nrfi=3, n_yrfi=2)
    body = render_body(card)
    assert "NRFI BOARD (3 games)" in body
    assert "YRFI BOARD (2 games)" in body
    # NRFI section appears before YRFI section.
    assert body.index("NRFI BOARD") < body.index("YRFI BOARD")
    # Footer present.
    assert "Internal testing" in body


def test_render_body_handles_empty_slate():
    from nrfi.email_report import render_body
    card = _make_card_with_picks(n_nrfi=0, n_yrfi=0)
    body = render_body(card)
    assert "No games on the slate" in body
    # Header still present.
    assert "Facts. Not Feelings." in body


def test_build_subject_uses_target_date():
    from nrfi.email_report import build_subject
    card = _make_card_with_picks()
    subject = build_subject(card)
    assert "NRFI/YRFI Daily" in subject
    assert "2026-04-28" in subject


# ---------------------------------------------------------------------------
# send_email dry-run mode
# ---------------------------------------------------------------------------

def test_send_email_dry_run_does_not_call_smtp(capsys):
    from nrfi.email_report import send_email
    card = _make_card_with_picks()
    result = send_email(card, recipient="test@example.com", dry_run=True)
    captured = capsys.readouterr()
    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["target"] == "test@example.com"
    assert "DRY RUN" in captured.out
    assert "NRFI/YRFI Daily" in captured.out


def test_send_email_dry_run_prefers_explicit_recipient_over_env(monkeypatch, capsys):
    from nrfi.email_report import send_email
    monkeypatch.setenv("EMAIL_TO", "fallback@example.com")
    card = _make_card_with_picks()
    result = send_email(card, recipient="explicit@example.com", dry_run=True)
    assert result["target"] == "explicit@example.com"


def test_send_email_dry_run_falls_back_to_env(monkeypatch, capsys):
    from nrfi.email_report import send_email
    monkeypatch.setenv("EMAIL_TO", "env-recipient@example.com")
    monkeypatch.delenv("SMTP_TO", raising=False)
    card = _make_card_with_picks()
    result = send_email(card, recipient=None, dry_run=True)
    assert result["target"] == "env-recipient@example.com"


def test_send_email_dry_run_default_to_professor(monkeypatch, capsys):
    from nrfi.email_report import send_email
    monkeypatch.delenv("EMAIL_TO", raising=False)
    monkeypatch.delenv("SMTP_TO", raising=False)
    card = _make_card_with_picks()
    result = send_email(card, recipient=None, dry_run=True)
    assert result["target"] == "ProfessorEdgeCash@gmail.com"


# ---------------------------------------------------------------------------
# build_card — exercise via injected fake bridge so we don't need xgboost
# ---------------------------------------------------------------------------

class _FakeBridgeOutput:
    def __init__(self, gid, mt, pct):
        self.game_id = gid
        self.market_type = mt
        self.fair_prob = Decimal(str(pct / 100.0))
        self.lambda_total = 0.86
        self.color_band = "Light Green"
        self.color_hex = "#7cb342"
        self.signal = "LEAN_NRFI"
        self.grade = "B"
        self.realization = 60
        self.shap_drivers = [("home_p_xera", -0.04), ("park_factor_runs", -0.02)]
        self.mc_low = 0.58
        self.mc_high = 0.66
        self.market_prob = None
        self.edge = None
        self.kelly = None
        self.metadata = {"engine": "fake"}


class _FakeBridge:
    @staticmethod
    def available():
        return True

    def predict_for_features(self, feature_dicts, *, game_ids,
                              market_probs=None, american_odds=None,
                              pitcher_bf_each=None):
        out = []
        for gid in game_ids:
            out.append(_FakeBridgeOutput(gid, "NRFI", 62.0))
            out.append(_FakeBridgeOutput(gid, "YRFI", 38.0))
        return out


def test_build_card_uses_bridge_and_builds_picks(monkeypatch):
    """Inject a fake bridge + stub feature-reconstruction so we can
    test the card build path without DuckDB / pybaseball / network."""
    import nrfi.email_report as mod

    monkeypatch.setattr(mod, "daily_etl", lambda *a, **kw: None)
    monkeypatch.setattr(
        mod, "reconstruct_features_for_date",
        lambda *a, **kw: [(101, {"poisson_p_nrfi": 0.55, "lambda_total": 1.20}),
                          (102, {"poisson_p_nrfi": 0.62, "lambda_total": 0.96})],
    )

    class _FakeNRFIStore:
        def __init__(self, *a, **kw): pass

    monkeypatch.setattr(mod, "NRFIStore", _FakeNRFIStore)
    monkeypatch.setattr(mod.NRFIEngineBridge, "try_load",
                          classmethod(lambda cls, cfg=None: _FakeBridge()))

    card = mod.build_card("2026-04-28", run_etl=False)
    assert card["card_type"] == "nrfi-daily"
    assert card["target_date"] == "2026-04-28"
    assert card["engine"] == "ml"
    assert len(card["picks"]) == 4    # 2 games × (NRFI + YRFI)
    nrfi = [p for p in card["picks"] if p["market_type"] == "NRFI"]
    yrfi = [p for p in card["picks"] if p["market_type"] == "YRFI"]
    assert len(nrfi) == 2
    assert len(yrfi) == 2
    # Sorted: NRFI first, then YRFI; within each, descending pct.
    assert card["picks"][0]["market_type"] == "NRFI"
    assert card["picks"][2]["market_type"] == "YRFI"


def test_build_card_handles_no_games(monkeypatch):
    import nrfi.email_report as mod

    monkeypatch.setattr(mod, "daily_etl", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "reconstruct_features_for_date",
                          lambda *a, **kw: [])

    class _FakeNRFIStore:
        def __init__(self, *a, **kw): pass

    monkeypatch.setattr(mod, "NRFIStore", _FakeNRFIStore)
    monkeypatch.setattr(mod.NRFIEngineBridge, "try_load",
                          classmethod(lambda cls, cfg=None: _FakeBridge()))

    card = mod.build_card("2026-04-28", run_etl=False)
    assert card["picks"] == []
    body = mod.render_body(card)
    assert "No games on the slate" in body
