#!/bin/bash
# JEV 对齐流水线（20261106 -> 20261109）：等待日线发布 -> 跑 JEV 采集与快照
cd /c/aios || exit 2
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1
PY311="C:/Program Files/Python311/python.exe"
DAY=20261109
DAILY="quant_backtest/data/tushare/daily/d_20261109.parquet"
LOG="/c/aios/_jev_pipeline.log"
echo "[pipeline] START $(date)" | tee -a "$LOG"

# ---- Stage 1: 等日线发布（反复跑 guard，直到 d_20261109.parquet 落盘或 24 次耗尽）----
OK=0
for i in $(seq 1 24); do
  echo "[pipeline] guard attempt $i @ $(date)" | tee -a "$LOG"
  "$PY311" tools/data_sync_manual.py --steps guard >> "$LOG" 2>&1
  if [ -f "$DAILY" ]; then
    echo "[pipeline] DAILY READY after attempt $i" | tee -a "$LOG"
    OK=1
    break
  fi
  echo "[pipeline] daily missing, sleep 450s" | tee -a "$LOG"
  sleep 450
done
if [ "$OK" -ne 1 ]; then
  echo "[pipeline] DAILY FAILED after 24 tries" | tee /c/aios/_daily_fail.flag
  exit 3
fi
echo "DAILY_OK" > /c/aios/_daily_ready.flag

# ---- Stage 2: JEV 采集与快照对齐 ----
echo "[pipeline] concept fetch --force" | tee -a "$LOG"
"$PY311" tools/jev_concept_fetch.py --day $DAY --force >> "$LOG" 2>&1
echo "concept rc=$?" | tee -a "$LOG"

echo "[pipeline] mins fetch --force" | tee -a "$LOG"
"$PY311" tools/jev_mins_fetch.py --day $DAY --force >> "$LOG" 2>&1
echo "mins rc=$?" | tee -a "$LOG"

echo "[pipeline] social fetch" | tee -a "$LOG"
"$PY311" tools/jev_social_fetch.py --day $DAY >> "$LOG" 2>&1
echo "social rc=$?" | tee -a "$LOG"

echo "[pipeline] kg fetch --all-boards --force" | tee -a "$LOG"
"$PY311" tools/jev_kg_fetch.py --day $DAY --all-boards --force >> "$LOG" 2>&1
echo "kg rc=$?" | tee -a "$LOG"

echo "JEV_DONE" > /c/aios/_jev_done.flag
echo "[pipeline] FINISHED $(date)" | tee -a "$LOG"
