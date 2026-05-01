from edge_equation.engines.full_game.output import (
    FullGameOutput,
    render_full_game_output,
)


def test_full_game_output_uses_conviction_language():
    rendered = render_full_game_output(
        FullGameOutput(
            label="LAA @ CWS Over 5.5",
            model_probability=0.89,
            stake_units=1.5,
        )
    )
    assert "89% Conviction" in rendered
    assert "Deep Green" in rendered
    assert "1.5u" in rendered
