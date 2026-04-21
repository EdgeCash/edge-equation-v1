"""
Context multipliers.

Each context source (rest, travel, weather, officials, situational, injuries)
exposes a frozen Context dataclass and an Adjuster class whose adjustment()
staticmethod returns a ContextAdjustment (home_adv_delta + totals_delta).

ContextRegistry composes a ContextBundle into a single summed ContextAdjustment
that downstream feature builders can merge into the math-layer inputs.
"""
