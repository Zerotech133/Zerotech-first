#!/bin/bash
# 题材成分（开盘啦）重试：源可能盘后稍晚发布，按 5 分钟节奏重试
cd /c/aios || exit 2
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1
PY311="C:/Program Files/Python311/python.exe"
LOG="/c/aios/_concept_retry.log"
echo "[concept-retry] START $(date)" | tee -a "$LOG"
for i in $(seq 1 14); do
  echo "[concept-retry] attempt $i @ $(date)" | tee -a "$LOG"
  "$PY311" tools/jev_concept_fetch.py --day 20261109 --force >> "$LOG" 2>&1
  rc=$?
  echo "[concept-retry] attempt $i rc=$rc" | tee -a "$LOG"
  if [ "$rc" -eq 0 ]; then
    echo "CONCEPT_OK" > /c/aios/_concept_done.flag
    echo "[concept-retry] DONE" | tee -a "$LOG"
    break
  fi
  echo "[concept-retry] empty, sleep 300s" | tee -a "$LOG"
  sleep 300
done
if [ ! -f /c/aios/_concept_done.flag ]; then
  echo "CONCEPT_FAIL" > /c/aios/_concept_fail.flag
  echo "[concept-retry] EXHAUSTED" | tee -a "$LOG"
fi
