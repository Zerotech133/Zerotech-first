# -*- coding: utf-8 -*-
"""org_command_api 单测（K-ORG-190 拍板落地验收）

铁律：所有用例跑在 tempfile 隔离目录，**绝不触真实 registry**（write_snapshot 判例：
写落盘函数不得默认绑定真实目录——本模块 STATION 仅作缺省参数，测试显式传 tmp）。
运行：python portal/test_org_command_api.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import org_command_api as oca  # noqa: E402


def make_station(tmp):
    """最小注册表 fixtures：chief + 1 部门（dev，1 岕 D1）+ 档位表 + 技能库。"""
    reg = os.path.join(tmp, "agent_platform", "registry")
    os.makedirs(reg, exist_ok=True)
    os.makedirs(os.path.join(tmp, "agent_platform", "prompts"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "portal"), exist_ok=True)
    org = {
        "org": "laotian_ai", "report_to": "human",
        "chief": {"agent_id": "A0", "role_profile": "a0_chief",
                  "llm_binding": "glm/glm-5.3", "grade": "E3",
                  "name": "田丰", "attributes": {}},
        "departments": [
            {"dept": "dev", "name": "研发部", "status": "active",
             "charter": "写代码", "agents": [
                 {"agent_id": "D1", "role_profile": "dev_engineer",
                  "name": "老D", "grade": "E2", "level": 2,
                  "node_key": "d1", "parent_node_keys": [],
                  "llm_binding": "glm/glm-5.3",
                  "tools": ["devtask"],
                  "attributes": {"experience": "20年", "specialties": ["后端"],
                                 "knowledge": []}}],
             "teams": []},
        ],
    }
    with open(os.path.join(reg, "org.json"), "w", encoding="utf-8") as f:
        json.dump(org, f, ensure_ascii=False)
    tiers = {
        "models": {
            "glm/glm-5.3": {"tier": "strong", "enabled": True},
            "glm/glm-5.3-flash": {"tier": "fast", "enabled": True},
            "qwen3.5:9b": {"tier": "local", "enabled": True},
            "claude/claude-sonnet": {"tier": "strong", "enabled": False},
        },
        "grade_policy": {
            "E3": {"allow": ["strong"], "default": "glm/glm-5.3"},
            "E2": {"allow": ["strong", "fast"], "default": "glm/glm-5.3"},
            "E1": {"allow": ["fast", "local"], "default": "glm/glm-5.3-flash"},
        },
    }
    with open(os.path.join(reg, "model_tiers.json"), "w", encoding="utf-8") as f:
        json.dump(tiers, f, ensure_ascii=False)
    skills = {"skills": [
        {"id": "devtask", "kind": "tool", "enabled": True},
        {"id": "quant_dual", "kind": "algo", "enabled": True},
    ]}
    with open(os.path.join(reg, "skill_registry.json"), "w", encoding="utf-8") as f:
        json.dump(skills, f, ensure_ascii=False)
    return tmp


class OrgCommandApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="org_api_test_")
        make_station(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _org(self):
        with open(os.path.join(self.tmp, "agent_platform", "registry",
                               "org.json"), encoding="utf-8") as f:
            return json.load(f)

    # ---- /api/org 数据面 ----

    def test_bundle_shape(self):
        j = oca.api_org(self.tmp)
        self.assertTrue(j["ok"])
        b = j["bundle"]
        self.assertIn("A0", b["agents"])
        self.assertIn("D1", b["agents"])
        self.assertEqual(b["agents"]["D1"]["llm"], "glm/glm-5.3")
        self.assertEqual(b["agents"]["D1"]["tools"], ["devtask"])
        devs = [d for d in b["departments"] if d["id"] == "dev"]
        self.assertEqual(devs[0]["agents"], ["D1"])
        self.assertEqual(b["stats"]["agents"], 2)

    def test_bundle_chief_charter_and_prompt_none_ok(self):
        j = oca.api_org(self.tmp)
        a0 = j["bundle"]["agents"]["A0"]
        self.assertEqual(a0["dept"], "chief")
        self.assertIsNone(a0["prompt"])  # 无 prompts 文件时诚实 None，不假造

    # ---- 拍板①：引用不内嵌（技能引用校验） ----

    def test_register_unknown_skill_rejected(self):
        r = oca.api_register({"kind": "agent", "agent_id": "DEV3", "name": "三号",
                              "dept": "dev", "grade": "E2",
                              "model": "glm/glm-5.3",
                              "tool_preset": "不存在的技能"}, self.tmp)
        self.assertFalse(r["ok"])
        self.assertIn("skill_registry", r["error"])
        self.assertNotIn("DEV3", [a["agent_id"] for _, a in oca._all_agents_raw(self._org())])

    # ---- 拍板②：模型档位硬校验 ----

    def test_register_tier_mismatch_rejected(self):
        r = oca.api_register({"kind": "agent", "agent_id": "RUN1", "dept": "dev",
                              "grade": "E1", "model": "glm/glm-5.3",
                              "tool_preset": "devtask"}, self.tmp)
        self.assertFalse(r["ok"])
        self.assertIn("档位不符", r["error"])

    def test_register_e1_local_ok(self):
        r = oca.api_register({"kind": "agent", "agent_id": "RUN1", "dept": "dev",
                              "grade": "E1", "model": "qwen3.5:9b",
                              "tool_preset": "devtask"}, self.tmp)
        self.assertTrue(r["ok"], r)

    def test_register_disabled_model_rejected(self):
        r = oca.api_register({"kind": "agent", "agent_id": "CLD1", "dept": "dev",
                              "grade": "E3", "model": "claude/claude-sonnet"},
                             self.tmp)
        self.assertFalse(r["ok"])
        self.assertIn("未启用", r["error"])

    # ---- 落盘安全链：备份 + 审计 + 原子写 ----

    def test_register_writes_backs_up_audits(self):
        r = oca.api_register({"kind": "agent", "agent_id": "DEV3", "name": "三号",
                              "dept": "dev", "grade": "E2",
                              "model": "glm/glm-5.3", "tool_preset": "devtask",
                              "charter": "写测试", "persona": "你是 DEV3。",
                              "caller": "unittest@k190"}, self.tmp)
        self.assertTrue(r["ok"], r)
        org = self._org()
        dev = [d for d in org["departments"] if d["dept"] == "dev"][0]
        self.assertEqual(dev["agents"][-1]["agent_id"], "DEV3")
        self.assertEqual(dev["agents"][-1]["llm_binding"], "glm/glm-5.3")
        # 备份存在且可解析
        self.assertTrue(os.path.isdir(os.path.join(
            self.tmp, "agent_platform", "registry", "binding_backups",
            os.path.basename(r["audit"]["backup"]))))
        # 人设文件落盘
        self.assertTrue(os.path.exists(os.path.join(
            self.tmp, "agent_platform", "prompts", "dev3.md")))
        # 审计行（拍板③：直属线直接生效+事后审计）
        ap = os.path.join(self.tmp, "agent_platform", "registry", "org_audit.jsonl")
        with open(ap, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        self.assertEqual(rows[-1]["kind"], "agent")
        self.assertEqual(rows[-1]["caller"], "unittest@k190")

    def test_register_new_dept_inline(self):
        r = oca.api_register({"kind": "agent", "agent_id": "MKT1", "dept": "mkt",
                              "dept_name": "市场部", "grade": "E2",
                              "model": "glm/glm-5.3", "tool_preset": ""},
                             self.tmp)
        self.assertTrue(r["ok"], r)
        org = self._org()
        mkt = [d for d in org["departments"] if d["dept"] == "mkt"]
        self.assertEqual(len(mkt), 1)
        self.assertEqual(mkt[0]["agents"][0]["agent_id"], "MKT1")

    def test_register_duplicate_rejected(self):
        r = oca.api_register({"kind": "agent", "agent_id": "D1", "dept": "dev",
                              "grade": "E2", "model": "glm/glm-5.3"},
                             self.tmp)
        self.assertFalse(r["ok"])
        self.assertIn("已在编", r["error"])

    def test_register_bad_id_rejected(self):
        r = oca.api_register({"kind": "agent", "agent_id": "bad id", "dept": "dev",
                              "grade": "E2", "model": "glm/glm-5.3"}, self.tmp)
        self.assertFalse(r["ok"])

    # ---- 拍板③：项目线走 A0 确认，不直接生效 ----

    def test_project_register_goes_pending(self):
        r = oca.api_project_register(
            {"kind": "project", "dept": "dev", "project_id": "PROJ-A",
             "name": "试验项目", "k_prefix": "K-TEST-001",
             "members": ["D1"], "charter": "试点"}, self.tmp)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["pending_a0_confirm"])
        pend = os.path.join(self.tmp, "agent_platform", "registry",
                            "workflow_candidates", "pending_a0_confirm",
                            "PROJ-A.json")
        self.assertTrue(os.path.exists(pend))
        with open(pend, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["status"], "pending_a0_confirm")
        # org.json 未被写入项目
        org = self._org()
        self.assertNotIn("projects", org)

    def test_project_unknown_member_rejected(self):
        r = oca.api_project_register(
            {"kind": "project", "dept": "dev", "project_id": "PROJ-B",
             "name": "x", "k_prefix": "K-T-002", "members": ["Ghost"]}, self.tmp)
        self.assertFalse(r["ok"])
        self.assertIn("不在编", r["error"])

    # ---- 属性/模型编辑 ----

    def test_update_agent_model_to_flash(self):
        r = oca.api_agent_update({"agent_id": "D1", "model": "glm/glm-5.3-flash",
                                  "experience": "21年",
                                  "specialties": "后端,测试",
                                  "tool_preset": "devtask,quant_dual",
                                  "caller": "unittest@k190"}, self.tmp)
        self.assertTrue(r["ok"], r)
        self.assertIn("模型", r["changed"])
        d1 = [a for _, a in oca._all_agents_raw(self._org())
              if a["agent_id"] == "D1"][0]
        self.assertEqual(d1["llm_binding"], "glm/glm-5.3-flash")
        self.assertEqual(d1["attributes"]["specialties"], ["后端", "测试"])

    def test_update_agent_tier_gate(self):
        r = oca.api_agent_update({"agent_id": "D1", "model": "qwen3.5:9b"},
                                 self.tmp)  # E2 不允许 local
        self.assertFalse(r["ok"])
        self.assertIn("档位不符", r["error"])

    def test_update_a0_rejected(self):
        r = oca.api_agent_update({"agent_id": "A0", "model": "glm/glm-5.3-flash"},
                                 self.tmp)
        self.assertFalse(r["ok"])
        self.assertIn("chief", r["error"])

    # ---- 学习环诚实降级 ----

    def test_learn_honest_refusal(self):
        r = oca.api_agent_learn({"agent_id": "D1", "doc": "x"}, self.tmp)
        self.assertFalse(r["ok"])
        self.assertIn("P2", r["error"])

    def test_knowledge_empty_ok(self):
        r = oca.api_agent_knowledge({"id": ["D1"]}, self.tmp)
        self.assertTrue(r["ok"])
        self.assertEqual(r["items"], [])

    # ---- 真实 org.json 形态回归（2026-10-19 lead 验收修订） ----

    def test_bundle_string_agents_with_profiles(self):
        """真实形态：dept.agents=[ID 字符串] + dept.agent_profiles=[dict]——
        判例：只认 dict 的旧版把 29 在编读成 1。"""
        org = {
            "org": "laotian_ai", "report_to": "human",
            "chief": {"agent_id": "A0", "llm_binding": "glm/glm-5.3",
                      "grade": "E3", "attributes": {}},
            "departments": [
                {"dept": "invest", "name": "量化交易部", "status": "active",
                 "agents": ["T1", "T2", "GHOST"],
                 "agent_profiles": [
                     {"agent_id": "T1", "role_profile": "t1",
                      "grade": "E2", "llm_binding": "glm/glm-5.3-flash",
                      "tools": ["devtask"], "charter_detail": "判读",
                      "attributes": {}},
                     {"agent_id": "T2", "grade": "E1",
                      "llm_binding": "glm/glm-5.3-flash", "attributes": {}},
                 ],
                 "teams": []},
            ],
        }
        reg = os.path.join(self.tmp, "agent_platform", "registry")
        with open(os.path.join(reg, "org.json"), "w", encoding="utf-8") as f:
            json.dump(org, f, ensure_ascii=False)
        b = oca.load_bundle(self.tmp)
        # T1/T2 有 profile；GHOST 有编制无详情 → 最小条目也要在编（不假绿丢人）
        self.assertEqual(b["stats"]["agents"], 4)  # A0 + T1 + T2 + GHOST
        self.assertIn("T1", b["agents"])
        self.assertEqual(b["agents"]["T1"]["charter"], "判读")
        self.assertIn("GHOST", b["agents"])
        self.assertEqual(b["agents"]["GHOST"]["llm"], "")
        inv = [d for d in b["departments"] if d["id"] == "invest"][0]
        self.assertEqual(inv["agents"], ["T1", "T2", "GHOST"])
        # 注册去重口径也认字符串形态岗
        r = oca.api_register({"kind": "agent", "agent_id": "T1", "dept": "invest",
                              "grade": "E2", "model": "glm/glm-5.3-flash"},
                             self.tmp)
        self.assertFalse(r["ok"])
        self.assertIn("已在编", r["error"])

    # ---- /api/org/meta 元数据面 ----

    def test_meta_shape_and_data(self):
        j = oca.api_meta(self.tmp)
        self.assertTrue(j["ok"])
        # 模型档位表（tier/enabled/note 三字段契约）
        self.assertEqual(j["models"]["glm/glm-5.3"],
                         {"tier": "strong", "enabled": True, "note": ""})
        self.assertFalse(j["models"]["claude/claude-sonnet"]["enabled"])
        # 档位策略原样透传
        self.assertEqual(j["grade_policy"]["E1"]["allow"], ["fast", "local"])
        # 技能索引按 kind 分桶（引用不内嵌：只给 id）
        self.assertEqual(j["skills"]["tools"], ["devtask"])
        self.assertEqual(j["skills"]["algos"], ["quant_dual"])
        self.assertEqual(j["skills"]["automations"], [])

    def test_meta_missing_files_degrade(self):
        """档位表/技能库缺失 → 降级空表（ok 仍 true，前端容错）。"""
        tmp2 = tempfile.mkdtemp(prefix="org_api_meta_empty_")
        try:
            j = oca.api_meta(tmp2)
            self.assertTrue(j["ok"])
            self.assertEqual(j["models"], {})
            self.assertEqual(j["skills"], {"tools": [], "algos": [], "automations": []})
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

    # ---- 心跳三态（green / stale / no_ledger / None）----

    def _hb_agent(self, probe_rel):
        return {"heartbeat": {"expect": "daily 17:35",
                              "probe": probe_rel, "stale_after": "26h"}}

    def test_heartbeat_green(self):
        # tmp station 里造 probe 文件：新 mtime → green
        rel = "agent_platform/data/shadow/probe.log"
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("SEND_OK\n")
        self.assertEqual(oca._heartbeat_state(self._hb_agent(rel), self.tmp), "green")

    def test_heartbeat_stale(self):
        # probe 文件 mtime 设 3 天前（os.utime）→ 超过 26h → stale
        rel = "agent_platform/data/shadow/probe.log"
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("SEND_OK\n")
        old = time.time() - 3 * 86400
        os.utime(p, (old, old))
        self.assertEqual(oca._heartbeat_state(self._hb_agent(rel), self.tmp), "stale")

    def test_heartbeat_no_ledger(self):
        # probe 文件缺失 → no_ledger（诚实灰，首跑前正常形态）
        self.assertEqual(oca._heartbeat_state(
            self._hb_agent("agent_platform/data/shadow/absent.log"), self.tmp),
            "no_ledger")

    def test_heartbeat_invalid_stale_after_grey(self):
        # stale_after 非法（非法单位/负数/非时长串）→ 按无配置处理 → None
        for bad in ("26 hours", "-5h", "soon", "", None, "1w"):
            self.assertIsNone(oca._heartbeat_state(
                {"heartbeat": {"probe": "x.log", "stale_after": bad}}, self.tmp),
                "stale_after=%r 应判非法" % (bad,))
        # 三种合法单位解析正确（26h/90m/2d）
        self.assertEqual(oca._stale_after_seconds("26h"), 26 * 3600)
        self.assertEqual(oca._stale_after_seconds("90m"), 90 * 60)
        self.assertEqual(oca._stale_after_seconds("2d"), 2 * 86400)

    def test_bundle_heartbeat_state_unconfigured_is_none(self):
        # bundle 输出含 heartbeat_state；未配置者（D1）为 None
        b = oca.api_org(self.tmp)["bundle"]
        self.assertIn("heartbeat_state", b["agents"]["D1"])
        self.assertIsNone(b["agents"]["D1"]["heartbeat_state"])

    def test_bundle_chief_default_heartbeat_no_ledger_in_tmp(self):
        # chief 内置默认心跳（常量注入，不改 org.json）：tmp 无日志 → no_ledger
        b = oca.api_org(self.tmp)["bundle"]
        a0 = b["agents"]["A0"]
        self.assertEqual(a0["heartbeat"]["probe"],
                         "agent_platform/data/shadow/chief_last_run.log")
        self.assertEqual(a0["heartbeat"]["stale_after"], "26h")
        self.assertEqual(a0["heartbeat_state"], "no_ledger")
        # org.json 未被写入 heartbeat（常量只在 bundle 层注入）
        self.assertIsNone(self._org()["chief"].get("heartbeat"))

    def test_bundle_agent_heartbeat_configured_green(self):
        # 部门岗配置 heartbeat + probe 文件新鲜 → bundle 中 green
        rel = "agent_platform/data/shadow/probe.log"
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("ok\n")
        org = self._org()
        org["departments"][0]["agents"][0]["heartbeat"] = {
            "probe": rel, "stale_after": "26h"}
        with open(os.path.join(self.tmp, "agent_platform", "registry",
                               "org.json"), "w", encoding="utf-8") as f:
            json.dump(org, f, ensure_ascii=False)
        b = oca.api_org(self.tmp)["bundle"]
        self.assertEqual(b["agents"]["D1"]["heartbeat_state"], "green")


if __name__ == "__main__":
    unittest.main(verbosity=2)
