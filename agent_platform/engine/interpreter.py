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
from contextlib import nullcontext
import contextvars
from pathlib import Path

sys_dir = Path(__file__).parent
import sys  # noqa: E402

sys.path.insert(0, str(sys_dir))

from llm import similarity  # noqa: E402
from team_schema import build_instruction, parse_structured  # noqa: E402
import team_exec  # noqa: E402
import logging  # noqa: E402

SIM_CONVERGE = 0.8  # 菜鸟 bull/bear 辩论收敛阈值


def _infer_role(role_profile):
    """控辩审契约（V2 思路 2）：从 role_profile 后缀推断立场角色。
    显式 role 字段优先于本推断；无法定性返回 None（校验跳过，向后兼容）。"""
    rp = (role_profile or "").lower()
    if rp.endswith("_bull") or rp.startswith("bull_"):
        return "bull"
    if rp.endswith("_bear") or rp.startswith("bear_"):
        return "bear"
    return None


def converge_state(a_text, b_text):
    """K-ORG-131 / T-20261008-002（B10 假收敛链）：收敛双条件——
    sim 超阈值且双方立场仍对立才算真提前收敛。
    立场提取口径对齐 eval_shadow.stance_groups（首 200 字首个「看多/看空」命中）。
    返回 (sim, converged, collapsed)：
    - converged=sim 超阈值且立场对立（真收敛）
    - collapsed=sim 超阈值但立场同向/缺失（立场塌缩：一方倒戈→表述趋同，非共识）——
      按轮次上限收场并标记 collapsed；Day0 判例：601318 末轮 0.78 距阈值 0.02，
      倒戈致立场塌缩险被误判为共识提前终止"""
    import re as _re

    sim = similarity(a_text, b_text)

    def _stance(t):
        m = _re.search(r"看多|看空", t[:200])
        return m.group(0) if m else None

    sa, sb = _stance(a_text), _stance(b_text)
    opposed = bool(sa) and bool(sb) and sa != sb
    converged = sim > SIM_CONVERGE and opposed
    collapsed = sim > SIM_CONVERGE and not opposed
    return sim, converged, collapsed


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
                    raise TopologyError(
                        "%s → %s 层级倒挂（可能成环）" % (pk, a["node_key"])
                    )
            if a.get("is_debate_node"):
                partner_key = a.get("debate_partner_key")
                if not partner_key or partner_key not in self.by_node:
                    raise TopologyError(
                        "%s 辩论对手 %s 不存在" % (a["node_key"], partner_key)
                    )
                partner = self.by_node[partner_key]
                if partner.get("debate_partner_key") != a["node_key"]:
                    raise TopologyError(
                        "%s 与 %s 未互指为辩论对手" % (a["node_key"], partner_key)
                    )
                # 控辩审契约（V2 思路 2 / K-ORG-166）：辩论对必须立场互补。
                # role 显式字段优先，缺省从 role_profile 后缀（_bull/_bear）推断；
                # 双方都无法定性时跳过（最小测试团队向后兼容），能定性且相同=配置错误。
                ra = a.get("role") or _infer_role(a.get("role_profile", ""))
                rb = partner.get("role") or _infer_role(partner.get("role_profile", ""))
                if ra and rb and ra == rb:
                    raise TopologyError(
                        "%s(%s) 与 %s(%s) 立场相同——控辩审辩论对必须 bull/bear 互补"
                        % (a["node_key"], ra, partner_key, rb)
                    )

    def prompt(self, agent):
        """人设外置（对齐菜鸟 ai_chat_role 配置化模式）：prompts/{role_profile}.md"""
        pp = sys_dir.parent / "prompts" / (agent["role_profile"] + ".md")
        return (
            pp.read_text(encoding="utf-8")
            if pp.exists()
            else "你是%s（%s）。" % (agent["agent_id"], agent["analysis_type"])
        )


class Interpreter:
    def __init__(self, team, llm, memory, checkpointer):
        self.team = team
        self.llm = llm
        self.memory = memory
        self.cp = checkpointer

    # ---------- O6 P2b：run 上下文 + attempt 记录 ----------
    def _run_scope(self, run_id):
        """把一次 Interpreter.run 包进 trace run 上下文；不可用时降级为 nullcontext。"""
        try:
            from observability import trace as _tr

            return _tr.run_scope(str(run_id))
        except Exception:  # noqa: BLE001
            return nullcontext()

    def _emit_attempt(self, run_id, node, status, latency_ms):
        """把一次 LLM 调用尝试记为同一 run 的 attempt（不含密钥/大参数）。"""
        try:
            from observability import trace as _tr

            _tr.record(
                _tr.trace_id(),
                "llm.attempt",
                "llm",
                status="ok" if str(status) in ("normal", "ok", "success", "succeeded") else "error",
                attrs={
                    "zhaoyun.node_key": str(node.get("node_key") or ""),
                    "zhaoyun.agent_id": str(node.get("agent_id") or ""),
                    "gen_ai.request.model": str(node.get("llm_binding") or ""),
                    "zhaoyun.status": str(status or ""),
                    "zhaoyun.latency_ms": round(float(latency_ms or 0), 1),
                },
                run_id=str(run_id),
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/engine/interpreter.py", exc)

    # ---------- 主流程 ----------
    def run(self, stock, run_id=None, data_ctx=None):
        run_id = run_id or uuid.uuid4().hex[:12]
        with self._run_scope(run_id):
            return self._run_inner(stock, run_id, data_ctx)

    # ---------- 主流程（P2b：在 run_scope 内执行） ----------
    def _run_inner(self, stock, run_id, data_ctx=None):
        self.cp.start_run(run_id, stock)
        ctx = {"stock": stock, "statements": {}, "status": {}, "data": data_ctx or {},
               "structured": {}}
        layers = {}
        for a in self.team.agents:
            layers.setdefault(a["level"], []).append(a)

        try:
            for level in sorted(layers):
                states = self.cp.get_run_states(
                    run_id
                )  # 断点续跑：查已 checkpoint 节点
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
                            ctx["statements"][n["node_key"]] = states[
                                n["node_key"]
                            ].get("output", "")
                            ctx["status"][n["node_key"]] = "resumed"
                            continue
                        jobs.append(("single", n, None))

                with ThreadPoolExecutor(max_workers=max(len(jobs), 1)) as ex:
                    futures = []
                    for kind, a, b in jobs:
                        cv = contextvars.copy_context()
                        futures.append(
                            ex.submit(cv.run, self._dispatch, kind, a, b, ctx, run_id)
                        )
                    for fut in futures:
                        for node_key, text, status in fut.result():
                            ctx["statements"][node_key] = text
                            ctx["status"][node_key] = status
        except Exception:  # noqa: BLE001
            self.cp.finish_run(run_id, status="failed")
            raise

        final_key = (
            max(layers[max(layers)], key=lambda n: 0)["node_key"] if layers else None
        )
        decision = ctx["statements"].get("d1_decision") or ctx["statements"].get(
            final_key, ""
        )
        # 菜鸟 createResultSummary + node_exec 执行账（落 agent_platform/data/team_exec）
        try:
            records = team_exec.read_run(run_id)
            dec_obj = team_exec.parse_decision_text(decision)
            summary = team_exec.build_summary(dec_obj, records, stock)
            team_exec.write_summary(run_id, summary)
        except Exception as exc:  # noqa: BLE001 摘要失败不阻塞决策
            summary = {}
            records = []
            logging.getLogger(__name__).warning("team summary failed: %s", exc)
        self.cp.finish_run(run_id, status="done")
        return {
            "run_id": run_id,
            "stock": stock,
            "decision": decision,
            "statements": ctx["statements"],
            "status": ctx["status"],
            "summary": summary,
            "structured": ctx.get("structured") or {},
            "node_exec": records,
            # 2026-10-29 outcome 台账：声明绑定模型（按节点），供 run_shadow 落账
            "models": {a.get("node_key"): a.get("llm_binding") for a in self.team.agents},
        }

    # ---------- 调度 ----------
    def _dispatch(self, kind, na, nb, ctx, run_id):
        if kind == "debate":
            return self._debate(na, nb, ctx, run_id)
        key, text, status = self._single(na, ctx, run_id)
        return [(key, text, status)]

    def _chat(self, node, user):
        # 10-18 全前缀路由：binding 原样下传（glm/→云端、nova/→特种、ollama/→本地），
        # 由 OllamaLLM.chat 入口统一分发——旧实现此处先砍前缀，glm 绑定被误塞给
        # 本地 Ollama 404，靠降级三态装死（SEC2/OPT1 云端席位一直未真正生效）。
        # Mock 的 chat 忽略 model 参数，mock 路径不受影响。
        model = node["llm_binding"]
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
        # 菜鸟 buildNodeInput：下游优先读父节点的结构化输出，缺失才退回全文。
        parents = node.get("parent_node_keys") or []
        upstream = []
        for p in parents:
            st = (ctx.get("structured") or {}).get(p)
            if isinstance(st, dict) and st and not st.get("error"):
                upstream.append("### %s\n%s" % (
                    p, json.dumps(st, ensure_ascii=False)[:900]))
            elif (ctx.get("statements") or {}).get(p):
                upstream.append("### %s\n%s" % (
                    p, str(ctx["statements"][p])[:900]))
        if upstream:
            base += "\n[上游分析摘要]\n" + "\n".join(upstream) + "\n"
        # 菜鸟 buildDebateInput：辩论节点注入全部分析师结构化摘要，再让模型逐条反驳。
        if node.get("is_debate_node"):
            analyst = []
            for k, st in (ctx.get("structured") or {}).items():
                if k == node.get("node_key") or k == node.get("debate_partner_key"):
                    continue
                if k in parents:
                    continue  # 已在上游摘要注入，避免重复
                if isinstance(st, dict) and st and not st.get("error"):
                    analyst.append("### %s\n%s" % (
                        k, json.dumps(st, ensure_ascii=False)[:900]))
            if analyst:
                base += "\n[分析师报告摘要]\n" + "\n".join(analyst) + "\n"
        # 决策节点与其它节点的输出契约不同：D1 的 system 要求「输出 JSON」，而旧实现
        # 给**所有**节点追加「3 句以内结论」——与 system 直接冲突，模型采信 user 时
        # 只写散文，parse_decision 拿不到 JSON（K-STOCK-338 偶发 parse_ok=false 的
        # 根因之一：10-17 隔离预演 600519 复现 123 字散文无 JSON）。决策节点单独收口。
        if node.get("analysis_type") == "final_decision":
            return (
                base
                + "\n请严格只输出一个 JSON 对象，不要任何解释文字、不要代码块围栏。"
            )
        # 菜鸟团队分析移植：分析师/研究员追加按 analysis_type 的 JSON Schema 指令，
        # 先写 Markdown 正文再在末尾输出 ```json``` 结构化块（不破坏 D1 严格 JSON）。
        schema_instruction = build_instruction(node.get("analysis_type"))
        if schema_instruction:
            return base + schema_instruction
        return base + "\n请基于以上数据给出简要分析结论（3 句以内，引用具体数据）。"

    # ---------- 单节点 ----------
    def _single(self, node, ctx, run_id):
        t0 = time.time()
        try:
            text, status = self._chat(node, self._build_user(node, ctx))
        except Exception as e:  # noqa: BLE001
            text, status = "[降级] LLM 不可用（%s），按中性规则处理" % e, "degraded"
        self._emit_attempt(run_id, node, status, (time.time() - t0) * 1000)
        self._record(
            node,
            ctx,
            run_id,
            phase="single",
            output=text,
            status=status,
            latency_ms=(time.time() - t0) * 1000,
            structured=parse_structured(text),
        )
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
        self._emit_attempt(run_id, na, st_a, (time.time() - t0) * 1000)
        self._emit_attempt(run_id, nb, st_b, (time.time() - t0) * 1000)
        self.cp.trace(
            run_id,
            na["node_key"],
            na["agent_id"],
            "debate_open",
            round_=0,
            output=a_text,
            status=st_a,
        )
        self.cp.trace(
            run_id,
            nb["node_key"],
            nb["agent_id"],
            "debate_open",
            round_=0,
            output=b_text,
            status=st_b,
        )

        rounds = int(na.get("max_debate_rounds") or 2)
        sim, converged, collapsed = converge_state(a_text, b_text)
        debate_log = []
        for r in range(1, rounds + 1):
            if converged:
                break
            a_input = self._build_user(na, ctx, extra=b_text)
            b_input = self._build_user(nb, ctx, extra=a_text)
            try:
                a_reb, st_a = self._chat(na, a_input)
            except Exception as e:  # noqa: BLE001
                a_reb, st_a = "[降级] %s" % e, "degraded"
            try:
                b_reb, st_b = self._chat(nb, b_input)
            except Exception as e:  # noqa: BLE001
                b_reb, st_b = "[降级] %s" % e, "degraded"
            self._emit_attempt(run_id, na, st_a, (time.time() - t0) * 1000)
            self._emit_attempt(run_id, nb, st_b, (time.time() - t0) * 1000)
            sim, converged, collapsed = converge_state(a_reb, b_reb)
            debate_log.append({
                "round": r,
                "bull_input": a_input[-1200:],
                "bear_input": b_input[-1200:],
                "bull_output": a_reb,
                "bear_output": b_reb,
                "similarity": sim,
                "terminated_early": bool(converged),
            })
            self.cp.trace(
                run_id,
                na["node_key"],
                na["agent_id"],
                "debate_rebuttal",
                round_=r,
                output=a_reb,
                similarity_=sim,
                status=st_a,
            )
            self.cp.trace(
                run_id,
                nb["node_key"],
                nb["agent_id"],
                "debate_rebuttal",
                round_=r,
                output=b_reb,
                similarity_=sim,
                status=st_b,
            )
            a_text, b_text = a_reb, b_reb

        verdict = (
            "提前收敛"
            if converged
            else "立场塌缩collapsed（表述趋同但立场同向/缺失，非共识）"
            if collapsed
            else "达轮次上限"
        )
        # 控辩审契约：结论携带显式角色（显式 role 字段优先，缺省从 role_profile 推断）
        ra = na.get("role") or _infer_role(na.get("role_profile", "")) or "bull?"
        rb = nb.get("role") or _infer_role(nb.get("role_profile", "")) or "bear?"
        merged = (
            "多空辩论结论（%s轮，末轮相似度%.2f，%s）：多头[%s·%s]：%s ‖ 空头[%s·%s]：%s"
            % (r, sim, verdict, ra, na["agent_id"], a_text, rb, nb["agent_id"], b_text)
        )
        # 各自记忆：私有 decision + 团队 observation（RBAC 校验在 MemoryStore 内）
        for node, text, st in ((na, a_text, st_a), (nb, b_text, st_b)):
            self._record(
                node,
                ctx,
                run_id,
                phase="debate_final",
                output=text,
                status=st,
                similarity_=sim,
                latency_ms=(time.time() - t0) * 1000,
                structured=parse_structured(text),
                debate_log=debate_log,
            )
        # 辩论对共享同一份合并结论，供 D1 汇总（parent 引用两节点）；collapsed 供 D1/评估者识别假收敛
        payload = {
            "merged": merged,
            "rounds": r,
            "sim": sim,
            "collapsed": collapsed,
            "roles": [ra, rb],
        }
        self.cp.save_node(run_id, na["node_key"], na["agent_id"], payload)
        self.cp.save_node(run_id, nb["node_key"], nb["agent_id"], payload)
        return [(na["node_key"], merged, st_a), (nb["node_key"], merged, st_b)]

    # ---------- 落库 ----------
    def _record(
        self,
        node,
        ctx,
        run_id,
        phase,
        output,
        status,
        similarity_=None,
        latency_ms=None,
        structured=None,
        debate_log=None,
    ):
        self.cp.save_node(
            run_id,
            node["node_key"],
            node["agent_id"],
            {"output": output, "status": status, "structured": structured or {}},
        )
        if structured:
            ctx.setdefault("structured", {})[node["node_key"]] = structured
        # 菜鸟 node_exec 执行账：normal/debate/degraded + duration/retry/structured
        mode = "degraded" if status == "degraded" else (
            "debate" if str(phase).startswith("debate") else "normal")
        try:
            team_exec.record(
                run_id, node, phase, mode, status, latency_ms or 0,
                input_text="", output_text=output, structured=structured or {},
                debate_log=debate_log or [],
            )
        except Exception as exc:  # noqa: BLE001 执行账失败不阻塞
            logging.getLogger(__name__).warning("team_exec record failed: %s", exc)
        self.cp.trace(
            run_id,
            node["node_key"],
            node["agent_id"],
            phase,
            output=output,
            similarity_=similarity_,
            status=status,
            latency_ms=latency_ms,
        )
        try:
            self.memory.write(
                node["agent_id"],
                "agent/%s/decision" % node["agent_id"],
                {
                    "stock": ctx["stock"],
                    "phase": phase,
                    "output": output,
                    "status": status,
                    "structured": structured or {},
                },
            )
            if node.get("is_debate_node"):
                self.memory.write(
                    node["agent_id"],
                    "dept/%s/observation" % self.team.dept,
                    {
                        "stock": ctx["stock"],
                        "round": phase,
                        "output": output,
                        "status": status,
                        "structured": structured or {},
                    },
                )
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/engine/interpreter.py", exc)
