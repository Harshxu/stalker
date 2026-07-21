# -*- coding: utf-8 -*-
"""
STALKER v2 — Strategy Tracker
================================
Per-setup rolling performance tracker with auto-disable capability.

Each setup type (BREAKOUT, MOMENTUM, PULLBACK, EARNINGS_RUNNER, etc.)
maintains its own rolling statistics:
  - Win count / Loss count
  - Rolling expectancy (average return per trade)
  - Rolling drawdown (worst losing streak)
  - Confidence level (0-100)
  - Active/Disabled status

Auto-Disable Rules:
  - Expectancy < 0 for N consecutive evaluations → disabled
  - Win rate < 30% over last 20 trades → disabled
  - Max drawdown > 8% for the setup → disabled

Recovery:
  - A disabled strategy is re-evaluated weekly
  - If paper-traded expectancy turns positive for 5+ trades → re-enabled
"""

import os
import sys
import json
import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)

# Add parent directory to path for imports
_parent = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _parent not in sys.path:
    sys.path.insert(0, _parent)


# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

# Known setup types in the system
KNOWN_SETUPS = [
    "BREAKOUT", "MOMENTUM", "PULLBACK", "EARNINGS_RUNNER",
    "QUALITY_TREND", "VALUE_MOMENTUM", "INSTITUTIONAL_BREAKOUT",
    "VWAP_RECLAIM", "WATCHLIST_ONLY"
]

# Auto-disable thresholds
DISABLE_WIN_RATE_THRESHOLD = 0.30      # Win rate below 30% → disable
DISABLE_EXPECTANCY_THRESHOLD = -0.20   # Avg return below -0.20% → disable
DISABLE_CONSECUTIVE_EVAL = 3           # Consecutive negative evaluations before disable
DISABLE_MAX_DRAWDOWN = 0.08            # 8% max drawdown for setup → disable
MIN_TRADES_FOR_EVALUATION = 5          # Need at least 5 resolved trades

# Recovery thresholds
RECOVERY_MIN_TRADES = 5                # Need 5 positive paper trades to recover
RECOVERY_EVAL_DAYS = 7                 # Re-evaluate disabled setups weekly

# Confidence scoring
CONFIDENCE_BASE = 50.0                 # Start at 50/100
CONFIDENCE_PER_WIN = 2.0               # +2 per winning trade
CONFIDENCE_PER_LOSS = -3.0             # -3 per losing trade (asymmetric — losses hurt more)
CONFIDENCE_MIN = 0.0
CONFIDENCE_MAX = 100.0


class SetupStats:
    """Rolling statistics for a single setup type."""

    def __init__(self, setup_type: str):
        self.setup_type = setup_type
        self.wins: int = 0
        self.losses: int = 0
        self.total_trades: int = 0
        self.returns: List[float] = []
        self.recent_returns: List[float] = []  # Last 20 trades
        self.confidence: float = CONFIDENCE_BASE
        self.is_disabled: bool = False
        self.disabled_at: Optional[str] = None
        self.disabled_reason: Optional[str] = None
        self.consecutive_negative_evals: int = 0
        self.max_drawdown: float = 0.0
        self.last_evaluated: Optional[str] = None

    @property
    def win_rate(self) -> float:
        return self.wins / self.total_trades if self.total_trades > 0 else 0.0

    @property
    def expectancy(self) -> float:
        if not self.recent_returns:
            return 0.0
        return float(np.mean(self.recent_returns))

    @property
    def avg_winner(self) -> float:
        winners = [r for r in self.recent_returns if r > 0]
        return float(np.mean(winners)) if winners else 0.0

    @property
    def avg_loser(self) -> float:
        losers = [r for r in self.recent_returns if r <= 0]
        return float(np.mean(losers)) if losers else 0.0

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(r for r in self.recent_returns if r > 0)
        gross_loss = abs(sum(r for r in self.recent_returns if r < 0))
        if gross_loss == 0:
            return 10.0 if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    def to_dict(self) -> Dict:
        return {
            "setup_type": self.setup_type,
            "wins": self.wins,
            "losses": self.losses,
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate * 100, 1),
            "expectancy": round(self.expectancy, 4),
            "avg_winner": round(self.avg_winner, 4),
            "avg_loser": round(self.avg_loser, 4),
            "profit_factor": round(self.profit_factor, 2),
            "confidence": round(self.confidence, 1),
            "is_disabled": self.is_disabled,
            "disabled_at": self.disabled_at,
            "disabled_reason": self.disabled_reason,
            "consecutive_negative_evals": self.consecutive_negative_evals,
            "max_drawdown": round(self.max_drawdown, 4),
            "recent_returns": [round(r, 4) for r in self.recent_returns[-20:]],
            "last_evaluated": self.last_evaluated,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "SetupStats":
        stats = cls(data["setup_type"])
        stats.wins = data.get("wins", 0)
        stats.losses = data.get("losses", 0)
        stats.total_trades = data.get("total_trades", 0)
        stats.confidence = data.get("confidence", CONFIDENCE_BASE)
        stats.is_disabled = data.get("is_disabled", False)
        stats.disabled_at = data.get("disabled_at")
        stats.disabled_reason = data.get("disabled_reason")
        stats.consecutive_negative_evals = data.get("consecutive_negative_evals", 0)
        stats.max_drawdown = data.get("max_drawdown", 0.0)
        stats.recent_returns = data.get("recent_returns", [])
        stats.last_evaluated = data.get("last_evaluated")
        return stats


class StrategyTracker:
    """
    Manages per-setup performance tracking across the system.
    
    Usage:
        tracker = get_tracker()
        
        # Check if a setup is allowed
        if tracker.is_allowed("MOMENTUM"):
            # Proceed with MOMENTUM setup scoring
            ...
        
        # Get confidence for a setup
        conf = tracker.get_confidence("MOMENTUM")
        
        # EOD: update stats from resolved trades
        tracker.update_all_stats_eod()
    """

    def __init__(self):
        self._stats: Dict[str, SetupStats] = {}
        for setup in KNOWN_SETUPS:
            self._stats[setup] = SetupStats(setup)

    def get_stats(self, setup_type: str) -> SetupStats:
        """Returns SetupStats for a given setup type."""
        if setup_type not in self._stats:
            self._stats[setup_type] = SetupStats(setup_type)
        return self._stats[setup_type]

    def is_allowed(self, setup_type: str) -> bool:
        """Returns True if the setup is currently enabled (not disabled)."""
        stats = self.get_stats(setup_type)
        return not stats.is_disabled

    def get_confidence(self, setup_type: str) -> float:
        """Returns the confidence level (0-100) for a given setup type."""
        return self.get_stats(setup_type).confidence

    def should_disable(self, setup_type: str) -> Tuple[bool, Optional[str]]:
        """
        Evaluates whether a setup should be disabled based on rolling performance.
        
        Returns:
            (should_disable: bool, reason: str or None)
        """
        stats = self.get_stats(setup_type)

        if stats.total_trades < MIN_TRADES_FOR_EVALUATION:
            return False, None

        # Rule 1: Win rate < 30% over recent trades
        recent_wins = sum(1 for r in stats.recent_returns if r > 0)
        recent_total = len(stats.recent_returns)
        if recent_total >= MIN_TRADES_FOR_EVALUATION:
            recent_wr = recent_wins / recent_total
            if recent_wr < DISABLE_WIN_RATE_THRESHOLD:
                return True, f"Win rate {recent_wr:.0%} below {DISABLE_WIN_RATE_THRESHOLD:.0%} threshold over last {recent_total} trades"

        # Rule 2: Expectancy < threshold for N consecutive evaluations
        if stats.consecutive_negative_evals >= DISABLE_CONSECUTIVE_EVAL:
            return True, f"Negative expectancy for {stats.consecutive_negative_evals} consecutive evaluations"

        # Rule 3: Max drawdown exceeded
        if stats.max_drawdown > DISABLE_MAX_DRAWDOWN:
            return True, f"Setup max drawdown {stats.max_drawdown:.1%} exceeds {DISABLE_MAX_DRAWDOWN:.0%} limit"

        return False, None

    def should_recover(self, setup_type: str) -> bool:
        """
        Checks if a disabled setup should be re-enabled.
        Re-evaluates weekly: if last RECOVERY_MIN_TRADES paper trades are positive → re-enable.
        """
        stats = self.get_stats(setup_type)
        if not stats.is_disabled:
            return False

        # Check if enough time has passed since disable
        if stats.disabled_at:
            try:
                disabled_date = datetime.fromisoformat(stats.disabled_at).date()
                days_disabled = (date.today() - disabled_date).days
                if days_disabled < RECOVERY_EVAL_DAYS:
                    return False  # Too soon to re-evaluate
            except Exception:
                pass

        # Check recent trades for recovery signal
        if len(stats.recent_returns) >= RECOVERY_MIN_TRADES:
            last_n = stats.recent_returns[-RECOVERY_MIN_TRADES:]
            if np.mean(last_n) > 0:
                return True

        return False

    # ─────────────────────────────────────────────
    # EOD UPDATE
    # ─────────────────────────────────────────────

    def update_all_stats_eod(self):
        """
        End-of-day update: loads all resolved trades, recomputes per-setup stats,
        applies auto-disable/recovery logic, and persists state.
        """
        logger.info("[STRATEGY_TRACKER] Starting EOD strategy evaluation...")

        # Load resolved trades
        resolved_by_setup = self._load_resolved_trades_by_setup()

        for setup_type, trades in resolved_by_setup.items():
            stats = self.get_stats(setup_type)

            # Update rolling stats
            stats.total_trades = len(trades)
            stats.wins = sum(1 for t in trades if t > 0)
            stats.losses = stats.total_trades - stats.wins
            stats.recent_returns = trades[-20:]  # Last 20 trades

            # Update confidence
            stats.confidence = self._compute_confidence(stats)

            # Compute max drawdown for setup
            stats.max_drawdown = self._compute_setup_drawdown(trades)

            # Update consecutive negative eval counter
            if stats.expectancy < DISABLE_EXPECTANCY_THRESHOLD:
                stats.consecutive_negative_evals += 1
            else:
                stats.consecutive_negative_evals = 0

            stats.last_evaluated = datetime.now().isoformat()

            # Auto-disable check
            should_disable, reason = self.should_disable(setup_type)
            if should_disable and not stats.is_disabled:
                stats.is_disabled = True
                stats.disabled_at = datetime.now().isoformat()
                stats.disabled_reason = reason
                logger.warning(
                    f"[STRATEGY_TRACKER] 🔴 DISABLED: {setup_type} — {reason} | "
                    f"WR={stats.win_rate:.0%}, Exp={stats.expectancy:+.2f}%, "
                    f"Conf={stats.confidence:.0f}/100"
                )

            # Recovery check
            elif stats.is_disabled and self.should_recover(setup_type):
                stats.is_disabled = False
                stats.disabled_at = None
                stats.disabled_reason = None
                stats.consecutive_negative_evals = 0
                logger.info(
                    f"[STRATEGY_TRACKER] 🟢 RECOVERED: {setup_type} — "
                    f"recent expectancy turned positive, re-enabled"
                )

            # Log active setup status
            elif not stats.is_disabled:
                status = "🟢" if stats.confidence >= 60 else "🟡" if stats.confidence >= 40 else "🟠"
                logger.info(
                    f"[STRATEGY_TRACKER] {status} {setup_type}: "
                    f"WR={stats.win_rate:.0%}, Exp={stats.expectancy:+.3f}%, "
                    f"PF={stats.profit_factor:.2f}, Conf={stats.confidence:.0f}/100, "
                    f"Trades={stats.total_trades}"
                )

        # Persist state
        self._save_state()

    def _compute_confidence(self, stats: SetupStats) -> float:
        """Computes confidence from recent trade outcomes."""
        conf = CONFIDENCE_BASE
        for ret in stats.recent_returns[-20:]:
            if ret > 0:
                conf += CONFIDENCE_PER_WIN
            else:
                conf += CONFIDENCE_PER_LOSS
        return max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, conf))

    def _compute_setup_drawdown(self, returns: List[float]) -> float:
        """Computes max drawdown for a setup's return series."""
        if not returns:
            return 0.0
        cumulative = np.cumsum(returns)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = cumulative - running_max
        return abs(float(np.min(drawdowns))) if len(drawdowns) > 0 else 0.0

    def _load_resolved_trades_by_setup(self) -> Dict[str, List[float]]:
        """Loads resolved trades and groups returns by setup type."""
        try:
            import db_manager
            import config

            db = db_manager.get_db()
            records = []

            if db is not None:
                try:
                    col = db[config.MONGO_COLLECTION_PICKS]
                    # MEMORY OPTIMIZATION: Only fetch last 60 days of picks to avoid OOM on 512MB server
                    cutoff_date = (date.today() - timedelta(days=60)).isoformat()
                    records = list(col.find({"date": {"$gte": cutoff_date}}))
                    if not records: # Fallback if dates are formatted differently
                        records = list(col.find().sort([("_id", -1)]).limit(60))
                except Exception as e:
                    logger.error(f"[STRATEGY_TRACKER] Error loading from MongoDB: {e}")

            if not records:
                # If falling back to JSON, memory is a concern so we slice the last 60
                all_json = db_manager._read_json("daily_picks.json")
                records = all_json[-60:] if all_json else []

            by_setup: Dict[str, List[float]] = {}
            for r in records:
                picks_list = r.get("picks", r.get("top_picks", []))
                for p in picks_list:
                    setup = p.get("trade_type", "UNKNOWN")
                    ret = self._get_best_return(p)
                    if ret is not None:
                        if setup not in by_setup:
                            by_setup[setup] = []
                        by_setup[setup].append(ret)

            return by_setup

        except Exception as e:
            logger.error(f"[STRATEGY_TRACKER] Error loading trades: {e}")
            return {}

    def _get_best_return(self, pick: Dict) -> Optional[float]:
        """Gets the best available forward return from a resolved trade."""
        for key in ("future_1d_return", "intraday_return", "future_5d_return", "future_3d_return"):
            val = pick.get(key)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    continue
        return None

    # ─────────────────────────────────────────────
    # PERSISTENCE
    # ─────────────────────────────────────────────

    def _save_state(self):
        """Saves tracker state to MongoDB and local JSON."""
        state = {
            "setups": {k: v.to_dict() for k, v in self._stats.items()},
            "saved_at": datetime.now().isoformat(),
        }

        # Local JSON
        try:
            import config
            path = os.path.join(config.DATA_DIR, "strategy_tracker_state.json")
            with open(path, "w") as f:
                json.dump(state, f, indent=2)
            logger.info("[STRATEGY_TRACKER] State saved to local JSON")
        except Exception as e:
            logger.error(f"[STRATEGY_TRACKER] Failed to save to JSON: {e}")

        # MongoDB
        try:
            import db_manager
            db = db_manager.get_db()
            if db is not None:
                col = db["strategy_tracker_state"]
                col.replace_one({"_id": "latest"}, {**state, "_id": "latest"}, upsert=True)
                logger.info("[STRATEGY_TRACKER] State saved to MongoDB")
        except Exception as e:
            logger.error(f"[STRATEGY_TRACKER] Failed to save to MongoDB: {e}")

    def load_state(self) -> bool:
        """Loads persisted state from MongoDB or local JSON."""
        # Try MongoDB
        try:
            import db_manager
            db = db_manager.get_db()
            if db is not None:
                col = db["strategy_tracker_state"]
                state = col.find_one({"_id": "latest"})
                if state:
                    return self._apply_state(state)
        except Exception:
            pass

        # Fallback to JSON
        try:
            import config
            path = os.path.join(config.DATA_DIR, "strategy_tracker_state.json")
            if os.path.exists(path):
                with open(path, "r") as f:
                    state = json.load(f)
                return self._apply_state(state)
        except Exception as e:
            logger.error(f"[STRATEGY_TRACKER] Failed to load state: {e}")

        return False

    def _apply_state(self, state: Dict) -> bool:
        """Applies loaded state to tracker."""
        try:
            setups = state.get("setups", {})
            for setup_type, data in setups.items():
                self._stats[setup_type] = SetupStats.from_dict(data)
            logger.info(
                f"[STRATEGY_TRACKER] State loaded — "
                f"{sum(1 for s in self._stats.values() if s.is_disabled)} setups disabled, "
                f"{sum(1 for s in self._stats.values() if not s.is_disabled)} active"
            )
            return True
        except Exception as e:
            logger.error(f"[STRATEGY_TRACKER] Failed to apply state: {e}")
            return False

    # ─────────────────────────────────────────────
    # REPORTING
    # ─────────────────────────────────────────────

    def get_status_report(self) -> List[Dict]:
        """Returns a summary of all setup types for reporting."""
        report = []
        for setup_type in sorted(self._stats.keys()):
            stats = self._stats[setup_type]
            if stats.total_trades == 0:
                continue
            report.append(stats.to_dict())
        return sorted(report, key=lambda x: x["confidence"], reverse=True)

    def get_disabled_setups(self) -> List[str]:
        """Returns list of currently disabled setup type names."""
        return [s.setup_type for s in self._stats.values() if s.is_disabled]

    def get_active_setups(self) -> List[str]:
        """Returns list of currently active setup type names."""
        return [s.setup_type for s in self._stats.values() if not s.is_disabled]


# ─────────────────────────────────────────────
# SINGLETON instance
# ─────────────────────────────────────────────
_tracker_instance: Optional[StrategyTracker] = None


def get_tracker() -> StrategyTracker:
    """Returns the global singleton StrategyTracker, loading state if needed."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = StrategyTracker()
        _tracker_instance.load_state()
    return _tracker_instance
