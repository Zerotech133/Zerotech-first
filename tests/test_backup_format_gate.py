# -*- coding: utf-8 -*-
r"""W-06 · ops/backup_format_gate.py 单元测试（qa-tester 单写者落盘）。

【委派】team-lead ADR-08 修订委派 W-06；只新建本文件，不改任何既有文件。
【上游】PRD 最终版 AC-02（F-04 坏档隔离 + 格式闸门设计交付）、治理方案 §三阶段 2、
        全栈研发师《技术研发方案》§9.3 单测清单（F2 侧 9 条）、FF06 §5.2 接口契约、
        qa-testing.md §三用例模板 / §四 Bug 规范 / §五质量门禁。
【解释器】"C:\Program Files\Python311\python.exe" -X utf8
【硬边界】六类夹具全部由 pytest tmp_path 运行时自建，含 AIOS-SELF-BUILT 标记；
        encrypted_aes 用「Salted__ + 伪 IV + 随机密文 + 伪 HMAC」合成，
        严禁读 C:\backups_mirror 真实 .enc 或 backups/db 生产明文 dump（FF11）；
        任何测试都不写 C:\aios 下既有文件、不执行 .enc 解密、不整文件解压 11GB 产物。

四层覆盖映射：
  功能：BG-N01~N10（等价类 6 类夹具 / 边界值 0B·截断·坏CRC / 场景法 gate 链 /
        状态迁移 ok 真值表 4 组合）
  兼容：BG-N08 常量声明（8MiB 探针 / 1MiB chunk 为设计预算，G0-b 不实测不设阈值）
  性能：BG-N09 只核「probe_bytes ≤ PROBE_BYTES、不超过文件实际字节」这一上界，
        不跑真实计时压测
  安全：BG-E05（.enc 不误判 gzip、绝不解密）、BG-E06（I/O 异常一律收敛 ok=false
        不抛穿 main）、BG-E10（FF11 夹具来源反向断言）

用例清单（编号 / 优先级 / 溯源）：
  BG-N01 P0 六类夹具 verdict（6 参数化）      BG-N02 P0 valid gzip ok=true + T-10 A 全量 sha256
  BG-N03 P0 detect_format 5 合法值           BG-N04 P0 FormatVerdict 九字段
  BG-N05 P0 gate_entry 保留 5 legacy 键       BG-N06 P0 should_select 接受合法 gzip
  BG-N07 P0 quarantine flag 八字段（C-1）     BG-N08 P1 常量/探针上界（设计预算声明）
  BG-N09 P0 sha256 口径 = 产物整体压缩字节   BG-N10 P0 main verify rc 0/1
  BG-E01 P0 0 字节 → empty                    BG-E02 P0 截断 → stream probe false
  BG-E03 P0 坏 CRC → CRC 校验失败            BG-E04 P0 2d2d 纯 SQL（10-29 永久回归夹具）
  BG-E05 P0 合成 .enc 不误判 gzip            BG-E06 P0 I/O 异常收敛 ok=false 不抛穿
  BG-E07 P0 假绿：gate_entry 真值表 + main rc BG-E08 P0 apply=False 默认不落盘
  BG-E09 P0 flag 八字段严格对齐 / manifest_patch 不混入
  BG-E10 P0 FF11 夹具反向断言（AIOS-SELF-BUILT / 无生产明文 / 全在 tmp_path）
  BG-N11 P0 BUG-04 坏档 sha256 必填         BG-N12 P0 BUG-04 单次 8B 读头（防 TOCTOU）
  BG-N13 P0 BUG-05 apply 合并 files          BG-N14 P0 BUG-05 类型冲突拒绝覆盖

异常流 10 条 ≥ 正常流 14 条的一半（7），满足 qa-testing.md 错误预防条款。
"""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import os
import sys

import pytest

sys.path.insert(0, r"C:\aios\ops")
import backup_format_gate as bfg  # noqa: E402

MARKER = b"-- AIOS-SELF-BUILT synthetic fixture (no production dump)\nSELECT 1;\n"
SELF_BUILT = b"AIOS-SELF-BUILT"


def build_fixtures(tmp_path):
    """六类 gzip 夹具，全部 tmp_path 运行时自建，返回 {name: path}（FF11）。"""
    d = tmp_path / "artifacts"
    d.mkdir(exist_ok=True)
    f = {}

    p = d / "valid.sql.gz"
    p.write_bytes(gzip.compress(MARKER))
    f["valid"] = p

    p = d / "plain_sql_2d2d.gz"
    p.write_bytes(MARKER)                      # 首 2 字节 '--' → 2d2d
    f["plain_sql_2d2d"] = p

    p = d / "truncated.sql.gz"
    p.write_bytes(gzip.compress(MARKER)[:12])  # 保留 magic，截断正文/trailer
    f["truncated"] = p

    p = d / "zero_byte"
    p.write_bytes(b"")
    f["zero_byte"] = p

    raw = bytearray(gzip.compress(MARKER))
    raw[-5] ^= 0xFF                            # 破坏 CRC32（trailer）
    p = d / "bad_crc"
    p.write_bytes(bytes(raw))
    f["bad_crc"] = p

    # 合成 .enc：Salted__ + 伪 IV(8) + 随机密文(24) + 伪 HMAC(32)，
    # 绝不触碰 C:\backups_mirror 的真实密钥/密文。
    p = d / "encrypted_aes"
    p.write_bytes(b"Salted__" + os.urandom(8) + os.urandom(24) + os.urandom(32))
    f["encrypted_aes"] = p
    return f


@pytest.fixture(scope="function")
def artifacts(tmp_path):
    return build_fixtures(tmp_path)


def _quiet_main(argv):
    with contextlib.redirect_stdout(io.StringIO()):
        return bfg.main(argv)


# --------------------------------------------------------------------------
# 正常流（N）
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name,exp_fmt,exp_ok,exp_rec", [
    ("valid", "gzip", True, True),
    ("plain_sql_2d2d", "plain-sql", False, False),
    ("truncated", "gzip", False, False),
    ("zero_byte", "empty", False, False),
    ("bad_crc", "gzip", False, False),
    ("encrypted_aes", "encrypted-aes", False, False),
])
def test_BG_N01_six_fixtures_verdict(artifacts, name, exp_fmt, exp_ok, exp_rec):
    """用例编号 BG-N01｜优先级 P0｜前置：六类自建夹具｜步骤：verify_artifact｜
    预期：format/ok/recoverable 逐类命中；**无一例外 ok=false 除非合法 gzip**｜
    溯源：AC-02 / FF06 §5.2 / 全栈《技术研发方案》§9.3。
    备注（ADR-30 后）：verify_artifact() 对外 v["magic"] 仍为 2 字节契约
    （'Salted__' 前两字节 'Sa' → '5361'），但另读 8 字节头喂 detect_format()，
    故 encrypted-aes 分支可达、format 应为 'encrypted-aes'。
    BG-E05 用完整 magic 直接调 detect_format 交叉验证。reason 为自由文本不断言枚举。"""
    v = bfg.verify_artifact(str(artifacts[name]))
    assert v["format"] == exp_fmt, name
    assert v["ok"] is exp_ok and v["recoverable"] is exp_rec, name
    assert v["format"] in bfg.VALID_FORMATS
    assert set(v) == {"ok", "format", "magic", "reason", "recoverable",
                      "size_bytes", "sha256", "probe_bytes", "checked_at"}
    # 假绿总闸：ok=true 当且仅当 magic=1f8b 且 format=gzip 且 recoverable=true
    if v["ok"]:
        assert v["magic"] == "1f8b" and v["format"] == "gzip" and v["recoverable"] is True
    else:
        assert v["magic"] != "1f8b" or v["format"] == "gzip"


def test_BG_N02_valid_gzip_ok_true_and_sha256_T10A(artifacts):
    """用例编号 BG-N02｜优先级 P0｜前置：合法 gzip 夹具｜步骤：verify_artifact｜
    预期：ok=true、magic=1f8b、format=gzip、recoverable=true、sha256 非空且等于
    **产物文件整体压缩字节**的 sha256（T-10 终裁采纳 A 全量，非解压后 SQL 明文）｜
    溯源：AC-02 / T-10 / G-09b。"""
    p = artifacts["valid"]
    v = bfg.verify_artifact(str(p))
    assert v["ok"] is True and v["magic"] == "1f8b" and v["format"] == "gzip"
    assert v["recoverable"] is True
    assert v["sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()
    assert v["sha256"] != hashlib.sha256(MARKER).hexdigest()  # 不是解压后明文
    assert v["size_bytes"] == p.stat().st_size
    assert "magic+stream ok" in v["reason"]


@pytest.mark.parametrize("magic,size,exp", [
    (b"\x1f\x8b", 100, "gzip"),
    (b"--", 100, "plain-sql"),
    (b"Salted__", 100, "encrypted-aes"),
    (b"", 0, "empty"),
    (b"PK\x03\x04", 100, "unknown"),
])
def test_BG_N03_detect_format_five_values(magic, size, exp):
    """用例编号 BG-N03｜优先级 P0｜前置：直调 detect_format｜步骤：五种魔数/大小｜
    预期：恰返回 5 合法值之一（gzip/plain-sql/encrypted-aes/empty/unknown）｜
    溯源：AC-02 / FF06 §5.2 纠正（5 值枚举，非自造）。"""
    assert bfg.detect_format(magic, size) == exp
    assert bfg.detect_format(magic, size) in bfg.VALID_FORMATS


def test_BG_N04_format_verdict_nine_fields(artifacts):
    """用例编号 BG-N04｜优先级 P0｜前置：合法.gz｜步骤：verify_artifact 全字段｜
    预期：九字段齐备、magic 为无空格小写 hex、checked_at 非空｜
    溯源：AC-02 / FF06 §5.2 接口契约。"""
    v = bfg.verify_artifact(str(artifacts["valid"]))
    assert len(v) == 9
    assert v["magic"] == v["magic"].lower() and " " not in v["magic"]
    assert v["checked_at"] and isinstance(v["reason"], str)
    assert isinstance(v["size_bytes"], int) and isinstance(v["probe_bytes"], int)
    assert isinstance(v["sha256"], str) and isinstance(v["recoverable"], bool)


def test_BG_N05_gate_entry_preserves_legacy_keys(artifacts):
    """用例编号 BG-N05｜优先级 P0｜前置：含 5 legacy 键的 manifest item + 合法.gz｜
    步骤：gate_entry｜预期：返回 NEW dict，保留 key/ok/size/file/must 且只增 6 键
    （format/magic/sha256/recoverable/checked_at/reason），不改入参｜
    溯源：AC-02 / FF06 A7 只增键约定。"""
    item = {"key": "quant", "ok": True, "size": 1, "file": "valid.sql.gz", "must": True}
    snap = dict(item)
    out = bfg.gate_entry(item, str(artifacts["valid"].parent))
    assert out is not item
    assert item == snap, "gate_entry 不得改入参"
    for k in bfg.LEGACY_KEYS:
        assert k in out
    for k in ("format", "magic", "sha256", "recoverable", "checked_at", "reason"):
        assert k in out
    assert out["ok"] is True and out["format"] == "gzip"


@pytest.mark.parametrize("name", ["valid"])
def test_BG_N06_should_select_accepts_valid_gzip(artifacts, name):
    """用例编号 BG-N06｜优先级 P0｜前置：合法 gzip verdict｜步骤：should_select｜
    预期：True（唯一判据 ok AND format=gzip AND recoverable）｜溯源：AC-02。"""
    assert bfg.should_select(bfg.verify_artifact(str(artifacts[name]))) is True


def test_BG_N07_quarantine_flag_eight_fields(artifacts):
    """用例编号 BG-N07｜优先级 P0｜前置：2d2d verdict｜步骤：
    write_quarantine_flag(apply=False) 取 flag_content｜预期：八字段齐备且对齐
    研发契约 C-1（type/date/files/format/recoverable/detected_magic/policy/
    decision_ref=D4），recoverable=false、detected_magic=2d2d｜溯源：AC-02 / C-1。
    备注（ADR-28 终裁）：policy 权威值 = "mark-only, do-not-delete"（逗号+空格），
    即 F2 write_quarantine_flag() 实现值、W-08 落盘实物值、W-10/11/12 写入值。
    散文 "mark-only-do-not-delete" 只是行文，不是 JSON 值；此处按权威值硬断言。"""
    v = bfg.verify_artifact(str(artifacts["plain_sql_2d2d"]))
    day = artifacts["plain_sql_2d2d"].parent / "2026-10-29"
    r = bfg.write_quarantine_flag(str(day), "plain_sql_2d2d.gz", v, apply=False)
    c = r["flag_content"]
    assert set(c) == {"type", "date", "files", "format", "recoverable",
                      "detected_magic", "policy", "decision_ref"}
    assert c["type"] == "invalid_gzip_quarantine" and c["date"] == "2026-10-29"
    assert c["files"] == ["plain_sql_2d2d.gz"]
    assert c["format"] == "plain-sql" and c["recoverable"] is False
    assert c["detected_magic"] == "2d2d" and c["decision_ref"] == "D4"
    assert c["policy"] == "mark-only, do-not-delete"


def test_BG_N08_constants_and_probe_budget_are_design_only():
    """用例编号 BG-N08｜优先级 P1｜前置：模块常量｜步骤：核对｜预期：
    GZIP_MAGIC=1f8b、PROBE_BYTES=8<<20、SHA256_CHUNK=1<<20、VALID_FORMATS 5 值；
    这是 G0-b「性能 6 项设计预算声明」的静态核对，**不实测、不设阈值**｜
    溯源：AC-02 / G0-b / 全栈非功能基线。"""
    assert bfg.GZIP_MAGIC == b"\x1f\x8b"
    assert bfg.PROBE_BYTES == 8 << 20
    assert bfg.SHA256_CHUNK == 1 << 20
    assert bfg.VALID_FORMATS == ("gzip", "plain-sql", "encrypted-aes", "empty", "unknown")


def test_BG_N09_sha256_is_full_artifact_and_probe_bounded(artifacts):
    """用例编号 BG-N09｜优先级 P0｜前置：合法.gz｜步骤：sha256_of + probe_bytes｜
    预期：sha256_of == 整体压缩字节 sha256；probe_bytes == min(probe,size)，
    不超过 8MiB 且不超过文件实际字节（不多读）｜溯源：AC-02 / T-10 A。"""
    p = artifacts["valid"]
    v = bfg.verify_artifact(str(p))
    assert bfg.sha256_of(str(p)) == hashlib.sha256(p.read_bytes()).hexdigest()
    assert v["probe_bytes"] == min(bfg.PROBE_BYTES, p.stat().st_size)
    assert v["probe_bytes"] <= bfg.PROBE_BYTES
    # compute_sha256=False 时 sha256 留空（快速探针设计预算）
    v2 = bfg.verify_artifact(str(p), compute_sha256=False)
    assert v2["ok"] is True and v2["sha256"] == ""


@pytest.mark.parametrize("name,exp_rc", [("valid", 0), ("plain_sql_2d2d", 1),
                                         ("zero_byte", 1), ("bad_crc", 1)])
def test_BG_N10_main_verify_exit_codes(artifacts, name, exp_rc):
    """用例编号 BG-N10｜优先级 P0｜前置：各类夹具｜步骤：main(['verify',path])｜
    预期：合法 gzip→rc=0，任何坏档→rc=1（绝不 rc=0 假绿）｜
    溯源：AC-02 / 退出码映射 B 册 / FF06 §3.2 契约 4。"""
    assert _quiet_main(["verify", str(artifacts[name])]) == exp_rc


# --------------------------------------------------------------------------
# 异常流（E）
# --------------------------------------------------------------------------

def test_BG_E01_zero_byte(artifacts):
    """用例编号 BG-E01｜优先级 P0｜前置：0 字节文件｜步骤：verify_artifact｜
    预期：format=empty、magic=''、size_bytes=0、ok=false、recoverable=false｜
    溯源：AC-02 / qa-testing.md 边界值 0。"""
    v = bfg.verify_artifact(str(artifacts["zero_byte"]))
    assert v["format"] == "empty" and v["magic"] == ""
    assert v["size_bytes"] == 0 and v["ok"] is False and v["recoverable"] is False


def test_BG_E02_truncated_gzip_stream_probe_false(artifacts):
    """用例编号 BG-E02｜优先级 P0｜前置：截断 gzip（保留 1f8b）｜步骤：verify_artifact｜
    预期：magic=1f8b、format=gzip、stream probe 失败 → ok=false、recoverable=false｜
    溯源：AC-02 / 全栈《技术研发方案》§9.3。"""
    v = bfg.verify_artifact(str(artifacts["truncated"]))
    assert v["magic"] == "1f8b" and v["format"] == "gzip"
    assert v["ok"] is False and v["recoverable"] is False
    assert "gzip stream probe failed" in v["reason"]


def test_BG_E03_bad_crc(artifacts):
    """用例编号 BG-E03｜优先级 P0｜前置：CRC 被破坏的 gzip｜步骤：verify_artifact｜
    预期：CRC 校验失败 → ok=false、recoverable=false（不静默标绿）｜
    溯源：AC-02 / qa-testing.md 异常流。"""
    v = bfg.verify_artifact(str(artifacts["bad_crc"]))
    assert v["magic"] == "1f8b" and v["ok"] is False and v["recoverable"] is False
    assert "probe failed" in v["reason"]


def test_BG_E04_plain_sql_2d2d_permanent_regression_fixture(artifacts):
    """用例编号 BG-E04｜优先级 P0｜前置：10-29 同型 2d2d 纯 SQL 自建夹具｜
    步骤：verify_artifact｜预期：format=plain-sql、detected magic=2d2d、
    recoverable=false、ok=false；reason 明确 magic mismatch 且标注 non-gzip payload｜
    溯源：AC-02 / 治理方案「10-29 永久回归夹具」/ ADR-05。"""
    v = bfg.verify_artifact(str(artifacts["plain_sql_2d2d"]))
    assert v["format"] == "plain-sql" and v["magic"] == "2d2d"
    assert v["ok"] is False and v["recoverable"] is False
    assert "magic mismatch" in v["reason"] and "non-gzip" in v["reason"]
    assert bfg.should_select(v) is False
    # C-1：绝不删除/改写/重压缩 —— 夹具字节在验证前后完全一致
    before = artifacts["plain_sql_2d2d"].read_bytes()
    bfg.verify_artifact(str(artifacts["plain_sql_2d2d"]))
    assert artifacts["plain_sql_2d2d"].read_bytes() == before


def test_BG_E05_synthetic_enc_never_misread_as_gzip(artifacts):
    """用例编号 BG-E05｜优先级 P0｜前置：合成 .enc（Salted__+伪IV+随机密文+伪HMAC）｜
    步骤：verify_artifact + detect_format(完整 magic)｜预期：verify_artifact 绝不
    误判为 gzip、ok=false、recoverable=false、should_select=false；detect_format 以
    完整 'Salted__' 前缀返回 encrypted-aes；全程**零解密调用**｜
    溯源：AC-02 / FF06 §5.2（encrypted-aes 启发式、绝不调用任何解密）/ FF11。"""
    p = artifacts["encrypted_aes"]
    raw = p.read_bytes()
    assert raw.startswith(b"Salted__") and len(raw) >= 49
    v = bfg.verify_artifact(str(p))
    assert v["format"] != "gzip" and v["ok"] is False and v["recoverable"] is False
    assert bfg.should_select(v) is False
    assert bfg.detect_format(raw[:8], len(raw)) == "encrypted-aes"
    src = io.open(r"C:\aios\ops\backup_format_gate.py", encoding="utf-8").read().lower()
    for forbidden in ("decrypt", "unprotect-file", "aescrypt", "cipher("):
        assert forbidden not in src, "F2 出现解密调用：%s" % forbidden
    assert p.read_bytes() == raw  # 只读，未改写


@pytest.mark.parametrize("patch_target", ["read_magic", "sha256_of", "stream_probe"])
def test_BG_E06_all_io_exceptions_converge_to_ok_false(monkeypatch, artifacts, patch_target):
    """用例编号 BG-E06｜优先级 P0｜前置：monkeypatch 让 read_magic / sha256_of /
    stream_probe 抛 OSError｜步骤：verify_artifact｜预期：任何 I/O 异常一律收敛为
    ok=false 的 verdict，**绝不抛穿 main 造成「没写 manifest 但 rc=0」**｜
    溯源：AC-02 / FF06 §5.2 / qa-testing.md「异常不得产 ok=true」。"""
    def boom(*a, **k):
        raise OSError("QA injected I/O failure")
    monkeypatch.setattr(bfg, patch_target, boom)
    v = bfg.verify_artifact(str(artifacts["valid"]))
    assert v["ok"] is False and v["recoverable"] is False
    assert isinstance(v, dict) and "reason" in v
    assert _quiet_main(["verify", str(artifacts["valid"])]) == 1


@pytest.mark.parametrize("legacy_ok,fixture,exp_ok", [
    (True, "valid", True), (True, "plain_sql_2d2d", False),
    (False, "valid", False), (False, "plain_sql_2d2d", False),
])
def test_BG_E07_fake_green_gate_entry_truth_table(artifacts, legacy_ok, fixture, exp_ok):
    """用例编号 BG-E07｜优先级 P0｜前置：旧 ok × verdict.ok 四组合｜步骤：
    gate_entry｜预期：out.ok = 旧ok AND v.ok —— 尤其「旧 ok=true 但 v.ok=false」
    必须收敛为 false，**rc=0 但 ok=false 的假绿绝不能被翻回 true**｜
    溯源：AC-02 / FF06 §3.2 契约 4 / qa-testing.md 安全层。"""
    item = {"key": "k", "ok": legacy_ok, "size": 1, "file": "%s.gz" % fixture, "must": True}
    # 用固定文件名映射，确保 gate_entry 能在 out_dir 找到对应产物
    d = artifacts[fixture].parent
    (d / "valid.gz").write_bytes(artifacts["valid"].read_bytes())
    (d / "plain_sql_2d2d.gz").write_bytes(artifacts["plain_sql_2d2d"].read_bytes())
    item["file"] = "%s.gz" % fixture
    out = bfg.gate_entry(item, str(d))
    assert out["ok"] is exp_ok
    if legacy_ok is True and exp_ok is False:
        assert out["format"] == "plain-sql" and out["recoverable"] is False


def test_BG_E08_apply_false_default_never_writes(tmp_path):
    """用例编号 BG-E08｜优先级 P0｜前置：tmp day 目录｜步骤：
    write_quarantine_flag 默认调用 + 显式 apply=False + 检查签名默认值｜
    预期：签名默认 apply=False；结果 apply=False、无 'written' 键；目标
    flag 文件不存在；flag 父目录不被创建 —— 杜绝「apply=False 被误改成 True」｜
    溯源：AC-02 / ADR-03/05「设计完成 != 运行态生效」/ qa-testing.md 异常流。"""
    import inspect
    assert inspect.signature(bfg.write_quarantine_flag).parameters["apply"].default is False
    day = tmp_path / "2026-10-29"
    v = {"format": "plain-sql", "magic": "2d2d", "recoverable": False}
    r1 = bfg.write_quarantine_flag(str(day), "x.sql.gz", v)
    r2 = bfg.write_quarantine_flag(str(day), "x.sql.gz", v, apply=False)
    for r in (r1, r2):
        assert r["apply"] is False and "written" not in r
        assert r["flag_path"] == str(day / "INVALID_GZIP.flag")
    assert not (day / "INVALID_GZIP.flag").exists()
    assert not day.exists(), "apply=False 连目录都不该创建"


def test_BG_E09_flag_content_strict_and_no_manifest_patch(artifacts):
    """用例编号 BG-E09｜优先级 P0｜前置：2d2d verdict｜步骤：
    write_quarantine_flag(apply=False) 全结果｜预期：flag_content 严格八字段；
    manifest_patch 只存在于 result 顶层、**不混入 flag_content**（禁改既有
    manifest.json）；apply=False 不落盘、不改 manifest｜
    溯源：AC-02 / C-1 / ADR-05「只新增 flag，禁改 manifest」。"""
    v = bfg.verify_artifact(str(artifacts["plain_sql_2d2d"]))
    day = artifacts["plain_sql_2d2d"].parent / "2026-10-29"
    r = bfg.write_quarantine_flag(str(day), "plain_sql_2d2d.gz", v, apply=False)
    assert "manifest_patch" in r and "manifest_patch" not in r["flag_content"]
    assert r["manifest_patch"] == {"format": "plain-sql", "recoverable": False}
    assert "written" not in r
    assert set(r["flag_content"]) == {
        "type", "date", "files", "format", "recoverable",
        "detected_magic", "policy", "decision_ref"}
    assert not (day / "INVALID_GZIP.flag").exists()


def test_BG_E10_ff11_fixture_provenance_reverse_assertion(artifacts, tmp_path):
    """用例编号 BG-E10｜优先级 P0｜前置：六类运行时自建夹具｜步骤：逐一读字节
    + 校验路径归属｜预期：所有夹具都在 pytest tmp_path 下；文本型夹具含
    AIOS-SELF-BUILT 标记；**整批字节不含生产明文 SQL / `-- pg_dump` / 生产库名 /
    连接串**；无一个夹具来自 backups/db 或 C:\\backups_mirror｜
    溯源：FF11 / AC-E03 / qa-testing.md 安全层。"""
    production_markers = [b"pg_dump", b"cainiao_mysql", b"zhice_os_v2",
                          b"quant_pg_alldb", b"postgresql://", b"mysql://", b"password="]
    for name, p in artifacts.items():
        assert str(tmp_path) in str(p), "%s 不在 tmp_path 下" % name
        assert "backups" not in str(p).lower(), "%s 疑似来自生产目录" % name
        raw = p.read_bytes()
        for m in production_markers:
            assert m not in raw, "%s 含生产标记 %r" % (name, m)
    # 文本型 / 可解压型夹具携带自建标记
    assert SELF_BUILT in artifacts["plain_sql_2d2d"].read_bytes()
    assert SELF_BUILT in gzip.decompress(artifacts["valid"].read_bytes())
    # encrypted_aes 是纯合成字节，不含任何 SQL 明文
    assert b"SELECT" not in artifacts["encrypted_aes"].read_bytes()


# ---------------------------------------------------------------------------
# 2026-11-06 代码审计回归（BUG-04 / BUG-05）
# ---------------------------------------------------------------------------

def test_BG_N11_bad_artifact_sha256_present_when_requested(artifacts):
    """BG-N11 P0｜BUG-04 回归：坏档在 compute_sha256=True 时必须带指纹。

    隔离留痕与 FF05 回滚取证恰恰最依赖坏档指纹；原实现 magic mismatch 分支
    提前 return，sha256 恒为空串，违背函数契约。T-10 口径 A（产物压缩字节）。
    """
    p = artifacts["plain_sql_2d2d"]
    v = bfg.verify_artifact(str(p))
    assert v["ok"] is False and v["format"] == "plain-sql"
    assert v["sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()
    # compute_sha256=False 的快速探针仍须留空（BG-N09 契约不回归）
    assert bfg.verify_artifact(str(p), compute_sha256=False)["sha256"] == ""


def test_BG_N12_verify_reads_header_once(monkeypatch, artifacts):
    """BG-N12 P0｜BUG-04 回归：只读一次 8 字节头，杜绝双 open 的 TOCTOU 面。"""
    calls = []
    orig = bfg.read_magic

    def spy(path, n=2):
        calls.append(n)
        return orig(path, n)

    monkeypatch.setattr(bfg, "read_magic", spy)
    v = bfg.verify_artifact(str(artifacts["encrypted_aes"]))
    assert calls == [8], "verify_artifact 必须只读一次 8 字节头：%r" % (calls,)
    assert v["magic"] == "5361" and v["format"] == "encrypted-aes"


def test_BG_N13_apply_merges_existing_flag_files(tmp_path, artifacts):
    """BG-N13 P0｜BUG-05 回归：apply=True 合并已有 flag，不静默覆盖留痕。"""
    day = tmp_path / "2026-10-29"
    v = bfg.verify_artifact(str(artifacts["plain_sql_2d2d"]))
    bfg.write_quarantine_flag(str(day), "a.sql.gz", v, apply=True)
    r = bfg.write_quarantine_flag(str(day), "b.sql.gz", v, apply=True)
    flag = day / "INVALID_GZIP.flag"
    c = json.loads(flag.read_text(encoding="utf-8"))
    assert c["files"] == ["a.sql.gz", "b.sql.gz"]
    assert c["policy"] == "mark-only, do-not-delete"
    assert r["apply"] is True and r["written"] == str(flag)
    # 重复同名去重；首档权威字段不被后一档覆盖
    bfg.write_quarantine_flag(str(day), "a.sql.gz", v, apply=True)
    c2 = json.loads(flag.read_text(encoding="utf-8"))
    assert c2["files"] == ["a.sql.gz", "b.sql.gz"]


def test_BG_N14_apply_refuses_type_conflict(tmp_path):
    """BG-N14 P0｜BUG-05 回归：类型冲突拒绝覆盖，已有留痕字节不变。"""
    day = tmp_path / "2026-11-06"
    day.mkdir()
    flag = day / "INVALID_GZIP.flag"
    before = json.dumps({"type": "other_quarantine", "files": ["x"]})
    flag.write_text(before, encoding="utf-8")
    with pytest.raises(ValueError, match="类型冲突"):
        bfg.write_quarantine_flag(str(day), "a.sql.gz", {}, apply=True)
    assert json.loads(flag.read_text(encoding="utf-8"))["files"] == ["x"]
