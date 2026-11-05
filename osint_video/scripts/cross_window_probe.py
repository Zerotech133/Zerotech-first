"""跨窗口召回 A/B 探针：在**一次性库**上复现"历史事件已存在 + 新批次未归属"的滚动场景。

为什么需要它
------------
``cross_window`` 的效果只在一种形态下可见：**窗外已有归属好的历史事件，
窗内又来了一批还没归属的事实**。生产 cron 每天滚 14 天窗，今天的新事实与
20 天前的老事件正好是这个关系；但静态语料库一把跑完时，两个时点的事实都在
同一个窗内，跨窗路径根本不会被走到——``cross_window_merged`` 恒为 0，
看上去像"开了没效果"。

所以验证必须把时间轴人为折叠：

1. ``--window-days 60`` 跑一轮，让全部事实成组（模拟历史已经积累好）；
2. 删掉窗内（默认 14 天）事实的成员关系，它们变回"未归属"（模拟新到批次），
   同时清掉因此空掉的事件；
3. ``--window-days 14`` 再跑一轮，窗内是待归属的新批次，窗外是已归属的
   历史事件 → 这才是跨窗口召回的用武之地；
4. 对照组把第 3 步换成 ``--cross-window`` 的另一种取值，其余完全相同。

**它会删数据**
--------------
第 1、2 步会 ``DELETE FROM osint_story_member`` / ``osint_story``。这不是可
回滚的操作，因此在生产库上误跑等于清空事件层。三重护栏：

1. 库名必须以 ``_calib`` 结尾（``make_calibration_db.py`` 建出来的就是）；
2. 必须显式 ``--yes``；
3. 库里有事件时不阻拦（那是上轮实验的残留），但会打印将被清掉的条数。

用法
----
    python scripts/cross_window_probe.py --mode off --yes
    python scripts/cross_window_probe.py --mode on  --yes

输出两行 JSON（``off`` 与 ``on`` 各一次结果）供人工对比，指标含义：

- ``anchors_loaded``：窗外已归属事实条数。为 0 说明场景没构造成功
  （窗外没有历史事件），此时 ``cross_window_merged`` 必为 0，**不能**据此
  判断"跨窗无效"；
- ``cross_window_merged``：真正走跨窗路径挂上的配对数；
- ``stories_created``：新开事件数。跨窗有效时它应当变小——同一件事的后半段
  挂回老事件，而不是开新事件。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from config.settings import get_settings  # noqa: E402

#: 允许被清空的库名后缀。生产库叫 ``osint``，正是不匹配这个名字才被挡住。
CALIB_SUFFIX: str = "_calib"

#: 与 ``story_group.CROSS_WINDOW_DAYS_MULTIPLIER`` 对齐的种子窗口倍数。
#: 这里直接写 60 天而不是引用常量：种子只要"覆盖全部语料"即可，与跨窗口
#: 召回带宽无关，耦合过去反而会让"改倍数"意外改变验证场景。
SEED_WINDOW_DAYS: int = 60

_RESULT_KEYS: tuple[str, ...] = (
    "units_scanned",
    "stories_created",
    "stories_reused",
    "members_added",
    "unrelated",
    "anchors_loaded",
    "cross_window_merged",
    "borderline",
    "judge_calls",
    "elapsed_ms",
)


def _engine():
    settings = get_settings()
    if not settings.pg_db.endswith(CALIB_SUFFIX):
        raise SystemExit(
            f"[REFUSED] PG_DB={settings.pg_db!r} 不是一次性库（要求以 {CALIB_SUFFIX!r} 结尾）。\n"
            "           本脚本会 DELETE osint_story_member / osint_story，\n"
            "           在生产库上跑等于清空事件层。先跑 scripts/make_calibration_db.py。"
        )
    return create_engine(
        f"postgresql+psycopg://{settings.pg_user}:{settings.pg_password}"
        f"@{settings.pg_host}:{settings.pg_port}/{settings.pg_db}"
    )


def sql(engine, query: str, **params):
    with engine.begin() as conn:
        result = conn.execute(text(query), params)
        # DELETE 不返回行，硬 fetchall 会抛 ResourceClosedError。
        return result.fetchall() if result.returns_rows else []


def reset(engine) -> None:
    sql(engine, "DELETE FROM osint_story_member")
    sql(engine, "DELETE FROM osint_story")


def run_cli(*args: str) -> dict:
    """调子进程跑一轮聚类，取 stdout 里最后一行 JSON。

    固定加 ``--no-record``：本探针会反复清库重灌跑对照，这些轮次不是生产轮次，
    写进台账只会把趋势基线污染成"事件数 37→31→37→33"的锯齿。需要在台账里
    留下验证痕迹时，用 ``story_group_run.py`` 本身跑，不要用探针。
    """
    env = {**os.environ}
    proc = subprocess.run(
        [sys.executable, "scripts/story_group_run.py", "--no-record", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    payloads = [ln for ln in proc.stdout.strip().splitlines() if ln.startswith("{")]
    if not payloads:
        raise SystemExit(
            f"[FAILED] 子进程没有输出 JSON（exit={proc.returncode}）：\n{proc.stderr[-2000:]}"
        )
    return json.loads(payloads[-1])


def seed_history(engine) -> dict:
    """第一轮：大窗口全量成组，模拟"历史事件已经积累好了"。"""
    reset(engine)
    return run_cli("--window-days", str(SEED_WINDOW_DAYS), "--judge", "rule")


def strip_recent(engine, days: int) -> tuple[int, int]:
    """删掉窗内事实的成员关系并清掉空掉的事件，返回剩余 (成员, 事件)。"""
    sql(
        engine,
        """DELETE FROM osint_story_member m
             USING osint_knowledge_unit u
             WHERE m.unit_key = u.unit_key
               AND u.created_at >= now() - make_interval(days => :days)""",
        days=days,
    )
    # 空事件必须一起清：留着会让"事件数"这个指标失真，而事件数正是本实验
    # 最主要的观测量。注意主键列是 ``story_id`` 而不是 ``id``（story_key 是
    # 另一个 UNIQUE 列，容易顺手写错）。
    sql(
        engine,
        """DELETE FROM osint_story s
             WHERE NOT EXISTS (
                   SELECT 1 FROM osint_story_member m WHERE m.story_id = s.story_id)""",
    )
    row = sql(
        engine,
        "SELECT (SELECT count(*) FROM osint_story_member) AS m,"
        "       (SELECT count(*) FROM osint_story) AS s",
    )[0]
    return int(row[0]), int(row[1])


def probe(mode: str, days: int, yes: bool) -> dict:
    engine = _engine()
    if not yes:
        raise SystemExit(
            "[REFUSED] 本脚本会清空事件层，必须加 --yes 明确授权（详见模块文档）。"
        )

    settings = get_settings()
    print(f"[db] {settings.pg_host}:{settings.pg_port}/{settings.pg_db}", file=sys.stderr)

    seeded = seed_history(engine)
    members, stories = strip_recent(engine, days)
    print(
        f"[seed] 历史事件 {seeded['stories_created']} 个 / {seeded['members_added']} 条成员；"
        f"剥掉最近 {days} 天后剩 {stories} 个事件 / {members} 条锚点",
        file=sys.stderr,
    )
    if members == 0:
        print(
            "[WARN] 窗外锚点为 0：语料时间跨度不足，跨窗路径不会被走到，"
            "本次结果不能用来判断跨窗效果。",
            file=sys.stderr,
        )

    flag = "--cross-window" if mode == "on" else "--no-cross-window"
    result = run_cli("--window-days", str(days), "--judge", "rule", flag)
    return {k: result.get(k) for k in _RESULT_KEYS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="跨窗口召回 A/B 探针（会清空一次性库的事件层）"
    )
    parser.add_argument("--mode", choices=("off", "on"), required=True)
    parser.add_argument(
        "--days", type=int, default=14, help="窗内天数，与生产的 --window-days 对齐"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="明确授权清空事件层（无此参数直接拒绝运行）",
    )
    args = parser.parse_args(argv)

    payload = {"cross_window": args.mode, "days": args.days}
    payload.update(probe(args.mode, args.days, args.yes))
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
