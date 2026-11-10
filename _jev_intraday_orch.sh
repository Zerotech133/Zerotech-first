#!/bin/bash
# JEV 盘中增量汇聚编排（自动化 5ac9e3f8）
# 用法: bash _jev_intraday_orch.sh [step]   step=all(默认)|1|2
# 时间判定走 python 本地时区(机器即 UTC+8=北京)，避免 tzdata/跨进程偏差。
set +e
cd /c/aios
PY="C:/Program Files/Python311/python.exe"
LOCK="/c/Temp/jev_intraday.lock"
STEP="${1:-all}"

# ---- 时间判定（python 本地时区 = 北京，机器即 China Standard Time）----
DAY=$(timeout 30 "$PY" -c "import datetime; b=datetime.datetime.now(); print(b.strftime('%Y%m%d'))")
TRADING=$(timeout 30 "$PY" -c "import datetime; b=datetime.datetime.now(); dow=b.weekday(); hhmm=int(b.strftime('%H%M')); print(1 if (dow<5 and ((900<=hhmm<1130) or (1300<=hhmm<1500))) else 0)")
echo "TIMECHECK DAY=$DAY TRADING=$TRADING"
if [ "$TRADING" != "1" ]; then
  echo "SKIP_OFFHOURS"
  exit 0
fi
echo "TRADING=true"

# ---- 单实例保护（step=2 视为同轮续跑，不再判定锁）----
if [ "$STEP" != "2" ]; then
  if [ -f "$LOCK" ]; then
    OLD_PID=$(cat "$LOCK" 2>/dev/null | tr -d '[:space:]')
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
      echo "SKIP_LOCKED PID=$OLD_PID"
      exit 0
    fi
  fi
  echo "$$" > "$LOCK"
  echo "LOCK_SET PID=$$"
fi

if [ "$STEP" != "2" ]; then
  echo "=== STEP1 分钟线 jev_mins_fetch --day $DAY --limit 60 ==="
  timeout 540 env PYTHONIOENCODING=utf-8 "$PY" tools/jev_mins_fetch.py --day "$DAY" --limit 60
  echo "STEP1_EXIT=$?"
fi

if [ "$STEP" != "1" ]; then
  echo "=== STEP2 社交 jev_social_fetch --day $DAY --limit 60 ==="
  timeout 540 env PYTHONIOENCODING=utf-8 "$PY" tools/jev_social_fetch.py --day "$DAY" --limit 60
  echo "STEP2_EXIT=$?"
fi

if [ "$STEP" != "1" ]; then
  rm -f "$LOCK"
  echo "LOCK_CLEARED"
fi
echo "DONE"
