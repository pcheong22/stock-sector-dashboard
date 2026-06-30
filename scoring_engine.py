"""
ScoringEngine: the one interface the dashboard is allowed to depend on.

    ScoringEngine
        ↓
    MomentumModel   <-- only implementation today

Future models (Bayesian, options-based, macro) become additional
implementations of this same interface, added only when Phase 3's
evidence rule justifies them. Do not add a registry, factory, or
comparison framework here -- that's Phase 4.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class ScoringEngine(ABC):
    """Contract: take a feature DataFrame (one row per ticker), return scores."""

    #: Bump this string whenever scoring logic changes. Stored on every row
    #: so later analysis can separate "the model changed" from "the world
    #: changed" -- per the historical storage schema in the handoff doc.
    version: str = "unversioned"

    @abstractmethod
    def score(self, features: pd.DataFrame) -> pd.DataFrame:
        """
        Args:
            features: DataFrame with columns including Ticker, Sector, and
                      the engineered feature columns (see features.FEATURE_COLUMNS).

        Returns:
            The same DataFrame with two additional columns appended:
                - GlobalScore: cross-sectional rank/score across the whole universe
                - SectorScore: rank/score within the ticker's own sector
        """
        raise NotImplementedError
