# -*- coding: utf-8 -*-
"""投研团队 DSL 解释器 v0（K-STOCK-004）

菜鸟 ai_team_role 的 Python 重实现：
    拓扑校验 → 按 level 分层 → 层内并行（辩论对成对调度）→ wait_all 屏障
    → 辩论循环（轮次上限 + 字符 bigram 相似度 > 0.8 提前收敛，对齐菜鸟 bull/bear 链）
    → 节点 checkpoint 落库 → 最终决策节点输出五档推荐。

正交纪律：本引擎只管「执行拓扑」；org 汇报/记忆权限由 org 注册表与 memory grants 管（K-ORG-001）。
记忆写入执行 RBAC 三道闸之运行时校验（memory.MemoryStore.enforce=True）。
"""
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys_dir = Path(__file__).parent
import sys  # noqa: E402

sys.path.insert(0, str(sys_dir))

from checkpoints import Checkpointer  # noqa: E402
from llm import OllamaLLM, MockLLM, similarity  # noqa: E402
from memory import MemoryStore  # noqa: E402

SIM_CONVERGE = 0.8  # 菜鸟 bull/bear 辩论收敛阈值


class TopologyError(Exception):
    """注册表拓扑非法（无环/辩论互指/父节点缺失等）"""


class Team:
    """注册表加载与拓扑校验"""

    def __init__(self, registry_path):
        p = Path(registry_path)
        if p.suffix == ".yaml":
            try:
                import yaml
                data = yaml.safe_load(p.read_text(encoding="utf-8"))
            except ImportError:
                raise TopologyError("PyYAML 未安装，请用 JSON 注册表")
        else:
            data = json.loads(p.read_text(encoding="utf-8"))
        self.team = data["team"]
        self.dept = data["dept"]
        self.agents = data["agents"]
        self.by_node = {a["node_key"]: a for a in self.agents}
        self.by_agent = {a["agent_id"]: a for a in self.agents}
        self.validate()

    def validate(self):
        keys = list(self.by_node)
        if len(keys) != len(self.agents):
            raise TopologyError("node_key 重复")
        for a in self.agents:
            for pk in a["parent_node_keys"] or []:
                if pk not in self.by_node:
                    raise TopologyError("%s 父节点 %s 不存在" % (a["node_key"], pk))
                if self.by_node[pk]["level"] >= a["level"]:
                    raise TopologyError("%s → %s 层级倒挂（可能成环）" % (pk, a["node_key"]))
            if a.get("is_debate_node"):
                partner_key = a.get("debate_partner_key")
                if not partner_key or partner_key not in self.by_node:
                    raise TopologyError("%s 辩论对手 %s 不存在" % (a["node_key"], partner_key))
                partner = self.by_node[partner_key]
                if partner.get("debate_partner_key") != a["node_key"]:
                    raise TopologyError("%s 与 %s 未互指为辩论对手" % (a["node_key"], partner_key))

    def prompt(self, agent):
        """人设外置（对齐菜鸟 ai_chat_role 配置化模式）：prompts/{role_profile}.md"""
        pp = sys_dir.parent / "prompts" / (agent["role_profile"] + ".md")
        return pp.read_text(encoding="utf-8") if pp.exists() else "你是%s（%s）。" % (
            agent["agent_id"], agent["analysis_type"])


class Interpreter:
    def __init__(self, team, llm, memory, checkpointer):
        self.team = team
        self.llm = llm
        self.memory = memory
        self.cp = checkpointer

    # ---------- 主流程 ----------
    def run(self, stock, run_id=None, data_ctx=None):
        run_id = run_id or uuid.uuid4().hex[:12]
        self.cp.start_run(run_id, stock)
        ctx = {"stock": stock, "statements": {}, "status": {}, "data": data_ctx or {}}
        layers = {}
        for a in self.team.agents:
            layers.setdefault(a["level"], []).append(a)

        try:
            for level in sorted(layers):
                states = self.cp.get_run_states(run_id)  # 断点续跑：查已 checkpoint 节点
                nodes = layers[level]
                done_pairs = set()
                jobs = []
                for n in nodes:
                    if n.get("is_debate_node"):
                        pk = n["debate_partner_key"]
                        if n["node_key"] in states and pk in states:
                            st = states[n["node_key"]]
                            merged = st.get("merged") or st.get("output", "")
                            for k in (n["node_key"], pk):
                                ctx["statements"][k] = merged
                                ctx["status"][k] = "resumed"
                            done_pairs.update([n["node_key"], pk])
                            continue
                        if pk in done_pairs:
                            continue
                        done_pairs.update([n["node_key"], pk])
                        jobs.append(("debate", n, self.team.by_node[pk]))
                    else:
                        if n["node_key"] in states:
                            ctx["statements"][n["node_key"]] = states[n["node_key"]].get("output", "")
                            ctx["status"][n["node_key"]] = "resumed"
                            continue
                        jobs.append(("single", n, None))

                with ThreadPoolExecutor(max_workers=max(len(jobs), 1)) as ex:
                    futures = [ex.submit(self._dispatch, kind, a, b, ctx, run_id) for kind, a, b in jobs]
                    for fut in futures:
                        for node_key, text, status in fut.result():
                            ctx["statements"][node_key] = text
                            ctx["status"][node_key] = status
        except Exception as e:  # noqa: BLE001
            self.cp.finish_run(run_id, status="failed")
            raise

        final_key = max(layers[max(layers)], key=lambda n: 0)["node_key"] if layers else None
        decision = ctx["statements"].get("d1_decision") or ctx["statements"].get(final_key, "")
        self.cp.finish_run(run_id, status="done")
        return {
            "run_id": run_id,
            "stock": stock,
            "decision": decision,
            "statements": ctx["statements"],
            "status": ctx["status"],
        }

    # ---------- 调度 ----------
    def _dispatch(self, kind, na, nb, ctx, run_id):
        if kind == "debate":
            return self._debate(na, nb, ctx, run_id)
        key, text, status = self._single(na, ctx, run_id)
        return [(key, text, status)]

    def _chat(self, node, user):
        model = node["llm_binding"].split("/", 1)[1] if "/" in node["llm_binding"] else node["llm_binding"]
        # 角色标识前缀：Mock 精确路由 + 真 LLM 无害（人设配置化的运行时锚点）
        system = "[%s]\n%s" % (node["role_profile"], self.team.prompt(node))
        return self.llm.chat(system, user, model)

    def _build_user(self, node, ctx, extra=""):
        base = "标的：%s\n分析类型：%s\n" % (ctx["stock"], node["analysis_type"])
        data = (ctx.get("data") or {}).get(node["analysis_type"], "")
        if data:
            base += "\n[数据]\n%s\n" % data
        if extra:
            base += "\n对方观点：\n%s\n" % extra
        return base + "\n请基于以上数据给出简要分析结论（3 句以内，引用具体数据）。"

    # ---------- 单节点 ----------
    def _single(self, node, ctx, run_id):
        t0 = time.time()
        try:
            text, status = self._chat(node, self._build_user(node, ctx))
        except Exception as e:  # noqa: BLE001
            text, status = "[降级] LLM 不可用（%s），按中性规则处理" % e, "degraded"
        self._record(node, ctx, run_id, phase="single", output=text, status=status,
                     latency_ms=(time.time() - t0) * 1000)
        return node["node_key"], text, status

    # ---------- 辩论对（对齐菜鸟 bull/bear：轮次上限 + 相似度收敛） ----------
    def _debate(self, na, nb, ctx, run_id):
        t0 = time.time()
        # 开场陈词（各自独立）
        try:
            a_text, st_a = self._chat(na, self._build_user(na, ctx))
        except Exception as e:  # noqa: BLE001
            a_text, st_a = "[降级] 多头 LLM 不可用：%s" % e, "degraded"
        try:
            b_text, st_b = self._chat(nb, self._build_user(nb, ctx))
        except Exception as e:  # noqa: BLE001
            b_text, st_b = "[降级] 空头 LLM 不可用：%s" % e, "degraded"
        self.cp.trace(run_id, na["node_key"], na["agent_id"], "debate_open", round_=0,
                      output=a_text, status=st_a)
        self.cp.trace(run_id, nb["node_key"], nb["agent_id"], "debate_open", round_=0,
                      output=b_text, status=st_b)

        rounds = int(na.get("max_debate_rounds") or 2)
        sim = similarity(a_text, b_text)
        converged = sim > SIM_CONVERGE
        for r in range(1, rounds + 1):
            if converged:
                break
            try:
                a_reb, st_a = self._chat(na, self._build_user(na, ctx, extra=b_text))
            except Exception as e:  # noqa: BLE001
                a_reb, st_a = "[降级] %s" % e, "degraded"
            try:
                b_reb, st_b = self._chat(nb, self._build_user(nb, ctx, extra=a_reb))
            except Exception as e:  # noqa: BLE001
                b_reb, st_b = "[降级] %s" % e, "degraded"
            sim = similarity(a_reb, b_reb)
            converged = sim > SIM_CONVERGE
            self.cp.trace(run_id, na["node_key"], na["agent_id"], "debate_rebuttal", round_=r,
                          output=a_reb, similarity_=sim, status=st_a)
            self.cp.trace(run_id, nb["node_key"], nb["agent_id"], "debate_rebuttal", round_=r,
                          output=b_reb, similarity_=sim, status=st_b)
            a_text, b_text = a_reb, b_reb

        merged = (
            "多空辩论结论（%s轮，末轮相似度%.2f，%s）：多头[%s]：%s ‖ 空头[%s]：%s"
            % (r, sim, "提前收敛" if converged else "达轮次上限",
               na["agent_id"], a_text, nb["agent_id"], b_text)
        )
        # 各自记忆：私有 decision + 团队 observation（RBAC 校验在 MemoryStore 内）
        for node, text, st in ((na, a_text, st_a), (nb, b_text, st_b)):
            self._record(node, ctx, run_id, phase="debate_final", output=text, status=st,
                         similarity_=sim, latency_ms=(time.time() - t0) * 1000)
        # 辩论对共享同一份合并结论，供 D1 汇总（parent 引用两节点）
        self.cp.save_node(run_id, na["node_key"], na["agent_id"], {"merged": merged, "rounds": r, "sim": sim})
        self.cp.save_node(run_id, nb["node_key"], nb["agent_id"], {"merged": merged, "rounds": r, "sim": sim})
        return [(na["node_key"], merged, st_a), (nb["node_key"], merged, st_b)]

    # ---------- 落库 ----------
    def _record(self, node, ctx, run_id, phase, output, status, similarity_=None, latency_ms=None):
        self.cp.save_node(run_id, node["node_key"], node["agent_id"], {"output": output, "status": status})
        self.cp.trace(run_id, node["node_key"], node["agent_id"], phase,
                      output=output, similarity_=similarity_, status=status, latency_ms=latency_ms)
        try:
            self.memory.write(node["agent_id"], "agent/%s/decision" % node["agent_id"],
                              {"stock": ctx["stock"], "phase": phase, "output": output, "status": status})
            if node.get("is_debate_node"):
                self.memory.write(node["agent_id"], "dept/%s/observation" % self.team.dept,
                                  {"stock": ctx["stock"], "round": phase, "output": output, "status": status})
        except Exception:  # noqa: BLE001  RBAC 拒绝不阻断主流程（但 smoke 会单测它）
            pass
