import os
import sys
import logging

logging.basicConfig(level=logging.INFO)

# Add Stalker to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stalker.learning.adaptive_weights import get_engine
from stalker.learning.strategy_tracker import get_tracker

def test_learning_loop():
    print("Testing Adaptive Weights Engine...")
    engine = get_engine()
    print("Engine active:", engine.is_active)
    
    print("\nRunning EOD update for weights...")
    engine.update_weights_eod()
    print("\nFactor Status Report:")
    for rep in engine.get_factor_status_report():
        print(f"  {rep['factor']:<20} IC: {rep['smoothed_ic']:+.3f}  Status: {rep['status']}")
        
    print("\n==============================")
    print("Testing Strategy Tracker...")
    tracker = get_tracker()
    
    print("\nRunning EOD update for strategy stats...")
    tracker.update_all_stats_eod()
    print("\nStrategy Status Report:")
    for rep in tracker.get_status_report():
        print(f"  {rep['setup_type']:<20} WR: {rep['win_rate']:>5.1f}%  Exp: {rep['expectancy']:>6.2f}%  Conf: {rep['confidence']:>5.1f}  Status: {'DISABLED' if rep['is_disabled'] else 'ACTIVE'}")
        
if __name__ == "__main__":
    test_learning_loop()
