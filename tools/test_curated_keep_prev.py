# -*- coding: utf-8 -*-
"""EXEC-R17 单测：refresh_curated.py 的 --keep-prev / --from-tmp / 缩水护栏。

只碰 tempfile 临时目录（桩 parquet），**不读不写** quant_backtest/data 真实数据根；
可独立跑：`python -B tools/test_curated_keep_prev.py`，也可被 pytest 收集。
覆盖：
  1) swap_out 默认行为不变（旧层永久删除，无 .old/.prev 残留）；
  2) swap_out --keep-prev：旧层 rename 为 <out>.prev（内容=上一代 live）；
  3) 第二代 --keep-prev：只保留 1 代 .prev（上一代被轮换掉）；
  4) 回滚：tmp 缺失/old 残骸时 rc=1 且 live 原样；
  5) check_shrink 护栏：表数缩水 / 行数缩水 / 持平通过；
  6) main(--from-tmp --replace --keep-prev) 端到端桩目录：切换成功 + .prev 生成；
  7) main --from-tmp 护栏失败：rc=1、live 不变、tmp 保留、无 .prev；
  8) main --from-tmp 无 tmp：rc=2；
  9) 参数组合约束：--keep-prev 无 --replace / --from-tmp 无 --replace → argparse rc=2。
"""
from __future__ import annotations

import importlib.util
import io
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD_PATH = HERE / "refresh_curated.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("rc_keep_prev_test", str(MOD_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rc = _load_module()
FAILS = []
PASSED = [0]


def check(name, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print("PASS %s" % name)
    else:
        FAILS.append(name)
        print("FAIL %s  %s" % (name, detail))


def _pq():
    import pyarrow as pa
    import pyarrow.parquet as pq
    return pa, pq


def make_layer(dirpath: Path, tables: dict) -> Path:
    """写一份桩 parquet 层：{文件名: 行数}。"""
    import pyarrow as pa
    import pyarrow.parquet as pq
    dirpath.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        pq.write_table(pa.table({"x": list(range(rows))}),
                       str(dirpath / name))
    return dirpath


def files_of(p: Path) -> set:
    return set(f.name for f in p.glob("*") if f.is_file()) if p.is_dir() else set()


class Sandbox:
    """一次用例的独立临时目录 + stdout 静音。"""

    def __enter__(self):
        self.root = Path(tempfile.mkdtemp(prefix="curated_r17_"))
        self.saved = sys.stdout
        sys.stdout = io.StringIO()  # module 的 _say 输出不刷屏
        return self

    def __exit__(self, *exc):
        sys.stdout = self.saved
        shutil.rmtree(self.root, ignore_errors=True)
        return False


def case_swap_default():
    with Sandbox() as sb:
        out = make_layer(sb.root / "out", {"a.parquet": 3})
        tmp = make_layer(sb.root / "out.tmp", {"b.parquet": 5})
        rc_ = rc.swap_out(out, tmp, keep_prev=False)
        check("1a 默认替换 rc=0", rc_ == 0)
        check("1b live=新层", files_of(out) == {"b.parquet"}, files_of(out))
        check("1c 旧层已删（无 .old/.prev）",
              not (sb.root / "out.old").exists()
              and not (sb.root / "out.prev").exists())


def case_swap_keep_prev():
    with Sandbox() as sb:
        out = make_layer(sb.root / "out", {"a.parquet": 3})
        tmp = make_layer(sb.root / "out.tmp", {"b.parquet": 5})
        rc_ = rc.swap_out(out, tmp, keep_prev=True)
        check("2a keep-prev rc=0", rc_ == 0)
        check("2b live=新层", files_of(out) == {"b.parquet"}, files_of(out))
        prev = sb.root / "out.prev"
        check("2c .prev 存在且=旧层", files_of(prev) == {"a.parquet"}, files_of(prev))
        check("2d 无 .old 残留", not (sb.root / "out.old").exists())


def case_swap_keep_prev_rotation():
    with Sandbox() as sb:
        out = make_layer(sb.root / "out", {"g1.parquet": 3})
        make_layer(sb.root / "out.tmp", {"g2.parquet": 4})
        rc_ = rc.swap_out(out, sb.root / "out.tmp", keep_prev=True)
        # 第二代：live(g2) → tmp(g3)
        make_layer(sb.root / "out.tmp", {"g3.parquet": 5})
        rc2 = rc.swap_out(out, sb.root / "out.tmp", keep_prev=True)
        prev = sb.root / "out.prev"
        check("3a 两代 keep-prev 均 rc=0", rc_ == 0 and rc2 == 0, (rc_, rc2))
        check("3b .prev 只保留上一代（=g2）",
              files_of(prev) == {"g2.parquet"}, files_of(prev))
        check("3c live=第三代 g3", files_of(out) == {"g3.parquet"}, files_of(out))
        check("3d 无 .old 残留", not (sb.root / "out.old").exists())


def case_rollback():
    with Sandbox() as sb:
        out = make_layer(sb.root / "out", {"a.parquet": 3})
        rc_ = rc.swap_out(out, sb.root / "missing.tmp", keep_prev=True)
        check("4a tmp 缺失 rc=1", rc_ == 1)
        check("4b live 原样", files_of(out) == {"a.parquet"}, files_of(out))
        check("4c 未产生 .prev/.old",
              not (sb.root / "out.prev").exists()
              and not (sb.root / "out.old").exists())
        (sb.root / "out.tmp").mkdir()
        (sb.root / "out.old").mkdir()  # 模拟上次失败残骸
        rc2 = rc.swap_out(out, sb.root / "out.tmp", keep_prev=True)
        check("4d .old 残骸拒绝替换（rc=1）", rc2 == 1)
        check("4e .old 残骸仍被拒绝且 live 原样",
              rc2 == 1 and files_of(out) == {"a.parquet"})


def case_shrink_guard():
    with Sandbox() as sb:
        out = make_layer(sb.root / "out", {"a.parquet": 3, "b.parquet": 4})
        make_layer(sb.root / "new", {"a.parquet": 3, "b.parquet": 4})
        ok = rc.check_shrink(out, 7, 2)
        check("5a 表/行持平 → 护栏通过", ok == [], ok)
        bad_t = rc.check_shrink(out, 7, 1)
        check("5b 表数缩水 → FAIL", any("表数" in x for x in bad_t), bad_t)
        bad_r = rc.check_shrink(out, 5, 2)
        check("5c 行数缩水 → FAIL", any("行数" in x for x in bad_r), bad_r)


def _main_sandbox(src_dir: Path, out: Path, tmp: Path):
    return rc.main(["--src", str(src_dir), "--out", str(out),
                    "--replace", "--keep-prev", "--from-tmp",
                    "--guard-minutes", "0", "--no-log"])


def case_from_tmp_end_to_end():
    with Sandbox() as sb:
        src = sb.root / "tushare"
        src.mkdir()
        out = make_layer(sb.root / "tushare_curated",
                         {"old_a.parquet": 2, "old_b.parquet": 3})
        tmp = make_layer(sb.root / "tushare_curated.tmp",
                         {"new_a.parquet": 5, "new_b.parquet": 6})
        rc_ = _main_sandbox(src, out, tmp)
        check("6a from-tmp 端到端 rc=0", rc_ == 0, rc_)
        check("6b live 已换成新层", files_of(out) == {"new_a.parquet", "new_b.parquet"},
              files_of(out))
        prev = sb.root / "tushare_curated.prev"
        check("6c .prev=旧层两表", files_of(prev) == {"old_a.parquet", "old_b.parquet"},
              files_of(prev))
        check("6d tmp 已被切走", not tmp.exists())


def case_from_tmp_shrink_blocked():
    with Sandbox() as sb:
        src = sb.root / "tushare"
        src.mkdir()
        out = make_layer(sb.root / "tushare_curated",
                         {"a.parquet": 2, "b.parquet": 3, "c.parquet": 4})
        tmp = make_layer(sb.root / "tushare_curated.tmp",
                         {"a.parquet": 2, "b.parquet": 3})
        rc_ = _main_sandbox(src, out, tmp)
        check("7a 表数缩水 from-tmp rc=1", rc_ == 1, rc_)
        check("7b live 不变", files_of(out) == {"a.parquet", "b.parquet", "c.parquet"},
              files_of(out))
        check("7c tmp 保留", tmp.exists())
        check("7d 未生成 .prev", not (sb.root / "tushare_curated.prev").exists())


def case_from_tmp_missing():
    with Sandbox() as sb:
        src = sb.root / "tushare"
        src.mkdir()
        out = make_layer(sb.root / "tushare_curated", {"a.parquet": 2})
        rc_ = _main_sandbox(src, out, sb.root / "tushare_curated.tmp")
        check("8a tmp 缺失 rc=2", rc_ == 2, rc_)
        check("8b live 不变", files_of(out) == {"a.parquet"})


def case_arg_constraints():
    with Sandbox():
        try:
            rc.main(["--keep-prev"])
            ok1 = False
        except SystemExit as exc:
            ok1 = (exc.code == 2)
        try:
            rc.main(["--from-tmp"])
            ok2 = False
        except SystemExit as exc:
            ok2 = (exc.code == 2)
        try:
            rc.main(["--replace", "--from-tmp", "--dry-run"])
            ok3 = False
        except SystemExit as exc:
            ok3 = (exc.code == 2)
        check("9a --keep-prev 无 --replace → rc=2", ok1)
        check("9b --from-tmp 无 --replace → rc=2", ok2)
        check("9c --from-tmp + --dry-run → rc=2", ok3)


def case_from_tmp_skips_kg_guard():
    """from-tmp 只切换、不读原始层：STAGE 刚写过 kg_sync 日志也不能被守卫误挡。"""
    with Sandbox() as sb:
        src = sb.root / "tushare"
        src.mkdir()
        out = make_layer(sb.root / "tushare_curated", {"old.parquet": 2})
        tmp = make_layer(sb.root / "tushare_curated.tmp", {"new.parquet": 3})
        kg = sb.root / "kg_sync_task.log"
        kg.write_text("just written by STAGE\n", encoding="utf-8")  # mtime=now
        old_kg = rc.KG_LOG
        try:
            rc.KG_LOG = kg
            rc_ = rc.main(["--src", str(src), "--out", str(out), "--replace",
                           "--keep-prev", "--from-tmp", "--guard-minutes", "60",
                           "--no-log"])
        finally:
            rc.KG_LOG = old_kg
        check("10a from-tmp 豁免 kg_sync 活跃守卫 rc=0", rc_ == 0, rc_)
        check("10b live 已切到新层", files_of(out) == {"new.parquet"}, files_of(out))
        check("10c .prev=旧层", files_of(sb.root / "tushare_curated.prev") == {"old.parquet"})


def main() -> int:
    case_swap_default()
    case_swap_keep_prev()
    case_swap_keep_prev_rotation()
    case_rollback()
    case_shrink_guard()
    case_from_tmp_end_to_end()
    case_from_tmp_shrink_blocked()
    case_from_tmp_missing()
    case_from_tmp_skips_kg_guard()
    case_arg_constraints()
    print("\n== EXEC-R17 单测：%d 通过 / %d 失败 ==" %
          (PASSED[0], len(FAILS)))
    if FAILS:
        print("失败用例：" + "，".join(FAILS))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
