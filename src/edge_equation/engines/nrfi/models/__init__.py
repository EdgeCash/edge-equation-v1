"""Modeling stack for the elite NRFI/YRFI engine.

Modules
-------
poisson_baseline : Closed-form Poisson conversion + GLM-style baseline.
model_training   : XGBoost/LightGBM training pipelines (binary + Poisson).
calibration      : Isotonic / Platt scaling on a holdout slice.
inference        : Daily inference orchestrator + SHAP top-N drivers.
"""
