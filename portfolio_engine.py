"""
STALKER — Portfolio Engine
Dedicated module for portfolio construction rules.
Extracted from screener.py to decouple portfolio logic from alpha logic.

Responsibilities:
  1. Sector concentration cap (max 2 per sector)
  2. Industry concentration cap (max 1 per industry)
  3. Portfolio heat cap vs PORTFOLIO_MAX_RISK_PCT
  4. Pairwise Pearson correlation gating (avg < 0.60)
  5. Single-stock correlation gating (< 0.80 vs each existing pick)

Usage:
    pe = PortfolioEngine()
    for stock in candidates:
        accept, reason = pe.accepts(stock, df_hist)
        if accept:
            pe.add(stock, df_hist)
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple

import config

logger = logging.getLogger(__name__)


class PortfolioEngine:
    """Stateful portfolio construction engine for a single screening session."""

    def __init__(self, risk_per_trade_pct: float = None, heat_limit_pct: float = None):
        self.portfolio: List[Dict] = []
        self.dfs: List[pd.DataFrame] = []
        self.sector_counts: Dict[str, int] = {}
        self.industry_counts: Dict[str, int] = {}

        self.risk_per_trade_pct = risk_per_trade_pct or (
            getattr(config, "RISK_PER_TRADE_PCT", 0.02) * 100.0
        )
        self.heat_limit_pct = heat_limit_pct or (
            getattr(config, "PORTFOLIO_MAX_RISK_PCT", 0.06) * 100.0
        )
        self.active_heat: float = 0.0

    # ── Correlation Helpers ──────────────────────────────────────────

    @staticmethod
    def _pairwise_corr(dfs: List[pd.DataFrame]) -> float:
        """Average absolute pairwise return correlation across a list of DataFrames."""
        if len(dfs) < 2:
            return 0.0
        try:
            returns_list = [df["Close"].pct_change().rename(f"s{i}") for i, df in enumerate(dfs)]
            aligned = pd.concat(returns_list, axis=1, join="inner").dropna()
            if len(aligned) < 10:
                return 0.0
            corr_matrix = aligned.corr()
            upper = corr_matrix.where(
                np.triu(np.ones(corr_matrix.shape), k=1).astype(bool)
            )
            vals = upper.stack().values
            return float(np.mean(np.abs(vals))) if len(vals) > 0 else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def _single_corr(df1: pd.DataFrame, df2: pd.DataFrame) -> float:
        """Absolute 60-day return correlation between two stocks."""
        try:
            r1 = df1["Close"].pct_change()
            r2 = df2["Close"].pct_change()
            aligned = pd.concat([r1, r2], axis=1, join="inner").dropna()
            if len(aligned) < 20:
                return 0.0
            corr = float(aligned.iloc[:, 0].tail(60).corr(aligned.iloc[:, 1].tail(60)))
            return abs(corr) if not np.isnan(corr) else 0.0
        except Exception:
            return 0.0

    # ── Main Gating Method ──────────────────────────────────────────

    def accepts(
        self,
        stock: Dict,
        df_hist: pd.DataFrame,
        action: str = "BUY",
    ) -> Tuple[bool, str]:
        """
        Evaluates whether the candidate stock should be added to the portfolio.

        Args:
            stock: Candidate stock dict (must have 'sector', 'fund' with 'industry')
            df_hist: OHLCV DataFrame for correlation checks
            action: 'BUY' or 'WATCH' (heat check only applies to BUY)

        Returns:
            (accept: bool, reject_reason: str)
        """
        symbol = stock.get("symbol", "?")
        sector = stock.get("sector", "Unknown")
        industry = (stock.get("fund") or {}).get("industry", "Unknown")

        # 1. Sector cap
        if self.sector_counts.get(sector, 0) >= 2:
            return False, f"Sector cap: already 2 picks in {sector}"

        # 2. Industry cap
        if self.industry_counts.get(industry, 0) >= 1:
            return False, f"Industry cap: already 1 pick in {industry}"

        # 3. Heat cap (only for BUY)
        if action == "BUY":
            projected_heat = self.active_heat + self.risk_per_trade_pct
            if projected_heat > self.heat_limit_pct:
                return (
                    False,
                    f"Heat cap: {projected_heat:.1f}% would exceed {self.heat_limit_pct:.1f}% limit",
                )

        # 4. Avg pairwise correlation (if adding this stock)
        if df_hist is not None and len(self.dfs) >= 1:
            temp_dfs = self.dfs + [df_hist]
            avg_corr = self._pairwise_corr(temp_dfs)
            if avg_corr > 0.60:
                return False, f"Correlation: avg pairwise r={avg_corr:.2f} > 0.60"

        # 5. Single-stock correlation vs each existing position
        if df_hist is not None:
            for existing_df in self.dfs:
                r = self._single_corr(df_hist, existing_df)
                if r > 0.80:
                    return False, f"Correlation: r={r:.2f} > 0.80 vs an existing position"

        return True, ""

    def add(
        self,
        stock: Dict,
        df_hist: pd.DataFrame,
        action: str = "BUY",
    ) -> None:
        """Adds a stock to the portfolio and updates internal state."""
        sector = stock.get("sector", "Unknown")
        industry = (stock.get("fund") or {}).get("industry", "Unknown")

        self.portfolio.append(stock)
        if df_hist is not None:
            self.dfs.append(df_hist)
        self.sector_counts[sector] = self.sector_counts.get(sector, 0) + 1
        self.industry_counts[industry] = self.industry_counts.get(industry, 0) + 1
        if action == "BUY":
            self.active_heat += self.risk_per_trade_pct

    def size(self) -> int:
        return len(self.portfolio)

    def get_portfolio(self) -> List[Dict]:
        return self.portfolio
