"""
MomentumModel -- the first (and currently only) ScoringEngine implementation.

This is the validated momentum/relative-strength methodology carried over
from the AION reverse-engineering phase, used here as the *starting* model
for Version 1 -- not a permanent commitment. Per Phase 3 of the handoff,
it only gets replaced or supplemented when cited Decision Journal evidence
says so.

Methodology:
    1. Cross-sectionally z-score each input feature across the whole
       universe (so a feature's scale doesn't dominate the composite).
    2. Combine z-scores into a single weighted composite per ticker.
    3. GlobalScore = that composite's percentile rank (0-100) across the
       full universe.
    4. SectorScore = the same composite's percentile rank (0-100) computed
       *within* the ticker's own sector -- i.e. "best momentum relative to
       sector peers", which is what drove the validated Sector Score
       behavior during the AION reverse-engineering work.

NOTE ON WEIGHTS: the weights below are a reasonable starting point
consistent with the project's momentum/relative-strength framing (heavier
weight on intermediate-term returns and relative strength, light
penalization of stretched RSI and high realized vol). If the AION
reverse-engineering report has a more precise validated weighting, swap
FEATURE_WEIGHTS for that -- nothing else in this file needs to change.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from features import FEATURE_COLUMNS
from scoring_engine import ScoringEngine

# weight, and whether higher-is-better (+1) or lower-is-better (-1)
FEATURE_WEIGHTS: dict[str, tuple[float, int]] = {
    "ret_12M": (0.20, 1),
    "ret_6M": (0.20, 1),
    "ret_3M": (0.15, 1),
    "ret_1M": (0.05, 1),
    "rel_ret_3M_vs_market": (0.15, 1),
    "rel_ret_3M_vs_sector": (0.10, 1),
    "trend_r2_63d": (0.05, 1),     # tight, consistent uptrend rewarded
    "dist_from_52w_high": (0.05, 1),
    "realized_vol_21d": (0.03, -1),  # penalize excess volatility, lightly
    "rsi_14": (0.02, 0),            # handled specially: penalize overbought extremes
}


def _zscore(s: pd.Series) -> pd.Series:
    mu, sd = s.mean(), s.std()
    if not sd or pd.isna(sd):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sd


def _rsi_penalty(rsi: pd.Series) -> pd.Series:
    """Distance from neutral (50), so both overbought and oversold extremes
    get flagged -- momentum wants 'strong but not exhausted'."""
    return -(rsi - 50).abs()


def _percentile_rank(s: pd.Series) -> pd.Series:
    """0-100 scale, higher = better, NaNs left as NaN."""
    return s.rank(pct=True, na_option="keep") * 100


class MomentumModel(ScoringEngine):
    version = "momentum_v1"

    def score(self, features: pd.DataFrame) -> pd.DataFrame:
        df = features.copy()
        missing = [c for c in FEATURE_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"features DataFrame missing expected columns: {missing}")

        composite = pd.Series(0.0, index=df.index)
        for feat, (weight, direction) in FEATURE_WEIGHTS.items():
            if feat == "rsi_14":
                z = _zscore(_rsi_penalty(df[feat]))
            else:
                z = _zscore(df[feat]) * direction
            composite = composite.add(weight * z.fillna(0.0))

        df["_composite"] = composite
        df["GlobalScore"] = _percentile_rank(df["_composite"])
        df["SectorScore"] = df.groupby("Sector")["_composite"].transform(_percentile_rank)
        df = df.drop(columns=["_composite"])
        df["ModelVersion"] = self.version
        return df


def aggregate_sector_breadth(scored: pd.DataFrame, threshold: float = 60.0) -> pd.DataFrame:
    """
    Sector-level rollup for the Sector Rankings view: cross-validated
    breadth aggregation -- the % of constituents in each sector scoring
    above `threshold`, plus the sector's average GlobalScore.

    This is the per-sector aggregate (used for ranking sectors against
    each other), distinct from each stock's own per-ticker SectorScore.
    """
    g = scored.groupby("Sector")
    out = g.agg(
        AvgGlobalScore=("GlobalScore", "mean"),
        MedianGlobalScore=("GlobalScore", "median"),
        NumConstituents=("Ticker", "count"),
    ).reset_index()
    breadth = g["GlobalScore"].apply(lambda s: (s > threshold).mean() * 100).rename("BreadthPct")
    out = out.merge(breadth.reset_index(), on="Sector")
    return out.sort_values("AvgGlobalScore", ascending=False).reset_index(drop=True)


if __name__ == "__main__":
    # Smoke test against the synthetic features used in features.py's own test.
    import features as feat_mod
    import config

    rng = pd.date_range("2025-01-01", periods=300, freq="B")
    np.random.seed(0)
    frames = []
    for sym in ["AAPL", "MSFT", "SPY", "XLK"]:
        price = 100 * np.exp(np.cumsum(np.random.normal(0.0003, 0.01, len(rng))))
        frames.append(pd.DataFrame({
            "Date": rng, "Ticker": sym, "Open": price, "High": price * 1.01,
            "Low": price * 0.99, "Close": price, "Volume": 1_000_000,
        }))
    synthetic = pd.concat(frames, ignore_index=True)
    config.SECTOR_TICKERS = {"Technology": ["AAPL", "MSFT"]}

    feats = feat_mod.build_features(synthetic)
    model = MomentumModel()
    scored = model.score(feats)
    print(scored[["Ticker", "Sector", "GlobalScore", "SectorScore", "ModelVersion"]])
    print(aggregate_sector_breadth(scored))
