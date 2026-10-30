# -*- coding: utf-8 -*-
"""门户管理/雇佣族 Mixin（A5 B3，2026-10-31 关羽）。

纯移动自 portal_api.py Handler（方法体零改动）：
_manager_run_json / _api_manager_roster / _api_manager_hire / _api_manager_fire /
_api_reception / _api_minutes_inbox / _api_onboard / _api_paper_direct_open

经 portal_api 基座多继承并入；class 属性（_PAPER_DIR/_PAPER_PY/_OB_GREETING_FB）
与 _paper_run_json 等仍由原 Handler 提供，通过 MRO 解析。
"""
import glob
import json
import logging
import os
import re
import subprocess
import sys
import time


class Handler:  # Mixin 壳：经基座多继承并入

    def _manager_run_json(self, cmd, timeout=35):
        """manager.py --json 专用子进程封装（K-STOCK-137d）。
        与 _paper_run_json 的差异：manager.py hire/fire 成功输出为
        indent=1 多行 JSON 数组 [rec]、拒绝时单行 {"ok": false}（exit 2）
        ——取「整段 stdout json.loads」；拒绝 JSON 原样透传（错误消息
        直达前端，与 CLI exit 2 口径一致）。"""
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
        except Exception as e:
            return {"ok": False, "error": repr(e)}
        out = (p.stdout or b"").decode("utf-8", "replace").strip()
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        try:
            d = json.loads(out)
        except Exception:
            d = None
        if isinstance(d, dict):
            return d                     # 拒绝（exit 2）：{"ok": false, error} 原样透传
        if isinstance(d, list) and d and isinstance(d[0], dict):
            rec = dict(d[0])             # hire/fire 成功：[rec]（indent=1 多行）
            rec["ok"] = True
            return rec
        return {"ok": False,
                "error": err.splitlines()[-1] if err else (out[-200:] or f"exit={p.returncode}")}

    def _api_manager_roster(self):
        """K-STOCK-137d 经理名册投影（纯只读：managers/*.json +
        manager_hires.json + models.json 系数计价，无子进程）。"""
        base = self._PAPER_DIR
        try:
            cat = None
            try:  # 模型系数表（同 manager._hire_price 口径：基价×coef_vs_glm52）
                with open(os.path.join(base, "models.json"), encoding="utf-8") as f:
                    cat = json.load(f)
            except Exception:
                cat = None
            profs = []
            for fn in sorted(glob.glob(os.path.join(base, "managers", "*.json"))):
                try:
                    with open(fn, encoding="utf-8") as f:
                        p = json.load(f)
                except Exception:
                    continue
                try:
                    coef = next((m.get("coef_vs_glm52", 1.0)
                                 for m in (cat or {}).get("models", [])
                                 if m.get("model") == p.get("model")), None)
                    p["hire_price_cny"] = (round(p.get("hire_base_price_cny", 99) * coef)
                                           if coef is not None else None)
                except Exception:
                    p["hire_price_cny"] = None
                p["active_hires"] = []
                profs.append(p)
            hp = os.path.join(base, "manager_hires.json")
            if os.path.exists(hp):
                with open(hp, encoding="utf-8") as f:
                    d = json.load(f)
                for h in ((d.get("hires") or []) if isinstance(d, dict) else []):
                    if h.get("status") != "active":
                        continue
                    for p in profs:
                        if p.get("id") == h.get("manager"):
                            p["active_hires"].append(
                                {"source": h.get("source"),
                                 "hire_price_cny": h.get("hire_price_cny"),
                                 "hired_at": h.get("hired_at"), "note": h.get("note")})
            self._json({"ok": True, "managers": profs})
        except Exception as e:
            self._json({"ok": False, "error": repr(e)}, 500)

    def _api_manager_hire(self, raw):
        """K-STOCK-137d 雇佣经理：子进程 manager.py hire --json（单写入口）。
        exit 2 拒绝 JSON 原样透传：HTTP 200 带 ok:false（错误消息直达前端）。"""
        try:
            rec = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        mid = str(rec.get("mid") or "").strip()
        source = str(rec.get("source") or "").strip()
        if not re.match(r"^[a-z0-9_]{1,32}$", mid):
            self._json({"ok": False, "error": "mid 格式非法"}, 400)
            return
        if not re.match(r"^[A-Za-z0-9_\u4e00-\u9fff-]{1,32}$", source):
            self._json({"ok": False, "error": "source 格式非法"}, 400)
            return
        cmd = [self._PAPER_PY, os.path.join(self._PAPER_DIR, "manager.py"),
               "hire", mid, source, "--json"]
        if rec.get("note"):
            cmd += ["--note", str(rec["note"])[:200]]
        self._json(self._manager_run_json(cmd, timeout=35))

    def _api_manager_fire(self, raw):
        """K-STOCK-137d 解雇经理：子进程 manager.py fire --json（单写入口）。"""
        try:
            rec = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        mid = str(rec.get("mid") or "").strip()
        source = str(rec.get("source") or "").strip()
        if not re.match(r"^[a-z0-9_]{1,32}$", mid):
            self._json({"ok": False, "error": "mid 格式非法"}, 400)
            return
        if not re.match(r"^[A-Za-z0-9_\u4e00-\u9fff-]{1,32}$", source):
            self._json({"ok": False, "error": "source 格式非法"}, 400)
            return
        cmd = [self._PAPER_PY, os.path.join(self._PAPER_DIR, "manager.py"),
               "fire", mid, source, "--json"]
        self._json(self._manager_run_json(cmd, timeout=35))

    def _api_reception(self):
        """K-STOCK-159 S-e 迎宾向导数据源（纯只读）。
        greeting：优先 duty_receptionist.json 的 role_prompt 提炼，
        档案缺失/读取失败 → 内置兜底文案；
        profiles：managers/*.json 中 status=="live" 且 id 以
        _5y/_10y/_20y 结尾的资历档摘要（读不到返回空数组）；
        portfolios：现有组合摘要。"""
        try:
            base = self._PAPER_DIR
            greeting = self._OB_GREETING_FB
            rp = os.path.join(base, "managers", "duty_receptionist.json")
            if os.path.exists(rp):
                try:
                    with open(rp, encoding="utf-8") as f:
                        _d = json.load(f)
                    _t = str(_d.get("role_prompt") or "").strip()
                    if _t:  # 提炼：取首段，限 200 字
                        greeting = _t.splitlines()[0].strip()[:200]
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
            profiles = []
            for fn in sorted(glob.glob(os.path.join(base, "managers", "*.json"))):
                try:
                    with open(fn, encoding="utf-8") as f:
                        p = json.load(f)
                except Exception:
                    continue
                pid = str(p.get("id") or "")
                if p.get("status") != "live" or not pid.endswith(
                        ("_5y", "_10y", "_20y")):
                    continue
                profiles.append({"id": pid, "name": p.get("name"),
                                 "price": p.get("hire_base_price_cny"),
                                 "style": p.get("style")})
            try:
                import subscriptions as _sub
                pfs = [{"id": p.get("id"), "name": p.get("name"),
                        "strategies": p.get("strategies") or [],
                        "trader": p.get("trader"), "price": p.get("price"),
                        "paid": p.get("paid"), "services": p.get("services")}
                       for p in (_sub.portfolios().get("portfolios") or [])]
            except Exception:
                pfs = []
            self._json({"ok": True, "greeting": greeting,
                        "profiles": profiles, "portfolios": pfs})
        except Exception as e:
            self._json({"ok": False, "error": repr(e)}, 500)

    def _api_minutes_inbox(self, qs):
        """K-STOCK-146 S-c 纪要收件箱只读投影（纯只读直读
        minutes_inbox.json，复刻 _api_manager_roster 直读形状，无子进程）。
        文件缺失/空 → {"ok": true, "minutes": []}；?mid= 可选按经理过滤；
        按 created_at 倒序，最多 50 条；字段透传（body 全量，前端折叠）。"""
        try:
            from urllib.parse import parse_qs as _pqs
            mid = (_pqs(qs.lstrip("?")).get("mid") or [""])[0].strip()
            fp = os.path.join(self._PAPER_DIR, "minutes_inbox.json")
            items = []
            if os.path.exists(fp):
                with open(fp, encoding="utf-8") as f:
                    d = json.load(f)
                raw = d.get("items") if isinstance(d, dict) else None
                if not isinstance(raw, list):
                    raw = d.get("minutes") if isinstance(d, dict) else None
                items = [m for m in (raw or []) if isinstance(m, dict)]
            if mid:
                items = [m for m in items if m.get("mid") == mid]
            items.sort(key=lambda m: str(m.get("created_at") or ""),
                       reverse=True)
            self._json({"ok": True, "minutes": items[:50]})
        except Exception as e:
            self._json({"ok": False, "error": repr(e)}, 500)

    def _api_onboard(self, raw):
        """K-STOCK-159 S-e 迎宾向导四问落账。
        body: {plan: free_manual|free_auto|paid_auto, strategies?: [sid..],
               pre_open_report: bool,
               post_close_report: bool, manager_id?, portfolio_name?}
        free_manual → 不动订阅，引导策略市场直接建仓（轻量动线）；
        free_auto/paid_auto → 建组合 pf_<NNN>（序号递增，复用 subscriptions
        原子写入口）+ strategies 显式传入优先（至少 1 个，须 live；缺省
        兜底 ninedim→注册表首个 live）+ trader 取
        roster（trainees/*.json status∈{live,training}）未被占用者
        （占用判定：hires.json 活跃雇佣；portfolio.py hire 子进程落账）；
        paid_auto → price 置 99、paid=false+「收费意向待商业化开通」；
        manager_id 合法（live）→ 子进程 manager.py hire <mid> <pf_id>。
        幂等：同 plan+同组合名重复提交返回已存在组合。"""
        try:
            rec = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        plan = str(rec.get("plan") or "").strip()
        if plan not in ("free_manual", "free_auto", "paid_auto"):
            self._json({"ok": False,
                        "error": "plan 非法（free_manual|free_auto|paid_auto）"}, 400)
            return
        if plan == "free_manual":
            self._json({"ok": True, "plan": plan,
                        "guidance": "引导策略市场直接建仓（轻量动线）",
                        "market_url": "/market-strategy"})
            return
        base = self._PAPER_DIR
        try:
            import subscriptions as _sub
            # 经理前置校验（非法立即 400，不落任何账）
            mid = str(rec.get("manager_id") or "").strip()
            if mid:
                if not re.match(r"^[a-z0-9_]{1,32}$", mid):
                    self._json({"ok": False, "error": "manager_id 格式非法"}, 400)
                    return
                mp = os.path.join(base, "managers", mid + ".json")
                try:
                    with open(mp, encoding="utf-8") as f:
                        _m = json.load(f)
                except Exception:
                    _m = None
                if not _m or _m.get("status") != "live":
                    self._json({"ok": False,
                                "error": "manager 不存在或非 live: %s" % mid}, 400)
                    return
            d = _sub._load()
            pfs = d.get("portfolios") or []
            # 幂等：同 plan+同组合名（含都为空）已有迎宾组合 → 原样返回
            marker = "K-STOCK-159 迎宾向导 plan=%s" % plan
            pname = str(rec.get("portfolio_name") or "").strip()[:32]
            for p in pfs:
                if str(p.get("note") or "").startswith(marker) and \
                        (pname == "" or (p.get("name") or "") == pname):
                    # 组合名未提供时按 plan 标记命中（自动命名不参与匹配）
                    self._json({"ok": True, "plan": plan,
                                "portfolio": p.get("id"), "existed": True,
                                "guidance": "已存在迎宾组合，未重复创建"})
                    return
            # 组合序号递增（查现有最大号）
            mx = 0
            for p in pfs:
                m = re.match(r"^pf_(\d+)$", str(p.get("id") or ""))
                if m:
                    mx = max(mx, int(m.group(1)))
            pf_id = "pf_%03d" % (mx + 1)
            # 策略：K-STOCK-165 显式传入 strategies（list[str]，至少 1 个，
            # 每个须在注册表存在且 status=="live"）优先；缺省/为空 → 兜底
            # ninedim（非 live → 注册表首个 live）。写入订阅记录用客户选的。
            try:
                with open(_sub.REG, encoding="utf-8") as f:
                    reg = json.load(f).get("strategies") or []
            except Exception:
                reg = []
            def _live(sid):
                return any(s.get("id") == sid and s.get("status") == "live"
                           for s in reg)
            raw_strats = rec.get("strategies")
            if raw_strats is not None and not isinstance(raw_strats, list):
                self._json({"ok": False,
                            "error": "strategies 须为 list[str]"}, 400)
                return
            chosen = []
            for s in (raw_strats or []):
                sid = str(s).strip()
                if sid and sid not in chosen:
                    chosen.append(sid)
            if raw_strats is not None and not chosen:
                self._json({"ok": False,
                            "error": "strategies 至少 1 个"}, 400)
                return
            if chosen:
                bad = [s for s in chosen if not _live(s)]
                if bad:
                    self._json({"ok": False,
                                "error": "策略不存在或非 live: %s"
                                % ",".join(bad)}, 400)
                    return
            else:
                strat = "ninedim"
                if not _live(strat):
                    alt = next((s["id"] for s in reg
                                if s.get("status") == "live"), None)
                    if not alt:
                        self._json({"ok": False, "error": "注册表无 live 策略"}, 409)
                        return
                    strat = alt
                chosen = [strat]
            # trader：roster 取 status∈{live,training} 且未被占用者
            traders = []
            for fn in sorted(glob.glob(os.path.join(base, "trainees", "*.json"))):
                try:
                    with open(fn, encoding="utf-8") as f:
                        t = json.load(f)
                except Exception:
                    continue
                if t.get("status") in ("live", "training") and \
                        t.get("id") not in (None, "", "main"):
                    traders.append(t)
            occupied = set()
            hp = os.path.join(base, "hires.json")
            if os.path.exists(hp):
                try:
                    with open(hp, encoding="utf-8") as f:
                        for h in (json.load(f).get("hires") or []):
                            if h.get("status") == "active":
                                occupied.add(h.get("trader"))
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
            for p in pfs:  # 订阅清单 trader 字段并计（防 hires 缺行重复指派）
                if p.get("trader"):
                    occupied.add(p.get("trader"))
            free = sorted((t for t in traders
                           if t.get("id") not in occupied),
                          key=lambda t: t.get("status") != "live")
            if not free:
                self._json({"ok": False,
                            "error": "无可用交易员（live/training 且未被占用）"}, 409)
                return
            trader = free[0].get("id")
            # 组合落账（原子写：复用 subscriptions._save）
            now = _sub._now()
            note = marker
            price, paid = 0, False
            if plan == "paid_auto":
                price, paid = 99, False
                note += " · 收费意向待商业化开通"
            pf = {"id": pf_id, "name": pname or ("迎宾组合 " + pf_id),
                  "strategies": chosen, "price": price, "paid": paid,
                  "trader": None, "note": note, "created_at": now,
                  "services": {"pre_open_report":
                               bool(rec.get("pre_open_report", True)),
                               "post_close_report":
                               bool(rec.get("post_close_report", True))}}
            d["portfolios"].append(pf)
            _sub._save(d)
            # 真雇佣链：portfolio.py hire（hires.json 落 source=<pf_id>）
            warn = []
            _pfpy = (r"C:\Program Files\Python311\python.exe"
                     if os.path.exists(r"C:\Program Files"
                                       r"\Python311\python.exe")
                     else sys.executable)
            _script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   os.pardir, "k_stock", "paper",
                                   "portfolio.py")
            hired = False
            try:
                r = subprocess.run(
                    [_pfpy, "-X", "utf8", _script, "hire",
                     "--pf", pf_id, "--trader", str(trader), "--json"],
                    capture_output=True, timeout=60)
                out = r.stdout.decode("utf-8", "replace").strip()
                data = json.loads(out.splitlines()[-1]) if out else {}
                hired = bool(data.get("ok"))
            except Exception as _e:
                data = {"error": repr(_e)}
            if hired:
                _sub.bind_trader(str(trader), pid=pf_id)
            else:
                warn.append("交易员雇佣未落账：%s"
                            % (data.get("error") or "unknown"))
            # 经理雇佣（复刻 manager_hire 端点形状：子进程 manager.py hire）
            mh = None
            if mid:
                cmd = [self._PAPER_PY,
                       os.path.join(self._PAPER_DIR, "manager.py"),
                       "hire", mid, pf_id, "--json",
                       "--note", "K-STOCK-159 迎宾向导雇佣"[:200]]
                mh = self._manager_run_json(cmd, timeout=35)
                if not mh.get("ok"):
                    warn.append("经理雇佣未落账：%s"
                                % (mh.get("error") or "unknown"))
            self._json({"ok": True, "plan": plan, "portfolio": pf_id,
                        "trader": str(trader) if hired else None,
                        "strategies": chosen, "price": price, "paid": paid,
                        "services": pf["services"],
                        "manager_hire": mh, "warning": "；".join(warn) or None,
                        "guidance": "组合已生成，订阅与雇佣链次日 07:05 主闭环生效"})
        except Exception as e:
            self._json({"ok": False, "error": repr(e)}, 500)

    def _api_paper_direct_open(self, raw):
        """K-STOCK-137f S-d 轻量直订动线：策略市场「直接建仓」。
        纯动线薄层：strategy_id 对 strategies.json 校验后，复用既有建仓
        链路（_paper_run_json → paper cli.py order BUY 即时成交，同
        /api/paper_order 口径）；来源字段 source=direct_lightweight 落
        portal/data/direct_open_log.jsonl，为按动线计费留数据底座。
        蓝图：轻量 = 组合最简退化态（N=1 策略 / M=0 操盘手 / 0 经理）。"""
        try:
            rec = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        sid = str(rec.get("strategy_id") or "").strip()
        if not sid:
            self._json({"ok": False, "error": "strategy_id 必填"}, 400)
            return
        try:
            reg_p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "strategies.json")
            with open(reg_p, encoding="utf-8") as f:
                reg = json.load(f)
            strat = next((x for x in reg.get("strategies") or []
                          if x.get("id") == sid), None)
        except Exception as e:
            self._json({"ok": False, "error": repr(e)}, 500)
            return
        if not strat:
            self._json({"ok": False,
                        "error": "strategy not found: %s" % sid}, 404)
            return
        if strat.get("status") != "live":
            self._json({"ok": False,
                        "error": "策略 %s 为 %s 态，暂不支持直接建仓"
                                 % (sid, strat.get("status"))}, 400)
            return
        stock = str(rec.get("stock") or "").strip().upper()
        if not re.match(r"^\d{6}\.(SH|SZ|BJ)$", stock):
            self._json({"ok": False,
                        "error": "股票代码格式：6位数字.SH/.SZ/.BJ"}, 400)
            return
        qty = rec.get("qty")
        if not isinstance(qty, int) or isinstance(qty, bool) \
                or qty <= 0 or qty % 100:
            self._json({"ok": False, "error": "qty 须为 100 的整数倍股数"}, 400)
            return
        try:
            r = self._paper_run_json(
                [self._PAPER_PY, os.path.join(self._PAPER_DIR, "cli.py"),
                 "order", "--side", "BUY", "--stock", stock,
                 "--qty", str(qty), "--json"])
        except subprocess.TimeoutExpired:
            self._json({"ok": False, "error": "引擎子进程执行超时"}, 504)
            return
        except Exception as e:
            self._json({"ok": False, "error": repr(e)}, 500)
            return
        # 来源标记（按动线计费数据底座）：成败都留痕，留痕失败不阻断主链路
        try:
            log_p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "data", "direct_open_log.jsonl")
            os.makedirs(os.path.dirname(log_p), exist_ok=True)
            with open(log_p, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "source": "direct_lightweight",
                    "strategy_id": sid, "stock": stock, "qty": qty,
                    "ok": bool(r.get("ok")),
                }, ensure_ascii=False) + "\n")
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
        if r.get("ok"):
            r["source"] = "direct_lightweight"
            r["mode_note"] = "轻量模式：无经理无复盘，随时可在策略详情升级雇佣"
        self._json(r, 200 if r.get("ok") else 400)

