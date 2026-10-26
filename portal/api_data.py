# -*- coding: utf-8 -*-
"""P4 批 1：数据域 Mixin（data_health/warehouse/sync 族 + Handler 方法壳）。
消费方显式 import；Handler 壳经基座多继承并入（禁 import *）。
"""
import json
import os
import sys
import threading
import time
from api_common import (
    log_exc,
    pill_nav,
    STATION,
    QBT_DIR,
    DATA_SYNC_CMDS,
    DATA_SYNC_JOBS,
    _FRESH_BG_LOCK,
    _FRESH_BG_ERROR,
    data_freshness_bg,
    _TP_JS,
    _tp_row)


_WAREHOUSE_SERVICES_LOCK = threading.Lock()


_WAREHOUSE_SERVICES = None


_HEALTH_CACHE = {"data": None, "ts": 0, "refreshing": False, "error": None}


_HEALTH_LOCK = threading.Lock()


_KG_HEALTH_CACHE = {"data": None, "ts": 0, "refreshing": False, "error": None}


_KG_HEALTH_LOCK = threading.Lock()


def data_health_bg(ttl_sec=60):
    """返回只读扫描快照；首次或过期扫描由单个后台线程执行。"""
    with _HEALTH_LOCK:
        if (_HEALTH_CACHE["data"] is not None
                and time.monotonic() - _HEALTH_CACHE["ts"] < ttl_sec):
            return dict(_HEALTH_CACHE)
        if _HEALTH_CACHE["refreshing"]:
            return dict(_HEALTH_CACHE)
        _HEALTH_CACHE["refreshing"] = True
        snapshot = dict(_HEALTH_CACHE)

    def refresh():
        try:
            if QBT_DIR not in sys.path:
                sys.path.insert(0, QBT_DIR)
            import data_health
            report = data_health.scan_warehouse()
            if (not isinstance(report, dict)
                    or not {"kpi", "groups", "gaps", "generated_at"}.issubset(report)
                    or not isinstance(report["kpi"], dict)
                    or not {"n_tables", "total_mb", "tdx_sync", "freshest"}.issubset(report["kpi"])
                    or not isinstance(report["groups"], list)
                    or not isinstance(report["gaps"], list)):
                raise ValueError("数据仓扫描结果不完整")
            with _HEALTH_LOCK:
                _HEALTH_CACHE.update(data=report, ts=time.monotonic(), error=None)
        except Exception as exc:
            with _HEALTH_LOCK:
                _HEALTH_CACHE["error"] = str(exc)
        finally:
            with _HEALTH_LOCK:
                _HEALTH_CACHE["refreshing"] = False

    try:
        threading.Thread(target=refresh, daemon=True).start()
    except Exception as exc:
        with _HEALTH_LOCK:
            _HEALTH_CACHE.update(refreshing=False, error=str(exc))
            return dict(_HEALTH_CACHE)
    return snapshot


def data_health_kg_bg(ttl_sec=300):
    """图谱侧卡独立后台刷新，成功或失败均缓存，避免阻塞及重复重试。"""
    with _KG_HEALTH_LOCK:
        if (_KG_HEALTH_CACHE["refreshing"] or
                _KG_HEALTH_CACHE["ts"] and time.monotonic() - _KG_HEALTH_CACHE["ts"] < ttl_sec):
            return dict(_KG_HEALTH_CACHE)
        _KG_HEALTH_CACHE["refreshing"] = True
        snapshot = dict(_KG_HEALTH_CACHE)

    def refresh():
        try:
            if QBT_DIR not in sys.path:
                sys.path.insert(0, QBT_DIR)
            import kg_portal
            stats = kg_portal.api("stats")
            if not isinstance(stats, dict) or not stats.get("ok"):
                raise ValueError(stats.get("error", "图谱核验失败") if isinstance(stats, dict)
                                 else "图谱统计结果不完整")
            if not all(isinstance(stats.get(key), dict) for key in
                       ("nodes", "edges", "freshness", "hm_tags")):
                raise ValueError("图谱统计结果不完整")
            distill = kg_portal.api("distill")
            data = {"stats": stats, "distill": distill if isinstance(distill, dict) else {"ok": False},
                    "coverage": len(kg_portal.candle_coverage())}
            with _KG_HEALTH_LOCK:
                _KG_HEALTH_CACHE.update(data=data, error=None)
        except (Exception, SystemExit) as exc:
            with _KG_HEALTH_LOCK:
                _KG_HEALTH_CACHE["error"] = str(exc)
        finally:
            with _KG_HEALTH_LOCK:
                _KG_HEALTH_CACHE.update(ts=time.monotonic(), refreshing=False)

    try:
        threading.Thread(target=refresh, daemon=True).start()
    except Exception as exc:
        with _KG_HEALTH_LOCK:
            _KG_HEALTH_CACHE.update(ts=time.monotonic(), refreshing=False, error=str(exc))
            return dict(_KG_HEALTH_CACHE)
    return snapshot


def data_freshness(ttl_sec=300):
    """核心表时点校对（复用 quant_backtest/datamart.freshness_report）。

    ttl_sec 透传：驾驶舱用 1800（30min 缓存，冷算 ~100s 只接受启动预热一次），
    数据仓页维持默认 300。"""
    import sys as _sy
    if QBT_DIR not in _sy.path:
        _sy.path.insert(0, QBT_DIR)
    import datamart as _dm
    with _FRESH_BG_LOCK:
        return _dm.freshness_report(ttl_sec=ttl_sec)


def warehouse_update_services():
    """延迟构造安全适配；不初始化任务库、不请求供应商或启动采集。"""
    global _WAREHOUSE_SERVICES
    with _WAREHOUSE_SERVICES_LOCK:
        if _WAREHOUSE_SERVICES is None:
            if QBT_DIR not in sys.path:
                sys.path.insert(0, QBT_DIR)
            from warehouse_update_services import make_services
            _WAREHOUSE_SERVICES = make_services(STATION)
        return _WAREHOUSE_SERVICES


def warehouse_legacy_status(src):
    """旧响应字段兼容，但不从未关联的历史日志倒推本次任务。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    services = warehouse_update_services()
    selected = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    runs = [run for run in services['store'].list_runs(selected) if run['plan']['source']==src]
    if not runs:
        return dict(running=False,pid=None,exit=None,tail='',error=None,run_id=None,
                    legacy_log_association='unassociated',process_status='unknown')
    run = runs[0]
    log = services['launcher'].read_log(run['run_id'],offset=0,limit=4096)
    return dict(running=run['status'] in {'queued','running','verifying'},pid=run['pid'],
                exit=run['exit_code'],tail=log['text'][-1200:],error=None,run_id=run['run_id'],
                status=run['status'],progress=run['progress'],metrics=run['metrics'],
                process_status=run['process_status'])


def data_sync_status(src):
    """轮询：进程是否在跑 + 日志尾部。"""
    if src not in DATA_SYNC_CMDS:
        return {"error": "bad src"}
    if src in {'tdx', 'tushare'}:
        try:
            return warehouse_legacy_status(src)
        except Exception:
            return dict(running=None,pid=None,exit=None,tail='',run_id=None,
                        error='任务状态暂不可用，请重新查询')
    job = DATA_SYNC_JOBS.get(src)
    running = bool(job and job["p"].poll() is None)
    tail = ""
    log = os.path.join(r"C:\Temp", "portal_data_%s.log" % src)
    try:
        if os.path.exists(log):
            with open(log, "rb") as f:
                f.seek(0, 2)
                sz = f.tell()
                f.seek(max(0, sz - 4000))
                tail = f.read().decode("utf-8", "replace").strip()[-1200:]
    except Exception:
        pass
    rc = job["p"].poll() if job else None
    if job and rc is not None and not job.get("cache_cleared"):
        # 同步任务刚结束：失效 freshness 缓存，页面刷新即见新时点
        job["cache_cleared"] = True
        try:
            import sys as _sy
            if QBT_DIR not in _sy.path:
                _sy.path.insert(0, QBT_DIR)
            import datamart as _dm
            _dm._fresh_cache.update(ts=0.0, data=None)
            job["clear_err"] = None
        except Exception as ex:
            job["clear_err"] = repr(ex)
    return {"running": running, "exit": rc, "tail": tail,
            "cleared": bool(job.get("cache_cleared")) if job else None,
            "clear_err": job.get("clear_err", "") if job else None}


def _news_db():
    """news.db 轻量统计：今日采集/命中/已判分布 + 最近已判断条目。"""
    out = {"today": 0, "hit": 0, "dist": {"bull": 0, "bear": 0,
                                          "neutral": 0}, "recent": []}
    try:
        import sqlite3
        db = sqlite3.connect(os.path.join(STATION, "news_collector",
                                          "news.db"))
        out["today"] = db.execute(
            "SELECT COUNT(*) FROM news WHERE "
            "created_at>=date('now','localtime')").fetchone()[0]
        out["hit"] = db.execute(
            "SELECT COUNT(*) FROM news WHERE hit=1 AND "
            "created_at>=date('now','localtime')").fetchone()[0]
        for (j,) in db.execute(
                "SELECT jev FROM news WHERE jev IS NOT NULL AND "
                "created_at>=date('now','localtime')"):
            try:
                b = json.loads(j).get("bias", "neutral")
                out["dist"][b] = out["dist"].get(b, 0) + 1
            except Exception:
                log_exc("_news_db:jev 分布行解析")   # P0-5
        for ts, title, j in db.execute(
                "SELECT ts, title, jev FROM news WHERE jev IS NOT NULL "
                "ORDER BY id DESC LIMIT 12"):
            try:
                d = json.loads(j)
                out["recent"].append({"ts": ts, "title": title[:60],
                                      "bias": d.get("bias", "neutral"),
                                      "bias_cn": d.get("bias_cn", ""),
                                      "conf": d.get("conf", 0),
                                      "scope_cn": d.get("scope_cn", "")})
            except Exception:
                log_exc("_news_db:recent 行解析")  # C2批1
        db.close()
    except Exception:
        log_exc("_news_db:news.db 连接/统计")  # C2批1
    return out



class Handler:  # Mixin 壳：经基座多继承并入
    # ── 数据仓健康盘点（只读扫描，不改任何数据文件） ──
    def _data_health_page(self):
        import html as _h
        from warehouse_dashboard import render_dashboard
        e = _h.escape
        state = data_health_bg()
        d = state["data"]
        if d is None:
            message = ("扫描器异常：" + state["error"] if state["error"]
                       else "核验中，尚无完整扫描结果。")
            self._html("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                       "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                       "<meta http-equiv='refresh' content='10'><title>数据仓</title>"
                       + self._COCKPIT_CSS + "</head><body>"
                       + pill_nav("data-health") + "<h1>📊 数据仓 · 全部资产</h1>"
                       + "<div class='dim'>%s</div></body></html>" % e(message))
            return
        # 文件时点色点只表示新鲜度，不代表完整度。
        def _dot(age):
            if age is None:
                return "<span style='color:var(--text-dim)'>●</span>"
            color = "var(--color-ok)" if age <= 26 else "var(--color-warn)" if age <= 72 else "var(--color-err)"
            return "<span style='color:%s' title='%.0fh 前'>●</span>" % (color, age)

        # 数据时点校对（datamart.freshness_report 服务端渲染）：
        # 回答"数据停在几号 / 应到几号 / 滞后几天"
        try:
            fr = data_freshness_bg(ttl_sec=300)
            if fr is None:
                fr = {"expect": None, "rows": [], "err": _FRESH_BG_ERROR or "核验中"}
        except Exception as ex:
            log_exc("_data_health_page:freshness")  # C2批1
            fr = {"expect": None, "rows": [], "err": str(ex)}
        fresh_notice = ""
        if fr.get("cache_stale"):
            message = ("时点重新核验中；当前为上次结果。" if fr.get("refreshing")
                       else "时点刷新失败；当前为上次结果。")
            if fr.get("refresh_error"):
                message += " " + fr["refresh_error"]
            fresh_notice = "<div class='dim'>%s</div>" % e(message)
        fr_rows = []
        for x in fr.get("rows") or []:
            st = x["status"]
            col = ("var(--color-ok)" if st == "ok" else
                   "var(--text-dim)" if st in ("nocal",) else
                   "var(--color-warn)" if st.startswith("lag-") else "var(--color-err)")
            lag_txt = "—" if x["lag"] is None else \
                ("%d 交易日" % x["lag"] if x["lag"] else "0")
            fr_rows.append(
                "<tr><td><span style='cursor:pointer;margin-right:6px' "
                "title='穿透看数据' onclick=\"tp_toggle('%s')\">🔍</span>%s"
                "</td><td class='dim'>%s</td>"
                "<td style='color:%s'>%s</td><td>%s</td>"
                "<td class='dim'>%s（容忍 %d）</td></tr>%s"
                % (e(x["table"]), e(x["table"]), e(x["group"]), col, e(st),
                   e(x["latest"] or "空"), lag_txt, x["tol"],
                   _tp_row(x["table"])))
        fresh_html = (
            "<div class='sec'><h2>⏱ 数据时点校对</h2>"
            + fresh_notice +
            "<div class='dim'>期望更新日 = %s（最近已收盘 A 股交易日）· "
            "滞后按交易日计 · 跨市场日期先向下映射到 A 股日历</div>"
            "<table><tr><th>表</th><th>分组</th><th>状态</th>"
            "<th>数据停在</th><th>滞后</th></tr>%s</table></div>"
            % (e(fr.get("expect") or ("日历不可用：" + fr.get("err", ""))
                 if fr.get("err") else fr.get("expect") or "未知"),
               "".join(fr_rows)))

        # 图谱统计也可能很慢，完整页渲染只消费后台快照。
        try:
            kg_state = data_health_kg_bg()
        except Exception as ex:
            kg_state = {"data": None, "refreshing": False, "error": str(ex)}
        kg_snapshot = kg_state["data"]
        _ks = kg_snapshot["stats"] if kg_snapshot is not None else {"ok": False}
        kg_notice = ""
        if kg_snapshot is not None and (kg_state["refreshing"] or kg_state["error"]):
            message = ("图谱重新核验中；当前为上次结果。" if kg_state["refreshing"]
                       else "图谱刷新失败；当前为上次结果。")
            if kg_state["error"]:
                message += " " + kg_state["error"]
            kg_notice = "<div class='dim'>%s</div>" % e(message)
        if _ks.get("ok"):
            import datetime as _dtm

            def _kg_age(ymd):
                try:
                    return (_dtm.datetime.now() - _dtm.datetime.strptime(
                        str(ymd), "%Y%m%d")).total_seconds() / 3600
                except Exception:
                    return None
            _kn, _ke = _ks["nodes"], _ks["edges"]
            _kf, _kt = _ks["freshness"], _ks["hm_tags"]
            _di = kg_snapshot["distill"]
            if _di.get("ok"):
                _v = _di.get("verdict", {})
                _di_row = ("蒸馏样本", _di["n"],
                           "H2 %d · H1 %d · H3 %d · 无收敛 %d（ret5 监督 %d，"
                           "盘口特征 %d）"
                           % (_v.get("H2_funds", 0), _v.get("H1_event", 0),
                              _v.get("H3_game", 0),
                              _v.get("insufficient", 0),
                              _di.get("n_with_ret5", 0),
                              _di.get("cd_pre5_hit", 0)),
                           _di.get("generated_at"), None)
            else:
                _di_row = ("蒸馏样本", "未构建", "kg_distill.py build",
                           "—", None)
            _kg_rows = "".join(
                "<tr><td>%s</td><td>%s</td><td class='dim'>%s</td>"
                "<td>%s%s</td></tr>"
                % (lbl, e(str(cnt)), e(edge), e(latest or "—"), _dot(age))
                for lbl, cnt, edge, latest, age in (
                    ("缠论结构信号", _ke.get("HAS_STRUCTURE", 0),
                     "盘面事件 %d" % _kn.get("MarketEvent", 0),
                     _kf.get("me"), _kg_age(_kf.get("me"))),
                    ("盘口事件（分时）", _ke.get("HAS_CANDLE", 0),
                     "覆盖 %d 标的（分钟库·非全市场）"
                     % kg_snapshot["coverage"],
                     _kf.get("cd"), _kg_age(_kf.get("cd"))),
                    ("外部事件传导", _ke.get("DRIVE", 0),
                     "事件流 %d" % _kn.get("Event", 0),
                     _kf.get("ee"), _kg_age(_kf.get("ee"))),
                    ("龙虎榜净买", _ke.get("TRADED_ON", 0),
                     "%d 主体 · %d 席位" % (_kn.get("HotMoney", 0),
                                             _kn.get("Seat", 0)),
                     _kf.get("te"), _kg_age(_kf.get("te"))),
                    _di_row))
            kg_html = (
                "<div class='sec'><h2>🕸 知识图谱时点</h2>"
                + kg_notice +
                "<table><tr><th>资产</th><th>规模</th><th>构成</th>"
                "<th>最新时点</th></tr>%s</table>"
                "<div class='dim' style='margin-top:4px'>影子标注：跟随确认 "
                "%d · 诱多 %d（M2-W4c 口径）· 入口 "
                "<a href='/kg'>图谱探索</a> / <a href='/kg/map'>游资地图"
                "</a> / <a href='/kg/shadow'>影子面板</a></div></div>"
                % (_kg_rows, _kt.get("follow_confirm", 0),
                   _kt.get("lure_warn", 0)))
        else:
            message = "图谱核验中" if kg_state["refreshing"] else (
                "图谱核验失败：" + (kg_state["error"] or "未取得有效结果"))
            kg_html = ("<div class='sec'><h2>🕸 知识图谱时点</h2>"
                       "<div class='dim'>%s</div></div>" % e(message))

        self._html(render_dashboard(
            d, state, freshness_html=fresh_html, graph_html=kg_html,
            nav=pill_nav("data-health"), base_css=self._COCKPIT_CSS,
            preview_js=_TP_JS))


    def _warehouse_update_request(self, method, path, qs='', raw=b''):
        """新接口复用全站认证，正文大小在读取前已受限制。"""
        from urllib.parse import parse_qs
        try:
            if method == 'POST' and self.headers.get('Origin') is None and not self._is_local_req():
                self._json({'error':'无Origin的更新请求仅允许可信本机脚本'},403)
                return
            if method == 'POST' and self.headers.get('Content-Type','').split(';',1)[0].strip().lower() != 'application/json':
                self._json({'error':'每日更新只接受application/json'},415)
                return
            from warehouse_update_api import handle_request
            code, payload = handle_request(method,path,parse_qs(qs,keep_blank_values=True),raw,
                origin=self.headers.get('Origin'),host=self.headers.get('Host'),
                authenticated=self._check_portal_token(),services=warehouse_update_services())
            self._json(payload,code)
        except Exception:
            self._json({'error':'每日服务未就绪，未确认任务启动'},503)


