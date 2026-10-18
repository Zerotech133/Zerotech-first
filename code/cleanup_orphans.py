"""一次性数据清理（审计遗留，跑完可删本文件）。

1) 删 41 条 learning_case 孤儿：replay_id 无对应 v2_replay 且以 -sim 结尾（仿真样本残余）。
2) 纠正 42 条 v2_attribution_record.market：对齐父 v2_replay.market（seed fixture 打标错误）。

安全：只删 -sim 孤儿（非 -sim 孤儿保留待查）；归因只改 market 标签、值来自父 replay。
含预检 + 执行后验证（剩余应为 0）。用 `uv run python cleanup_orphans.py` 执行。
"""
from sqlalchemy import text

from zhice_os_v2.infrastructure.db.session import get_session

_ORPHAN = (
    "select count(*) from v2_learning_case lc where lc.replay_id like '%-sim' "
    "and not exists (select 1 from v2_replay r where r.replay_id=lc.replay_id)"
)
_MISMATCH = (
    "select count(*) from v2_attribution_record ar "
    "join v2_replay r on r.replay_id=ar.replay_id where ar.market<>r.market"
)

s = get_session()
try:
    print(f"预检: -sim孤儿={s.execute(text(_ORPHAN)).scalar()} "
          f"归因market不符={s.execute(text(_MISMATCH)).scalar()}")

    d = s.execute(text(
        "delete from v2_learning_case lc where lc.replay_id like '%-sim' "
        "and not exists (select 1 from v2_replay r where r.replay_id=lc.replay_id)"
    ))
    u = s.execute(text(
        "update v2_attribution_record ar set market=r.market from v2_replay r "
        "where r.replay_id=ar.replay_id and ar.market<>r.market"
    ))
    s.commit()
    print(f"已删孤儿={d.rowcount} 已纠正归因={u.rowcount}")

    print(f"验证: 剩余孤儿={s.execute(text(_ORPHAN)).scalar()}(应0) "
          f"剩余market不符={s.execute(text(_MISMATCH)).scalar()}(应0)")
finally:
    s.close()
