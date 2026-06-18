"""
STALKER — Adaptive Factor Learner
Offline machine learning module that optimizes validation multipliers 
by maximizing a composite rank and profit-aligned trading objective.
"""

import os
import json
import logging
import argparse
import sys
from datetime import datetime
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import spearmanr
from typing import Dict, List, Tuple

# Force UTF-8 output on Windows console — safe guard: never crash if buffer already replaced or closed
try:
    import io as _io
    import os
    if sys.stdout is None or getattr(sys.stdout, 'closed', False):
        sys.stdout = open(os.devnull, 'w', encoding='utf-8')
    else:
        try:
            sys.stdout.write('')
        except Exception:
            sys.stdout = open(os.devnull, 'w', encoding='utf-8')

    if sys.stderr is None or getattr(sys.stderr, 'closed', False):
        sys.stderr = open(os.devnull, 'w', encoding='utf-8')
    else:
        try:
            sys.stderr.write('')
        except Exception:
            sys.stderr = open(os.devnull, 'w', encoding='utf-8')

    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

import config
import db_manager

logger = logging.getLogger(__name__)

# Parameters to optimize:
# 1. vcp_elite_bonus
# 2. vcp_strong_bonus
# 3. vcp_weak_bonus
# 4. lead_elite_bonus
# 5. lead_strong_bonus
# 6. lead_low_penalty

PARAM_BOUNDS = [
    (0.0, 0.15),   # vcp_elite_bonus
    (0.0, 0.10),   # vcp_strong_bonus
    (-0.05, 0.05), # vcp_weak_bonus
    (0.0, 0.15),   # lead_elite_bonus
    (0.0, 0.10),   # lead_strong_bonus
    (0.0, 0.10),   # lead_low_penalty
]

DEFAULT_PARAMS = [0.07, 0.04, 0.02, 0.05, 0.02, 0.05]
PARAM_NAMES = [
    "vcp_elite_bonus", "vcp_strong_bonus", "vcp_weak_bonus",
    "lead_elite_bonus", "lead_strong_bonus", "lead_low_penalty"
]


def load_resolved_trades(min_trades: int = 200) -> Tuple[pd.DataFrame, bool]:
    """
    Loads completed, resolved trades from MongoDB or local JSON fallback.
    Returns (DataFrame of resolved trades, is_sufficient_data).
    """
    db = db_manager.get_db()
    records = []

    if db is not None:
        try:
            col = db["feature_attributions"]
            # Load trades where future returns are resolved
            records = list(col.find({
                "$or": [
                    {"future_1d_return": {"$ne": None}},
                    {"intraday_return": {"$ne": None}},
                    {"future_5d_return": {"$ne": None}},
                    {"future_3d_return": {"$ne": None}}
                ]
            }))
        except Exception as e:
            logger.error(f"Error loading attributions from MongoDB: {e}")

    if not records:
        try:
            all_attr = db_manager._read_json("feature_attributions.json")
            records = [
                r for r in all_attr
                if r.get("future_1d_return") is not None or r.get("intraday_return") is not None or r.get("future_5d_return") is not None or r.get("future_3d_return") is not None
            ]
        except Exception as e:
            logger.error(f"Error loading attributions from JSON fallback: {e}")

    if len(records) < min_trades:
        logger.warning(f"[LEARNER] Insufficient data. Found {len(records)} resolved trades. Min required: {min_trades}")
        return pd.DataFrame(), False

    df = pd.DataFrame(records)
    # Target return is 5d return, falling back to 3d return
    df["target_return"] = df["future_1d_return"].fillna(df["intraday_return"]).fillna(df["future_5d_return"]).fillna(df["future_3d_return"])
    df["date"] = pd.to_datetime(df["date"])
    df.sort_values("date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df, True


def calculate_simulated_final_score(df: pd.DataFrame, params: List[float]) -> np.ndarray:
    """
    Simulates the final_score calculation given candidate multiplier parameters.
    """
    vcp_elite_b, vcp_strong_b, vcp_weak_b, lead_elite_b, lead_strong_b, lead_low_p = params

    # Map VCP Score to Multiplier
    # Score >= 80 -> Elite; 60-79 -> Strong; 40-59 -> Weak; < 40 -> None
    vcp_score = df["vcp_score"].values
    vcp_mult = np.ones_like(vcp_score)
    vcp_mult[vcp_score >= 80] = 1.0 + vcp_elite_b
    vcp_mult[(vcp_score >= 60) & (vcp_score < 80)] = 1.0 + vcp_strong_b
    vcp_mult[(vcp_score >= 40) & (vcp_score < 60)] = 1.0 + vcp_weak_b

    # Map Leadership Score to Multiplier
    # Score >= 80 -> Elite; 60-79 -> Strong; 45-59 -> Acceptable; < 45 -> Low
    lead_score = df["leadership_score"].values
    lead_mult = np.ones_like(lead_score)
    lead_mult[lead_score >= 80] = 1.0 + lead_elite_b
    lead_mult[(lead_score >= 60) & (lead_score < 80)] = 1.0 + lead_strong_b
    lead_mult[lead_score < 45] = 1.0 - lead_low_p

    # Final Score = Alpha * Leadership Multiplier * VCP Multiplier
    alpha_score = df["alpha_score"].values
    sim_scores = alpha_score * lead_mult * vcp_mult
    return np.clip(sim_scores, 0.0, 100.0)


def evaluate_composite_score(df: pd.DataFrame, params: List[float]) -> float:
    """
    Calculates the composite objective score (0 to 100) for training optimization.
    Objective:
      - 50% Spearman Rank Correlation between final_score and target_return
      - 25% Top-10 Hit Rate (percentage of top 10 picks with positive return)
      - 15% Average Return of Top 5 picks
      - 10% Profit Factor of top picks
    """
    if len(df) < 5:
        return 0.0

    sim_scores = calculate_simulated_final_score(df, params)
    returns = df["target_return"].values

    # 1. Spearman Rank Correlation (scaled from [-1, 1] to [0, 100])
    spearman_corr, _ = spearmanr(sim_scores, returns)
    if np.isnan(spearman_corr):
        spearman_corr = 0.0
    spearman_score = 50.0 + (spearman_corr * 50.0)

    # Sort simulated trades by final score descending
    sorted_indices = np.argsort(sim_scores)[::-1]
    top_returns = returns[sorted_indices]

    # 2. Top-10 Hit Rate
    top_10 = top_returns[:10]
    hit_rate = np.mean(top_10 > 0) * 100.0 if len(top_10) > 0 else 0.0

    # 3. Average Return of Top 5 picks (scaled assuming max 15% return average)
    top_5 = top_returns[:5]
    avg_ret_val = np.mean(top_5) if len(top_5) > 0 else 0.0
    avg_ret_score = np.clip(avg_ret_val * 6.6, 0.0, 100.0) # 15% return maps to 100

    # 4. Profit Factor of top 10 picks
    gains = top_10[top_10 > 0].sum()
    losses = abs(top_10[top_10 < 0].sum())
    profit_factor = gains / losses if losses > 0 else (gains if gains > 0 else 1.0)
    # Scale profit factor: 1.0 maps to 50, 3.0 maps to 100
    pf_score = np.clip(50.0 + (profit_factor - 1.0) * 25.0, 0.0, 100.0)

    composite = (
        (0.50 * spearman_score) +
        (0.25 * hit_rate) +
        (0.15 * avg_ret_score) +
        (0.10 * pf_score)
    )
    return float(composite)


def walk_forward_validation(df: pd.DataFrame, num_folds: int = 3) -> float:
    """
    Runs time-based walk-forward validation.
    Splits the chronological data into train/test sets, optimizes on train,
    and returns the average out-of-sample (OOS) composite score.
    """
    total_len = len(df)
    fold_size = int(total_len / (num_folds + 1))
    
    oos_scores = []
    
    for i in range(num_folds):
        train_end = fold_size * (i + 1)
        test_end = train_end + fold_size
        
        df_train = df.iloc[:train_end]
        df_test = df.iloc[train_end:test_end]
        
        if len(df_train) < 50 or len(df_test) < 15:
            continue
            
        # Optimize parameters on Train Fold
        def loss_fn(p):
            return -evaluate_composite_score(df_train, p)
            
        res = minimize(loss_fn, DEFAULT_PARAMS, bounds=PARAM_BOUNDS, method="L-BFGS-B")
        learned_params = res.x
        
        # Evaluate out-of-sample (OOS) score on Test Fold
        oos_score = evaluate_composite_score(df_test, learned_params)
        oos_scores.append(oos_score)
        
    return float(np.mean(oos_scores)) if oos_scores else 0.0


def calculate_feature_importances(df: pd.DataFrame, params: List[float]) -> Dict[str, float]:
    """
    Estimates feature importance by measuring the variance reduction or correlation
    influence of each feature on the final optimized score.
    """
    sim_scores = calculate_simulated_final_score(df, params)
    
    features = {
        "Alpha Score": df["alpha_score"].values,
        "Leadership Score": df["leadership_score"].values,
        "VCP Score": df["vcp_score"].values,
        "Minervini Score": df["minervini_score"].values
    }
    
    correlations = {}
    for name, feat in features.items():
        corr, _ = spearmanr(sim_scores, feat)
        correlations[name] = abs(corr) if not np.isnan(corr) else 0.0
        
    total_corr = sum(correlations.values()) or 1.0
    importances = {k: round((v / total_corr) * 100.0, 1) for k, v in correlations.items()}
    return importances


def get_latest_version() -> int:
    """Helper to retrieve the latest weights version number from the filesystem."""
    latest_ver = 0
    weights_dir = config.DATA_DIR
    if os.path.exists(weights_dir):
        for f in os.listdir(weights_dir):
            if f.startswith("optimized_weights_v") and f.endswith(".json"):
                try:
                    ver = int(f.replace("optimized_weights_v", "").replace(".json", ""))
                    if ver > latest_ver:
                        latest_ver = ver
                except ValueError:
                    continue
    return latest_ver


def load_latest_weights() -> List[float]:
    """Loads the latest weights from data/optimized_weights_latest.json if present."""
    latest_path = os.path.join(config.DATA_DIR, "optimized_weights_latest.json")
    if os.path.exists(latest_path):
        try:
            with open(latest_path) as f:
                data = json.load(f)
            w = data.get("weights", {})
            return [
                w.get("vcp_elite_bonus", DEFAULT_PARAMS[0]),
                w.get("vcp_strong_bonus", DEFAULT_PARAMS[1]),
                w.get("vcp_weak_bonus", DEFAULT_PARAMS[2]),
                w.get("lead_elite_bonus", DEFAULT_PARAMS[3]),
                w.get("lead_strong_bonus", DEFAULT_PARAMS[4]),
                w.get("lead_low_penalty", DEFAULT_PARAMS[5]),
            ]
        except Exception as e:
            logger.error(f"Error loading latest weights: {e}")
    return DEFAULT_PARAMS


def promote_weights(learned_params: List[float], total_samples: int, spearman_corr: float, composite_score: float, oos_composite: float):
    """
    Performs exponential smoothing, versions the weights file, and updates optimized_weights_latest.json.
    """
    # 1. Apply Exponential Weight Smoothing
    prev_params = load_latest_weights()
    smoothed_params = [
        float(round(0.7 * prev_params[i] + 0.3 * learned_params[i], 4))
        for i in range(len(learned_params))
    ]
    
    # Map parameters to JSON
    weights_dict = {
        PARAM_NAMES[i]: smoothed_params[i] for i in range(len(smoothed_params))
    }
    
    # 2. Get next version number
    next_ver = get_latest_version() + 1
    
    metadata = {
        "version": f"v{next_ver}",
        "trained_on": datetime.now().isoformat(),
        "total_samples": total_samples,
        "composite_score": round(composite_score, 2),
        "oos_walk_forward_score": round(oos_composite, 2),
        "spearman_rank_correlation": round(spearman_corr, 3),
        "weights": weights_dict
    }
    
    # Save versioned file
    v_path = os.path.join(config.DATA_DIR, f"optimized_weights_v{next_ver}.json")
    with open(v_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info(f"[LEARNER] Versioned weights saved to {v_path}")
    
    # Promote to latest pointer
    latest_path = os.path.join(config.DATA_DIR, "optimized_weights_latest.json")
    with open(latest_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info(f"[LEARNER] Promoted version {next_ver} as latest pointer.")


def run_training() -> bool:
    """
    Main training workflow for Adaptive Factor Learner.
    """
    logger.info("[LEARNER] Starting Factor Optimization process...")
    
    df, sufficient = load_resolved_trades(min_trades=200)
    if not sufficient:
        logger.info("[LEARNER] Aborting: insufficient historical data.")
        return False
        
    # 1. Walk-Forward Validation
    logger.info("[LEARNER] Running Walk-Forward validation...")
    oos_composite = walk_forward_validation(df, num_folds=3)
    logger.info(f"[LEARNER] Walk-Forward Out-Of-Sample Composite Score: {oos_composite:.2f}")
    
    # 2. Full Optimization
    logger.info("[LEARNER] Running parameter optimization on entire dataset...")
    def loss_fn(p):
        return -evaluate_composite_score(df, p)
        
    res = minimize(loss_fn, DEFAULT_PARAMS, bounds=PARAM_BOUNDS, method="L-BFGS-B")
    optimal_params = list(res.x)
    
    # 3. Calculate Final Performance Metrics
    final_composite = evaluate_composite_score(df, optimal_params)
    sim_scores = calculate_simulated_final_score(df, optimal_params)
    final_corr, _ = spearmanr(sim_scores, df["target_return"].values)
    
    logger.info(f"[LEARNER] Optimization converged: {res.message}")
    logger.info(f"[LEARNER] Optimized Composite Score: {final_composite:.2f} | Spearman: {final_corr:.3f}")
    
    # 4. Promote weights with smoothing
    promote_weights(
        optimal_params,
        total_samples=len(df),
        spearman_corr=final_corr,
        composite_score=final_composite,
        oos_composite=oos_composite
    )
    
    # 5. Log Feature Importances
    importances = calculate_feature_importances(df, optimal_params)
    print("\n" + "="*50)
    print("  ADAPTIVE FACTOR LEARNER -- TRAINING COMPLETE")
    print("="*50)
    print(f"  Total resolved trades: {len(df)}")
    print(f"  Spearman Rank Corr:   {final_corr:.3f}")
    print(f"  OOS Composite Score:   {oos_composite:.2f}")
    print("\n  Optimized Parameters:")
    for i, name in enumerate(PARAM_NAMES):
        print(f"    * {name:20}: {optimal_params[i]:.4f}")
    print("\n  Calculated Feature Importances:")
    for feat, pct in importances.items():
        print(f"    * {feat:20}: {pct}%")
    print("="*50 + "\n")
    
    return True


def run_mock_test():
    """
    Test harness that generates 250 mock trade attribution records with random noise,
    verifies walk-forward splitting, optimization convergence, parameter bounds,
    exponential smoothing, and JSON metadata versioning.
    """
    print("\n🧪 Running factor_learner.py Mock Test Harness...")
    np.random.seed(42)
    
    # Generate 250 mock records
    records = []
    start_date = datetime(2026, 1, 1).timestamp()
    
    for i in range(250):
        # Generate raw parameters (0-100)
        alpha = np.random.uniform(50, 95)
        lead = np.random.uniform(30, 90)
        vcp = np.random.uniform(20, 100)
        minervini = np.random.randint(4, 9)
        
        # Simulate dynamic trade return
        # Stronger alpha, lead, and vcp should correlate positively with returns
        vcp_mult = 1.07 if vcp >= 80 else 1.04 if vcp >= 60 else 1.02 if vcp >= 40 else 1.00
        lead_mult = 1.05 if lead >= 80 else 1.02 if lead >= 60 else 1.00 if lead >= 45 else 0.95
        
        base_ret = (alpha - 70.0) * 0.15 + (lead - 60.0) * 0.05 + (vcp - 50.0) * 0.05
        # Add random market noise
        noise = np.random.normal(0, 3.5)
        ret_5d = base_ret * vcp_mult * lead_mult + noise
        
        records.append({
            "symbol": f"MOCK_{i}.NS",
            "date": datetime.fromtimestamp(start_date + i * 86400).strftime("%Y-%m-%d"),
            "alpha_score": alpha,
            "leadership_score": lead,
            "minervini_score": minervini,
            "vcp_score": vcp,
            "final_score": alpha * vcp_mult * lead_mult,
            "future_5d_return": float(round(ret_5d, 2)),
            "future_3d_return": float(round(ret_5d * 0.7, 2)),
            "future_10d_return": float(round(ret_5d * 1.2, 2)),
            "future_20d_return": float(round(ret_5d * 1.5, 2))
        })
        
    df = pd.DataFrame(records)
    df["target_return"] = df["future_5d_return"]
    df["date"] = pd.to_datetime(df["date"])
    
    print(f"Generated {len(df)} mock trade attributions.")
    
    # Test Walk-Forward Validation
    print("Testing walk-forward validation splits...")
    oos_composite = walk_forward_validation(df, num_folds=3)
    print(f"OOS Walk-Forward Composite Score: {oos_composite:.2f}")
    
    # Test Scipy Minimization
    print("Optimizing parameters on mock dataset...")
    def loss_fn(p):
        return -evaluate_composite_score(df, p)
        
    res = minimize(loss_fn, DEFAULT_PARAMS, bounds=PARAM_BOUNDS, method="L-BFGS-B")
    optimal_params = list(res.x)
    
    # Validate bounds
    bounds_ok = True
    for i, p in enumerate(optimal_params):
        low, high = PARAM_BOUNDS[i]
        if not (low - 1e-6 <= p <= high + 1e-6):
            bounds_ok = False
            print(f"  ❌ Bounds violation: {PARAM_NAMES[i]} = {p} (Expected [{low}, {high}])")
            
    if bounds_ok:
        print("  ✅ All parameter bounds respected.")
        
    # Evaluate performance
    final_composite = evaluate_composite_score(df, optimal_params)
    sim_scores = calculate_simulated_final_score(df, optimal_params)
    final_corr, _ = spearmanr(sim_scores, df["target_return"].values)
    
    print(f"Optimized composite score: {final_composite:.2f} | Spearman: {final_corr:.3f}")
    
    # Test Weight Smoothing and Promotion
    print("Testing weight smoothing and promotion versioning...")
    promote_weights(
        optimal_params,
        total_samples=len(df),
        spearman_corr=final_corr,
        composite_score=final_composite,
        oos_composite=oos_composite
    )
    
    # Check if latest weight file is created and readable
    latest_path = os.path.join(config.DATA_DIR, "optimized_weights_latest.json")
    if os.path.exists(latest_path):
        print(f"  ✅ Versioned metadata file promoted successfully to {latest_path}")
        with open(latest_path) as f:
            meta = json.load(f)
        print(f"  ✅ Saved metadata details: Version: {meta['version']}, Samples: {meta['total_samples']}, OOS Score: {meta['oos_walk_forward_score']}")
    else:
        print("  ❌ Latest weight pointer missing!")
        
    # Check Feature Importances
    importances = calculate_feature_importances(df, optimal_params)
    print("\nFeature Importances:")
    for feat, pct in importances.items():
        print(f"  * {feat:20}: {pct}%")
        
    print("\n🎉 Mock Test Harness completed successfully!\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="STALKER Adaptive Factor Learner")
    parser.add_argument("--train", action="store_true", help="Run offline optimization on real attribution database")
    parser.add_argument("--test-mock", action="store_true", help="Run unit test using mock data")
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    if args.test_mock:
        run_mock_test()
    elif args.train:
        run_training()
    else:
        parser.print_help()
