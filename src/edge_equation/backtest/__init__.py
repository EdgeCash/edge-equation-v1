"""
Deterministic backtesting scaffold.

Four sub-modules:
- walk_forward: expanding / rolling fold scheduler
- bankroll:     sequential-bet simulator with ROI / max drawdown / z-score
- calibration:  Brier score + Murphy decomposition + reliability bins + log-loss
- grading:      quantile-based A/B/C/D/F threshold calibrator
"""
