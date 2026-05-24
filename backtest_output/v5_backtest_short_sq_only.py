"""Run v5_full_backtest with require_short_squeeze=True (original)"""
import engines.liq_cluster_engine_v5 as eng
from engines.liq_cluster_engine_v5 import V5Config
eng.CFG = V5Config(require_short_squeeze=True)
print("Set CFG.require_short_squeeze = True")

# Now run the main backtest
exec(open("backtest_output/v5_full_backtest.py").read())
