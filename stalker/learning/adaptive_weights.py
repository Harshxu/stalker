# -*- coding: utf-8 -*-
"""
STALKER v2 — Adaptive Factor Weight Engine
============================================
Dynamically adjusts factor weights based on rolling Information Coefficient (IC).

Instead of static weights like:
    ENSEMBLE_WEIGHTS = {"momentum": 0.14, "quality": 0.28, ...}

This engine computes Spearman rank correlation between each factor's score
and the actual forward returns, then reweights factors proportional to their
ACTUAL predictive power.

Features:
- Rolling IC computation (30-trade window)
- Bayesian smoothing (avoids whipsawing on noisy IC estimates)
- Exponential decay for stale factors
- Automatic recovery when a suppressed factor starts working again
- Regime-aware base weights (still uses regime as a prior)
- Persists state to MongoDB + JSON fallback

Design Principle:
    A factor with IC = -0.25 (Fundamental Score) should get near-zero weight,
    while a factor with IC = +0.35 (Relative Strength) should get boosted.
"""

import os
import sys
import json
import logging
import numpy as np
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Add parent directory to path for imports
_parent = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _parent not in sys.path:
    sys.path.insert(0, _parent)


# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

# Factor names as stored in daily_picks → picks[].xxx_score
FACTOR_COLUMNS = {
    "rs_rank":              "Relative Strength",
    "structure_score":      "Structure",
    "technical_score":      "Technical",
    "institutional_score":  "Institutional",
    "fundamental_score":    "Fundamental",
    "earnings_score":       "Earnings",
    "sector_rank":          "Sector",
    "opportunity_score":    "Opportunity",
    "liquidity_score":      "Liquidity",
}

# Mapping factor columns to the 4 ensemble sub-models
# Each factor maps to one of: momentum, quality, institutional, catalyst
FACTOR_TO_SUBMODEL = {
    "rs_rank":              "momentum",
    "structure_score":      "momentum",
    "technical_score":      "momentum",
    "institutional_score":  "institutional",
    "fundamental_score":    "quality",
    "earnings_score":       "catalyst",
    "sector_rank":          "momentum",
    "opportunity_score":    "catalyst",
    "liquidity_score":      "quality",
}

# IC thresholds for weight adjustment
IC_STRONG_POSITIVE = 0.10   # Factor is actively predictive
IC_WEAK_ZONE = 0.05         # Between -0.05 and +0.05 = noise
IC_DEGRADED = -0.05         # Factor is anti-predictive → suppress

# Minimum weight floor (never fully zero — allows recovery)
MIN_WEIGHT_FLOOR = 0.03

# Smoothing parameter for exponential moving average of IC
IC_EMA_ALPHA = 0.3  # Higher = more reactive to recent IC changes

# Minimum number of resolved trades before adaptive weights activate
MIN_TRADES_FOR_ADAPTIVE = 20

# Rolling window for IC computation
IC_ROLLING_WINDOW = 30


class AdaptiveFactorEngine:
    """
    Dynamically adjusts ensemble sub-model weights based on rolling IC.
    
    The engine maintains a smoothed IC estimate per factor, then converts
    these into sub-model weights (momentum, quality, institutional, catalyst)
    that replace the static ENSEMBLE_WEIGHTS in config.py.
    """

    def __init__(self):
        self._smoothed_ics: Dict[str, float] = {}
        self._raw_ics: Dict[str, float] = {}
        self._last_updated: Optional[str] = None
        self._trade_count: int = 0
        self._is_active: bool = False  # Only active after MIN_TRADES_FOR_ADAPTIVE
        self._adaptive_weights: Dict[str, float] = {}
        self._weight_history: List[Dict] = []

    # ─────────────────────────────────────────────
    # CORE: Rolling IC Computation
    # ─────────────────────────────────────────────

    def compute_rolling_ic(
        self,
        resolved_trades: List[Dict],
        window: int = IC_ROLLING_WINDOW,
    ) -> Dict[str, float]:
        """
        Computes Spearman rank correlation between each factor score
        and the forward return for the most recent `window` trades.

        Args:
            resolved_trades: List of pick dicts with factor scores + resolved returns.
                             Each dict should have keys like 'rs_rank', 'structure_score', etc.
                             and one of 'future_1d_return', 'intraday_return', etc.
            window: Number of most recent trades to use.

        Returns:
            Dict mapping factor_name → IC (Spearman correlation)
        """
        if len(resolved_trades) < 10:
            logger.warning(f"[ADAPTIVE] Only {len(resolved_trades)} resolved trades — need >= 10 for IC")
            return {}

        # Use most recent `window` trades
        trades = resolved_trades[-window:]
        self._trade_count = len(resolved_trades)

        # Extract forward returns (best available)
        returns = []
        for t in trades:
            ret = self._get_best_return(t)
            returns.append(ret)

        returns = np.array(returns, dtype=float)
        valid_mask = ~np.isnan(returns)

        if valid_mask.sum() < 10:
            logger.warning(f"[ADAPTIVE] Only {valid_mask.sum()} valid returns — need >= 10")
            return {}

        ics = {}
        for col in FACTOR_COLUMNS.keys():
            factor_values = []
            for t in trades:
                val = t.get(col)
                if val is not None:
                    try:
                        factor_values.append(float(val))
                    except (ValueError, TypeError):
                        factor_values.append(np.nan)
                else:
                    factor_values.append(np.nan)

            factor_arr = np.array(factor_values, dtype=float)
            # Mask where both factor and return are valid
            both_valid = valid_mask & ~np.isnan(factor_arr)

            if both_valid.sum() < 10:
                ics[col] = 0.0
                continue

            try:
                from scipy.stats import spearmanr
                corr, _ = spearmanr(factor_arr[both_valid], returns[both_valid])
                ics[col] = float(corr) if not np.isnan(corr) else 0.0
            except Exception:
                ics[col] = 0.0

        self._raw_ics = ics
        return ics

    def _get_best_return(self, pick: Dict) -> float:
        """Gets the best available forward return from a resolved trade."""
        for key in ("future_1d_return", "intraday_return", "future_5d_return", "future_3d_return"):
            val = pick.get(key)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    continue
        return np.nan

    # ─────────────────────────────────────────────
    # SMOOTHING: EMA-smoothed IC
    # ─────────────────────────────────────────────

    def update_smoothed_ics(self, raw_ics: Dict[str, float]) -> Dict[str, float]:
        """
        Applies exponential moving average smoothing to IC estimates.
        This prevents the system from whipsawing on noisy single-day IC swings.

        New_Smoothed_IC = α * Raw_IC + (1-α) * Previous_Smoothed_IC
        """
        for col, raw_ic in raw_ics.items():
            if col in self._smoothed_ics:
                self._smoothed_ics[col] = (
                    IC_EMA_ALPHA * raw_ic +
                    (1.0 - IC_EMA_ALPHA) * self._smoothed_ics[col]
                )
            else:
                # First observation — initialize
                self._smoothed_ics[col] = raw_ic

        return dict(self._smoothed_ics)

    # ─────────────────────────────────────────────
    # WEIGHT CONVERSION: IC → Sub-Model Weights
    # ─────────────────────────────────────────────

    def get_adaptive_weights(self, regime: str = "Neutral_Rotation") -> Dict[str, float]:
        """
        Converts smoothed ICs into ensemble sub-model weights.

        Strategy:
        1. Group factor ICs by their sub-model (momentum, quality, etc.)
        2. Average the ICs within each sub-model group
        3. Apply Bayesian adjustment:
           - IC > +0.10  → factor gets full weight contribution
           - IC in [-0.05, +0.10] → reduced weight (linear interpolation)
           - IC < -0.05  → near-zero weight (suppressed, floor = 0.03)
        4. Normalize weights to sum to 1.0
        5. Blend with regime prior (70% adaptive, 30% regime prior)

        Returns:
            {"momentum": w1, "quality": w2, "institutional": w3, "catalyst": w4}
        """
        if not self._is_active or not self._smoothed_ics:
            # Fall back to config.py static weights
            return self._get_regime_prior(regime)

        # Step 1: Group factor ICs by sub-model
        submodel_ics: Dict[str, List[float]] = {
            "momentum": [], "quality": [], "institutional": [], "catalyst": []
        }
        for col, ic in self._smoothed_ics.items():
            submodel = FACTOR_TO_SUBMODEL.get(col)
            if submodel:
                submodel_ics[submodel].append(ic)

        # Step 2: Average IC per sub-model
        submodel_avg_ic = {}
        for sm, ic_list in submodel_ics.items():
            submodel_avg_ic[sm] = float(np.mean(ic_list)) if ic_list else 0.0

        # Step 3: Convert IC to raw weight using Bayesian mapping
        raw_weights = {}
        for sm, avg_ic in submodel_avg_ic.items():
            raw_weights[sm] = self._ic_to_weight(avg_ic)

        # Step 4: Normalize to sum to 1.0
        total = sum(raw_weights.values())
        if total > 0:
            adaptive_weights = {sm: w / total for sm, w in raw_weights.items()}
        else:
            adaptive_weights = {"momentum": 0.25, "quality": 0.25,
                                "institutional": 0.25, "catalyst": 0.25}

        # Step 5: Blend with regime prior (70% adaptive, 30% regime prior)
        prior = self._get_regime_prior(regime)
        blended = {}
        for sm in ["momentum", "quality", "institutional", "catalyst"]:
            blended[sm] = round(
                0.70 * adaptive_weights.get(sm, 0.25) +
                0.30 * prior.get(sm, 0.25),
                4
            )

        # Final normalization
        total = sum(blended.values())
        if total > 0:
            blended = {sm: round(w / total, 4) for sm, w in blended.items()}

        self._adaptive_weights = blended

        logger.info(
            f"[ADAPTIVE] Weights: momentum={blended['momentum']:.3f}, "
            f"quality={blended['quality']:.3f}, institutional={blended['institutional']:.3f}, "
            f"catalyst={blended['catalyst']:.3f} | "
            f"SubModel ICs: {', '.join(f'{k}={v:+.3f}' for k, v in submodel_avg_ic.items())}"
        )

        return blended

    def _ic_to_weight(self, ic: float) -> float:
        """
        Maps IC value to a raw weight using a Bayesian-inspired curve:
        - IC >= +0.10 → weight = 1.0 (full contribution)
        - IC in [-0.05, +0.10] → linear interpolation from MIN_FLOOR to 1.0
        - IC <= -0.05 → weight = MIN_FLOOR (suppressed but not dead)
        """
        if ic >= IC_STRONG_POSITIVE:
            return 1.0
        elif ic <= IC_DEGRADED:
            return MIN_WEIGHT_FLOOR
        else:
            # Linear interpolation between IC_DEGRADED and IC_STRONG_POSITIVE
            range_size = IC_STRONG_POSITIVE - IC_DEGRADED
            position = (ic - IC_DEGRADED) / range_size
            return MIN_WEIGHT_FLOOR + position * (1.0 - MIN_WEIGHT_FLOOR)

    def _get_regime_prior(self, regime: str) -> Dict[str, float]:
        """Gets static regime weights from config.py as a Bayesian prior."""
        try:
            import config
            return config.ENSEMBLE_WEIGHTS.get(regime, config.ENSEMBLE_WEIGHTS.get("Neutral_Rotation", {
                "momentum": 0.25, "quality": 0.25, "institutional": 0.25, "catalyst": 0.25
            }))
        except Exception:
            return {"momentum": 0.25, "quality": 0.25, "institutional": 0.25, "catalyst": 0.25}

    # ─────────────────────────────────────────────
    # EOD UPDATE: Called at end of each trading day
    # ─────────────────────────────────────────────

    def update_weights_eod(self) -> Dict[str, float]:
        """
        Full EOD pipeline:
        1. Load all resolved trades from DB
        2. Compute rolling IC
        3. Update smoothed ICs
        4. Recompute adaptive weights
        5. Save state to DB
        
        Returns:
            Updated adaptive weights dict
        """
        logger.info("[ADAPTIVE] Starting EOD weight update...")

        # Step 1: Load resolved trades
        resolved = self._load_resolved_trades()
        if not resolved:
            logger.warning("[ADAPTIVE] No resolved trades found — keeping current weights")
            return self._adaptive_weights or self._get_regime_prior("Neutral_Rotation")

        # Step 2: Compute rolling IC
        raw_ics = self.compute_rolling_ic(resolved)
        if not raw_ics:
            logger.warning("[ADAPTIVE] IC computation returned empty — keeping current weights")
            return self._adaptive_weights or self._get_regime_prior("Neutral_Rotation")

        # Step 3: Smooth
        smoothed = self.update_smoothed_ics(raw_ics)

        # Step 4: Activate if enough data
        if self._trade_count >= MIN_TRADES_FOR_ADAPTIVE:
            self._is_active = True
            logger.info(f"[ADAPTIVE] Engine ACTIVE with {self._trade_count} resolved trades")
        else:
            logger.info(
                f"[ADAPTIVE] Engine INACTIVE — {self._trade_count}/{MIN_TRADES_FOR_ADAPTIVE} "
                f"trades needed. Using static weights."
            )

        # Step 5: Compute weights (regime will be applied at scoring time)
        weights = self.get_adaptive_weights()

        # Step 6: Save state
        self._last_updated = datetime.now().isoformat()
        self._save_state()

        # Log factor-level IC details
        for col, ic in sorted(smoothed.items(), key=lambda x: x[1], reverse=True):
            status = "🟢 ACTIVE" if ic >= IC_STRONG_POSITIVE else "🟡 WEAK" if ic >= IC_DEGRADED else "🔴 SUPPRESSED"
            logger.info(f"[ADAPTIVE]   {FACTOR_COLUMNS.get(col, col):.<25s} IC={ic:+.3f}  {status}")

        return weights

    def _load_resolved_trades(self) -> List[Dict]:
        """Loads all resolved trades from DB/JSON with factor scores and returns."""
        try:
            import db_manager
            import config

            db = db_manager.get_db()
            records = []

            if db is not None:
                try:
                    col = db[config.MONGO_COLLECTION_PICKS]
                    # MEMORY OPTIMIZATION: Only fetch last 60 days of picks to avoid OOM on 512MB server
                    from datetime import date, timedelta
                    cutoff_date = (date.today() - timedelta(days=60)).isoformat()
                    records = list(col.find({"date": {"$gte": cutoff_date}}))
                    if not records:
                        records = list(col.find().sort([("_id", -1)]).limit(60))
                except Exception as e:
                    logger.error(f"[ADAPTIVE] Error loading picks from MongoDB: {e}")

            if not records:
                all_json = db_manager._read_json("daily_picks.json")
                records = all_json[-60:] if all_json else []

            # Flatten picks and filter for resolved
            flat = []
            for r in records:
                picks_list = r.get("picks", r.get("top_picks", []))
                for p in picks_list:
                    has_return = any(
                        p.get(k) is not None
                        for k in ("future_1d_return", "intraday_return",
                                  "future_5d_return", "future_3d_return")
                    )
                    if has_return:
                        p_copy = dict(p)
                        p_copy["date"] = r.get("date")
                        flat.append(p_copy)

            # Sort by date
            flat.sort(key=lambda x: x.get("date", ""))
            return flat

        except Exception as e:
            logger.error(f"[ADAPTIVE] Error loading resolved trades: {e}")
            return []

    # ─────────────────────────────────────────────
    # PERSISTENCE: Save/Load state
    # ─────────────────────────────────────────────

    def _save_state(self):
        """Saves adaptive engine state to MongoDB and local JSON."""
        state = {
            "smoothed_ics": self._smoothed_ics,
            "raw_ics": self._raw_ics,
            "adaptive_weights": self._adaptive_weights,
            "is_active": self._is_active,
            "trade_count": self._trade_count,
            "last_updated": self._last_updated,
            "saved_at": datetime.now().isoformat(),
        }

        # Save to local JSON
        try:
            import config
            state_path = os.path.join(config.DATA_DIR, "adaptive_weights_state.json")
            with open(state_path, "w") as f:
                json.dump(state, f, indent=2)
            logger.info("[ADAPTIVE] State saved to local JSON")
        except Exception as e:
            logger.error(f"[ADAPTIVE] Failed to save state to JSON: {e}")

        # Save to MongoDB
        try:
            import db_manager
            db = db_manager.get_db()
            if db is not None:
                col = db["adaptive_weights_state"]
                col.replace_one({"_id": "latest"}, {**state, "_id": "latest"}, upsert=True)
                logger.info("[ADAPTIVE] State saved to MongoDB")
        except Exception as e:
            logger.error(f"[ADAPTIVE] Failed to save state to MongoDB: {e}")

        # Save weight history entry
        history_entry = {
            "date": str(date.today()),
            "weights": self._adaptive_weights,
            "smoothed_ics": dict(self._smoothed_ics),
            "trade_count": self._trade_count,
        }
        self._weight_history.append(history_entry)

    def load_state(self) -> bool:
        """Loads persisted state from MongoDB or local JSON. Returns True if loaded."""
        # Try MongoDB first
        try:
            import db_manager
            db = db_manager.get_db()
            if db is not None:
                col = db["adaptive_weights_state"]
                state = col.find_one({"_id": "latest"})
                if state:
                    return self._apply_state(state)
        except Exception:
            pass

        # Fallback to JSON
        try:
            import config
            state_path = os.path.join(config.DATA_DIR, "adaptive_weights_state.json")
            if os.path.exists(state_path):
                with open(state_path, "r") as f:
                    state = json.load(f)
                return self._apply_state(state)
        except Exception as e:
            logger.error(f"[ADAPTIVE] Failed to load state: {e}")

        return False

    def _apply_state(self, state: Dict) -> bool:
        """Applies loaded state dict to engine."""
        try:
            self._smoothed_ics = state.get("smoothed_ics", {})
            self._raw_ics = state.get("raw_ics", {})
            self._adaptive_weights = state.get("adaptive_weights", {})
            self._is_active = state.get("is_active", False)
            self._trade_count = state.get("trade_count", 0)
            self._last_updated = state.get("last_updated")
            logger.info(
                f"[ADAPTIVE] State loaded — active={self._is_active}, "
                f"trade_count={self._trade_count}, last_updated={self._last_updated}"
            )
            return True
        except Exception as e:
            logger.error(f"[ADAPTIVE] Failed to apply state: {e}")
            return False

    # ─────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────

    def get_weights_for_regime(self, regime: str) -> Dict[str, float]:
        """
        Public API: Returns the best available weights for the given regime.
        If adaptive engine is active, returns IC-adjusted weights.
        Otherwise returns static config.py weights for the regime.
        """
        if self._is_active and self._smoothed_ics:
            return self.get_adaptive_weights(regime)
        return self._get_regime_prior(regime)

    def get_factor_status_report(self) -> List[Dict]:
        """Returns a summary of each factor's IC and status for reporting."""
        report = []
        for col, display_name in FACTOR_COLUMNS.items():
            ic = self._smoothed_ics.get(col, 0.0)
            raw_ic = self._raw_ics.get(col, 0.0)
            if ic >= IC_STRONG_POSITIVE:
                status = "ACTIVE"
            elif ic >= IC_DEGRADED:
                status = "WEAK"
            else:
                status = "SUPPRESSED"

            report.append({
                "factor": display_name,
                "column": col,
                "smoothed_ic": round(ic, 4),
                "raw_ic": round(raw_ic, 4),
                "status": status,
                "submodel": FACTOR_TO_SUBMODEL.get(col, "unknown"),
            })
        return sorted(report, key=lambda x: x["smoothed_ic"], reverse=True)

    @property
    def is_active(self) -> bool:
        return self._is_active


# ─────────────────────────────────────────────
# SINGLETON instance (module-level)
# ─────────────────────────────────────────────
_engine_instance: Optional[AdaptiveFactorEngine] = None


def get_engine() -> AdaptiveFactorEngine:
    """Returns the global singleton AdaptiveFactorEngine, loading state if needed."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = AdaptiveFactorEngine()
        _engine_instance.load_state()
    return _engine_instance
