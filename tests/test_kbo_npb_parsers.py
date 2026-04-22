"""
Phase 21: real _parse() methods for the KBO and NPB scrapers.

The scrapers target mykbostats.com (KBO) and npb.jp English BIS (NPB).
We don't hit the network here -- we feed hand-crafted fixture HTML that
mirrors the page structure we parse against, and assert the parser maps
rows to the expected dict shape. Malformed / empty HTML must degrade
to an empty list (never raise).
"""
import pytest

from edge_equation.data_fetcher import (
    KboStatsScraper,
    NpbStatsScraper,
    _parse_table_rows,
)


# ---------------------------------------------- KBO

KBO_SAMPLE = """
<html><body>
<table class="schedule">
  <tr class="game">
    <td class="time">18:30</td>
    <td class="away">LG @ Doosan</td>
    <td class="starters">Kelly / Raley</td>
  </tr>
  <tr class="game">
    <td class="time">14:00</td>
    <td class="away">Kia vs KT</td>
    <td class="starters">Won Tae-in / Kim Min</td>
  </tr>
  <tr class="header"><td>skip me</td></tr>
</table>
</body></html>
"""


def test_kbo_parse_extracts_two_games_with_teams_and_starters():
    games = KboStatsScraper._parse(KBO_SAMPLE, day_iso="2026-04-20")
    assert len(games) == 2
    g0 = games[0]
    assert g0["league"] == "KBO"
    assert g0["home_team"] == "Doosan"
    assert g0["away_team"] == "LG"
    assert g0["home_starter"] == "Raley"
    assert g0["away_starter"] == "Kelly"
    assert g0["start_time_local"] == "18:30"
    assert g0["game_id"] == "KBO-2026-04-20-LG-Doosan"


def test_kbo_parse_handles_vs_separator():
    games = KboStatsScraper._parse(KBO_SAMPLE, day_iso="2026-04-20")
    g1 = games[1]
    assert g1["home_team"] == "KT"
    assert g1["away_team"] == "Kia"
    assert g1["home_starter"] == "Kim Min"
    assert g1["away_starter"] == "Won Tae-in"


def test_kbo_parse_empty_and_malformed_returns_list():
    assert KboStatsScraper._parse(None) == []
    assert KboStatsScraper._parse("") == []
    assert KboStatsScraper._parse("<html>no tables here</html>",
                                  day_iso="2026-04-20") == []


def test_kbo_parse_row_without_starters_still_yields_game():
    html = """
    <table class="schedule">
      <tr class="game"><td>17:00</td><td>SSG @ NC</td></tr>
    </table>
    """
    games = KboStatsScraper._parse(html, day_iso="2026-04-21")
    assert len(games) == 1
    assert games[0]["home_starter"] == ""
    assert games[0]["away_starter"] == ""


# ---------------------------------------------- NPB

NPB_SAMPLE = """
<html><body>
<table class="schedule_table">
  <tr><td>18:00</td><td>Tigers</td><td>-</td><td>Giants</td><td>Aoyagi vs Suga</td></tr>
  <tr><td>17:45</td><td>Carp</td><td>-</td><td>Swallows</td><td></td></tr>
</table>
</body></html>
"""


def test_npb_parse_extracts_rows_with_correct_home_away_mapping():
    games = NpbStatsScraper._parse(NPB_SAMPLE, day_iso="2026-04-20")
    assert len(games) == 2
    g0 = games[0]
    assert g0["league"] == "NPB"
    assert g0["home_team"] == "Giants"
    assert g0["away_team"] == "Tigers"
    assert g0["home_starter"] == "Suga"
    assert g0["away_starter"] == "Aoyagi"
    assert g0["start_time_local"] == "18:00"
    assert g0["game_id"] == "NPB-2026-04-20-Tigers-Giants"


def test_npb_parse_blank_starters_become_empty_strings():
    games = NpbStatsScraper._parse(NPB_SAMPLE, day_iso="2026-04-20")
    g1 = games[1]
    assert g1["home_starter"] == ""
    assert g1["away_starter"] == ""


def test_npb_parse_empty_and_malformed():
    assert NpbStatsScraper._parse(None) == []
    assert NpbStatsScraper._parse("") == []
    assert NpbStatsScraper._parse("<html><body><p>no tables</p></body></html>",
                                  day_iso="2026-04-20") == []


def test_npb_parse_skips_rows_with_too_few_cells():
    html = """
    <table class="schedule_table">
      <tr><td>18:00</td><td>Tigers</td></tr>
    </table>
    """
    assert NpbStatsScraper._parse(html, day_iso="2026-04-20") == []


# ---------------------------------------------- _parse_table_rows stdlib shim

def test_parse_table_rows_without_class_filter():
    html = """
    <table>
      <tr><td>a</td><td>b</td></tr>
      <tr><td>c</td><td>d</td></tr>
    </table>
    """
    rows = _parse_table_rows(html)
    assert rows == [["a", "b"], ["c", "d"]]


def test_parse_table_rows_tolerates_broken_markup():
    # The robustness contract is "never raise". The parser may drop malformed
    # rows silently; what matters is that the public scrapers don't propagate
    # the failure upward.
    html = "<table><tr><td>x<td>y</tr><tr><td>z</table>"
    rows = _parse_table_rows(html)
    assert isinstance(rows, list)
