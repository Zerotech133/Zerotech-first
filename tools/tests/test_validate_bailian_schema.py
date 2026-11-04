# -*- coding: utf-8 -*-
"""tools/tests/test_validate_bailian_schema.py · 百炼 schema 门禁（task-186）离线测试。

覆盖（6+）：
  1. 合法 SFT（messages + meta）→ PASS / 退出码 0；
  2. 顶层 category（百炼不接受）→ FAIL，code=extra_top_level_keys；
  3. UTF-8 BOM → FAIL，code=bom；
  4. 坏 JSON 行 → FAIL，code=invalid_json；
  5. DPO：合法（messages 末尾 user + chosen/rejected role=assistant）→ PASS；
     chosen.role 非 assistant → FAIL；
  6. 超长行（--max-line 调小）→ FAIL，code=line_too_long；
  7. 角色交替错误（user/user/assistant）→ FAIL，code=message_alternation；
  8. 真实文件冒烟：v4d 通过、raw v4（顶层 category）报错（文件缺失时 skip）。

不联网、不写仓库数据（仅 pytest tmp_path）。
运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_validate_bailian_schema.py
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "finetune"))

import validate_bailian_schema as v  # noqa: E402

SFT_OK = {"messages": [
    {"role": "system", "content": "你是零界量化知识助手。"},
    {"role": "user", "content": "「因子分解」的要点是什么？"},
    {"role": "assistant", "content": "因子分解先剥离共同因子，再讨论剩余部分[1]。"},
], "meta": {"category": "card_concept"}}
DPO_OK = {
    "messages": [
        {"role": "system", "content": "你是投研知识助手。"},
        {"role": "user", "content": "「因子分解」的要点是什么？"},
    ],
    "chosen": {"role": "assistant", "content": "因子分解核心是剥离共同因子[1]。"},
    "rejected": {"role": "assistant", "content": "必涨！满仓买入[9]。"},
}


def _write(tmp_path, rows: list, *, bom: bool = False, name: str = "data.jsonl") -> Path:
    p = tmp_path / name
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    p.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return p


# ------------------------------------------------------------------ 1. 合法 SFT
def test_valid_sft_passes(tmp_path):
    p = _write(tmp_path, [SFT_OK, SFT_OK])
    res = v.validate_file(p, kind="sft")
    assert res["ok"] is True and res["valid_lines"] == 2 and res["invalid_lines"] == 0
    assert res["error_count"] == 0
    assert res["role_counts"]["user"] == 2 and res["role_counts"]["assistant"] == 2
    out = v.summarize(res)
    assert "RESULT: PASS" in out
    assert v.main(["--path", str(p), "--type", "sft"]) == 0
    assert v.main(["--path", str(p), "--type", "sft", "--json"]) == 0


# ------------------------------------------------------------------ 2. 顶层 category
def test_top_level_category_rejected(tmp_path):
    row = {**SFT_OK, "category": "E_variant"}
    p = _write(tmp_path, [row])
    res = v.validate_file(p, kind="sft")
    assert res["ok"] is False
    assert res["errors"][0]["code"] == "extra_top_level_keys"
    assert "category" in res["errors"][0]["message"]
    assert v.main(["--path", str(p), "--type", "sft"]) == 1
    # meta 内部 category 是允许的（v4d 清洗口径）
    p2 = _write(tmp_path, [SFT_OK], name="ok2.jsonl")
    assert v.validate_file(p2, kind="sft")["ok"] is True


# ------------------------------------------------------------------ 3. BOM
def test_utf8_bom_rejected(tmp_path):
    p = _write(tmp_path, [SFT_OK], bom=True)
    res = v.validate_file(p, kind="sft")
    assert res["ok"] is False and any(e["code"] == "bom" for e in res["errors"])
    assert v.main(["--path", str(p), "--type", "sft"]) == 1


# ------------------------------------------------------------------ 4. 坏 JSON
def test_bad_json_line(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text('{"messages": [} \n' + json.dumps(SFT_OK, ensure_ascii=False) + "\n", encoding="utf-8")
    res = v.validate_file(p, kind="sft")
    assert res["ok"] is False and any(e["code"] == "invalid_json" for e in res["errors"])
    assert res["invalid_lines"] == 1 and res["valid_lines"] == 1


# ------------------------------------------------------------------ 5. DPO
def test_dpo_schema(tmp_path):
    p = _write(tmp_path, [DPO_OK])
    res = v.validate_file(p, kind="dpo")
    assert res["ok"] is True
    assert res["role_counts"].get("chosen:assistant") == 1
    # chosen 角色错
    bad = {**DPO_OK, "chosen": {"role": "user", "content": "x"}}
    p2 = _write(tmp_path, [bad], name="bad_dpo.jsonl")
    res2 = v.validate_file(p2, kind="dpo")
    assert res2["ok"] is False and any(e["code"] == "chosen_role" for e in res2["errors"])
    # DPO 不接受 prompt/source 等额外顶层字段
    bad2 = {**DPO_OK, "prompt": "问题", "source": "x"}
    p3 = _write(tmp_path, [bad2], name="bad_dpo2.jsonl")
    errs = v.validate_file(p3, kind="dpo")["errors"]
    assert any(e["code"] == "extra_top_level_keys" and "prompt" in e["message"] for e in errs)


# ------------------------------------------------------------------ 6. 超长行
def test_line_too_long(tmp_path):
    long_row = copy.deepcopy(SFT_OK)
    long_row["messages"][1]["content"] = "长" * 500
    p = _write(tmp_path, [long_row])
    res = v.validate_file(p, kind="sft", max_line=200)
    assert res["ok"] is False and any(e["code"] == "line_too_long" for e in res["errors"])
    assert res["max_line_chars"] > 200


# ------------------------------------------------------------------ 7. 角色交替
def test_message_alternation(tmp_path):
    bad = {"messages": [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a"},
    ]}
    p = _write(tmp_path, [bad])
    errs = v.validate_file(p, kind="sft")["errors"]
    assert any(e["code"] == "message_alternation" for e in errs)
    # SFT 必须以 assistant 结尾
    tail_bad = {"messages": [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "u2"},
    ]}
    p2 = _write(tmp_path, [tail_bad], name="tail.jsonl")
    assert any(e["code"] == "missing_assistant" for e in v.validate_file(p2, kind="sft")["errors"])
    # meta 非 object
    p3 = _write(tmp_path, [{**SFT_OK, "meta": "str"}], name="meta.jsonl")
    assert any(e["code"] == "meta_not_object" for e in v.validate_file(p3, kind="sft")["errors"])


# ------------------------------------------------------------------ 8. 真实 v4/v4d 冒烟
def test_real_v4_files_detection():
    sft = ROOT / "research" / "finetune" / "sft"
    v4d = sft / "quant_kb_sft_v4d_train.jsonl"
    v4 = sft / "quant_kb_sft_v4_train.jsonl"
    if not v4d.is_file() or not v4.is_file():
        pytest.skip("v4/v4d 数据文件不存在")
    res_ok = v.validate_file(v4d, kind="sft")
    assert res_ok["ok"] is True, res_ok["errors"][:3]
    assert res_ok["lines_nonempty"] > 1000
    res_bad = v.validate_file(v4, kind="sft")
    assert res_bad["ok"] is False
    assert any(e["code"] == "extra_top_level_keys" and "category" in e["message"]
               for e in res_bad["errors"])
    assert res_bad["top_key_counts"].get("category", 0) > 0
    assert res_bad["top_key_counts"].get("messages") == res_bad["lines_nonempty"]
