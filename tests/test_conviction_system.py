from edge_equation.engines.core.posting.conviction import (
    ELECTRIC_BLUE,
    RED,
    conviction_band,
    electric_indices,
    filter_electric_blue,
    format_conviction_line,
    render_conviction_key,
)


def test_conviction_band_electric_and_bad_edge_red():
    assert conviction_band(0.62, is_electric=True) == ELECTRIC_BLUE
    assert conviction_band(0.82, edge=-0.01) == RED


def test_electric_indices_and_filter():
    rows = [
        {"model_probability": 0.71, "edge": None, "conviction_color": "Electric Blue"},
        {"model_probability": 0.66, "edge": None, "conviction_color": "Deep Green"},
        {"model_probability": 0.54, "edge": None, "conviction_color": "Orange"},
    ]
    assert electric_indices(rows, top_n=1) == {0}
    assert len(filter_electric_blue(rows)) == 1


def test_conviction_line_and_key():
    line = format_conviction_line(
        label="BOS @ TOR NRFI",
        model_probability=0.71,
        band=ELECTRIC_BLUE,
        stake_units=1.5,
    )
    assert "71% Conviction" in line
    assert "Electric Blue" in line
    assert "1.5u" in line
    key = render_conviction_key()
    assert "Electric Blue" in key
    assert "Facts" not in key


def test_graphic_prompt_uses_conviction_language():
    from edge_equation.posting.ai_graphic_prompt import build_ai_graphic_prompt

    prompt = build_ai_graphic_prompt({
        "generated_at": "2026-04-29T12:00:00Z",
        "picks": [{
            "selection": "BOS @ TOR NRFI",
            "fair_prob": "0.71",
            "conviction_color": "Electric Blue",
            "line": {"odds": -110},
            "metadata": {},
        }],
    })

    assert "Electric Blue" in prompt
    assert "71% Conviction" in prompt
    assert "Conviction key footer" in prompt

