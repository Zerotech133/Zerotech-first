# -*- coding: utf-8 -*-
"""HTTP 服务层（路由）。

从原 tdx_monitor_system.py 的 Handler 与两个调度线程迁出，行为不变。
顺手修复两个存量 bug：
1. do_POST /api/data/download 分支里 logger 未定义（NameError）→ 改 logging
2. POST /api/models、POST /api/stocks、DELETE /api/models/*、DELETE /api/stocks/*
   四条路由调用的 SYS.add_model/add_stock/del_model/del_stock 方法不存在
   （分组化重构时的遗留死代码，前端也不调用）→ 直接删除路由
"""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tdx_speed_alert as m
import minute_collector as mc
import stock_news_collector as snc
from stock_data_cache import CACHE

from .common import HOST, PORT, WEB_REFRESH_MS
from .ui import render_html, render_advanced

# 全局单例（由 facade 注入，替代原模块级 SYS = MonitorSystem()）
SYS = None


def _get_bt():
    """回测引擎延迟导入（避免启动时加载 pandas/numpy 影响监控服务）。"""
    import backtest_engine
    return backtest_engine


class Handler(BaseHTTPRequestHandler):
    # 升级到 HTTP/1.1 解决 Java hutool HttpRequest "Unexpected end of file from server" 问题
    # HTTP/1.0 默认连接关闭，hutool 用 HTTP/1.1 期望 keep-alive，导致响应被截断
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        self.close_connection = True  # 强制关闭连接，确保 hutool 能正确读到 EOF

    def _send_html(self):
        self._send(200, render_html(WEB_REFRESH_MS), 'text/html; charset=utf-8')

    def _body_json(self):
        n = int(self.headers.get('Content-Length', 0) or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode('utf-8'))

    def do_GET(self):
        p = self.path.split('?')[0]
        if p == '/' or p.startswith('/model/'):
            self._send_html()
            return
        if p == '/advanced':
            # 旧版完整功能页（数据下载/回测/板块/股票管理）
            self._send(200, render_advanced(), 'text/html; charset=utf-8')
            return
        if p == '/api/models':
            return self._send(200, SYS.api_models())
        if p == '/api/stocks':
            return self._send(200, SYS.api_stocks())
        if p == '/api/groups':
            return self._send(200, SYS.api_groups())
        if p == '/api/qmt/status':
            return self._send(200, SYS.api_qmt_status())
        if p == '/api/qmt/strategies':
            return self._send(200, {'strategies': SYS.api_qmt_strategies()})
        if p == '/api/status':
            s = SYS.api_status()
            s['rt_min'] = mc.get_status()
            s['stock_info'] = snc.COLLECTOR.status()
            return self._send(200, s)
        if p.startswith('/api/model/'):
            mid = p[len('/api/model/'):]
            d = SYS.api_model_detail(mid)
            return self._send(200 if d else 404, d or {'error': 'model not found'})
        if p == '/api/data/status':
            return self._send(200, CACHE.get_status())
        if p == '/api/data/progress':
            return self._send(200, CACHE.progress())
        if p.startswith('/api/data/table/'):
            from urllib.parse import parse_qs
            q = parse_qs(self.path.split('?', 1)[1]) if '?' in self.path else {}
            tbl = p[len('/api/data/table/'):]
            code = q.get('code', [None])[0]
            start_date = q.get('start_date', [None])[0]
            end_date = q.get('end_date', [None])[0]
            limit = int(q.get('limit', ['100'])[0])
            offset = int(q.get('offset', ['0'])[0])
            try:
                return self._send(200, CACHE.query(tbl, code=code, limit=limit, offset=offset,
                                                   start_date=start_date, end_date=end_date))
            except Exception as e:
                return self._send(400, {'error': str(e)})
        # ===== 通达信板块数据 GET 端点 =====
        if p == '/api/tdx-sector/status':
            import tdx_sector_data as tsd
            return self._send(200, {'tables': tsd.get_status()})
        if p.startswith('/api/tdx-sector/'):
            from urllib.parse import parse_qs
            q = parse_qs(self.path.split('?', 1)[1]) if '?' in self.path else {}
            import tdx_sector_data as tsd
            import sqlite3 as _sq
            sub = p[len('/api/tdx-sector/'):]
            try:
                if sub == 'list':
                    # 板块列表（含定义+最新行情）：?seg=&kw=&limit=
                    seg = q.get('seg', [None])[0]
                    kw = q.get('kw', [None])[0]
                    limit = int(q.get('limit', ['100'])[0])
                    conn = _sq.connect(tsd.DB_PATH)
                    try:
                        # 最新交易日收盘 + 前一交易日收盘 → 涨跌幅
                        sql = ('SELECT d.code, d.name, d.seg, '
                               't.close, t.pre_close, '
                               'ROUND((t.close/CAST(NULLIF(t.pre_close,0) AS REAL)-1)*100,2) AS pct '
                               'FROM tdx_sector_def d LEFT JOIN ('
                               '  SELECT a.code, a.close AS close, b.close AS pre_close '
                               '  FROM tdx_sector_daily a JOIN tdx_sector_daily b '
                               '    ON a.code=b.code AND b.trade_date=('
                               '      SELECT MAX(trade_date) FROM tdx_sector_daily c '
                               "      WHERE c.code=a.code AND c.trade_date<a.trade_date) "
                               '  WHERE a.trade_date=(SELECT MAX(trade_date) FROM tdx_sector_daily)) t '
                               '  ON t.code=d.code ')
                        where, params = [], []
                        if seg:
                            where.append('d.seg=?')
                            params.append(seg)
                        if kw:
                            where.append('(d.name LIKE ? OR d.code LIKE ?)')
                            params.extend([f'%{kw}%', f'%{kw}%'])
                        if where:
                            sql += 'WHERE ' + ' AND '.join(where) + ' '
                        sql += 'ORDER BY d.code LIMIT ?'
                        params.append(min(limit, 2000))
                        rows = conn.execute(sql, params).fetchall()
                        return self._send(200, {'sectors': [
                            {'code': r[0], 'name': r[1], 'seg': r[2],
                             'close': r[3], 'pre_close': r[4], 'pct': r[5]} for r in rows]})
                    finally:
                        conn.close()
                if sub == 'rank':
                    # 板块涨幅排名：?seg=industry|concept|style|region|old_industry|stat
                    #              |industry_detail&top=20&date=YYYYMMDD
                    # 2026-08-18: seg=industry 默认返回官方 56 个一级行业(industry_l1，
                    # 与手机端通达信"行业板块"一致)；细分行业用 seg=industry_detail；
                    # level 参数保留兼容（不再有聚合语义）
                    seg = q.get('seg', ['industry'])[0]
                    top = int(q.get('top', ['20'])[0])
                    date = q.get('date', [None])[0]
                    return self._send(200, tsd.query_rank(top_n=top, trade_date=date,
                                                           seg=seg))
                if sub == 'members':
                    # 行业成分股（含当日行情）：?code=881385&date=YYYYMMDD
                    code = q.get('code', [None])[0]
                    if not code:
                        return self._send(400, {'error': 'code required'})
                    date = q.get('date', [None])[0]
                    return self._send(200, tsd.query_members(code, trade_date=date))
                if sub == 'detail':
                    # 板块详情：?code=880966 → 定义+最新行情+近N日日线
                    code = q.get('code', [None])[0]
                    if not code:
                        return self._send(400, {'error': 'code required'})
                    days = int(q.get('days', ['60'])[0])
                    conn = _sq.connect(tsd.DB_PATH)
                    try:
                        d = conn.execute('SELECT code,name,seg FROM tdx_sector_def WHERE code=?',
                                         (code,)).fetchone()
                        if not d:
                            return self._send(404, {'error': 'sector not found'})
                        daily = conn.execute(
                            'SELECT trade_date,open,high,low,close,vol,amount '
                            'FROM tdx_sector_daily WHERE code=? ORDER BY trade_date DESC LIMIT ?',
                            (code, days)).fetchall()
                        members = conn.execute(
                            'SELECT con_code FROM tdx_sector_member WHERE block_name=? ORDER BY con_code',
                            (d[1],)).fetchall()
                        return self._send(200, {
                            'sector': {'code': d[0], 'name': d[1], 'seg': d[2]},
                            'daily': [{'trade_date': r[0], 'open': r[1], 'high': r[2], 'low': r[3],
                                       'close': r[4], 'vol': r[5], 'amount': r[6]} for r in daily],
                            'member_count': len(members),
                            'members': [r[0] for r in members[:200]]})
                    finally:
                        conn.close()
                if sub == 'minutes':
                    # 板块当日分钟线：?code=880966 → 240根1分钟K线(含涨跌家数)
                    code = q.get('code', [None])[0]
                    if not code:
                        return self._send(400, {'error': 'code required'})
                    conn = _sq.connect(tsd.DB_PATH)
                    try:
                        last_day = conn.execute(
                            'SELECT MAX(trade_date) FROM tdx_sector_min WHERE code=?', (code,)).fetchone()[0]
                        if not last_day:
                            return self._send(200, {'code': code, 'date': None, 'minutes': []})
                        rows = conn.execute(
                            'SELECT trade_date,trade_time,open,high,low,close,vol,amount,up_count,down_count '
                            'FROM tdx_sector_min WHERE code=? AND trade_date=? ORDER BY trade_time',
                            (code, last_day)).fetchall()
                        return self._send(200, {'code': code, 'date': last_day,
                            'minutes': [{'time': r[1], 'open': r[2], 'high': r[3], 'low': r[4],
                                         'close': r[5], 'vol': r[6], 'amount': r[7],
                                         'up_count': r[8], 'down_count': r[9]} for r in rows]})
                    finally:
                        conn.close()
                return self._send(404, {'error': f'unknown sub: {sub}'})
            except Exception as e:
                return self._send(400, {'error': str(e)})
        # ===== 回测引擎 GET 端点 =====
        if p == '/api/backtest/health':
            return self._send(200, _get_bt().health_check())
        if p == '/api/backtest/strategy/templates':
            from strategy_templates import list_templates
            return self._send(200, {'templates': list_templates()})
        if p == '/api/backtest/factors':
            return self._send(200, {'factors': _get_bt().get_all_factor_names()})
        if p == '/api/backtest/factors/info':
            return self._send(200, {'factors': _get_bt().get_factor_info()})
        if p == '/api/backtest/data-sources':
            # 返回本地 SQLite 数据源覆盖情况
            try:
                bt = _get_bt()
                conn = bt._conn()
                sources = []
                check_tables = [
                    ('ts_daily', 'trade_date', '日K行情'),
                    ('ts_daily_basic', 'trade_date', '每日指标'),
                    ('ts_moneyflow', 'trade_date', '资金流向'),
                    ('ts_margin_detail', 'trade_date', '融资融券'),
                    ('ts_moneyflow_hsgt', 'trade_date', '北向资金'),
                    ('ts_income', 'end_date', '利润表'),
                    ('ts_balancesheet', 'end_date', '资产负债表'),
                    ('ts_cashflow', 'end_date', '现金流量表'),
                    ('ts_fina_indicator', 'end_date', '财务指标'),
                    ('ts_top_list', 'trade_date', '龙虎榜'),
                    ('ts_top_inst', 'trade_date', '龙虎榜机构席位'),
                    ('ts_index_daily', 'trade_date', '指数日线'),
                    ('ts_ths_daily', 'trade_date', '板块日线'),
                    ('ts_rt_min', 'time', '实时分钟线'),
                ]
                for tbl, dc, cn in check_tables:
                    try:
                        cnt = conn.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
                        r = conn.execute(f'SELECT MIN({dc}),MAX({dc}) FROM {tbl}').fetchone()
                        sources.append({'table': tbl, 'name_cn': cn, 'rows': cnt,
                                        'date_min': r[0], 'date_max': r[1]})
                    except Exception:
                        sources.append({'table': tbl, 'name_cn': cn, 'rows': 0})
                conn.close()
                return self._send(200, {'sources': sources})
            except Exception as e:
                return self._send(400, {'error': str(e)})
        # 另类数据按类型查询：/api/backtest/data-sources/{dataType}?code=&start_date=&end_date=
        if p.startswith('/api/backtest/data-sources/') and p != '/api/backtest/data-sources':
            from urllib.parse import parse_qs
            data_type = p[len('/api/backtest/data-sources/'):]
            q = parse_qs(self.path.split('?', 1)[1]) if '?' in self.path else {}
            code = q.get('code', [None])[0]
            start_date = q.get('start_date', ['20230101'])[0]
            end_date = q.get('end_date', [''])[0]
            if not end_date:
                import datetime
                end_date = datetime.datetime.now().strftime('%Y%m%d')
            try:
                result = _get_bt().query_data_source(data_type, code, start_date, end_date)
                return self._send(200, result)
            except Exception as e:
                return self._send(400, {'error': str(e)})
        # ===== 涨停统计（本地日线精确判定，非龙虎榜近似） =====
        # ?days=20&min_count=4&top=50&code=002156.SZ
        if p == '/api/backtest/limit-up-stats':
            from urllib.parse import parse_qs
            q = parse_qs(self.path.split('?', 1)[1]) if '?' in self.path else {}
            days = int(q.get('days', ['20'])[0])
            min_count = int(q.get('min_count', ['1'])[0])
            top = int(q.get('top', ['50'])[0])
            code = q.get('code', [None])[0]
            try:
                return self._send(200, _get_bt().query_limit_up_stats(
                    days=days, min_count=min_count, top_n=top, code=code))
            except Exception as e:
                return self._send(400, {'error': str(e)})
        # ===== TDX 公式语法/算子说明（LLM 自助排查用） =====
        if p == '/api/backtest/screen/help':
            return self._send(200, {
                'vars': 'C收盘 O开盘 H最高 L最低 V成交量 AMO成交额 PREC昨收 PCT涨跌幅 ISUP收阳 ISDOWN收阴',
                'funcs': ['MA', 'EMA', 'SMA', 'REF', 'HHV', 'LLV', 'SUM', 'COUNT',
                          'EVERY', 'EXIST', 'STD', 'AVEDEV', 'ABS', 'MAX', 'MIN',
                          'POW', 'SQRT', 'BETWEEN', 'IF', 'CROSS', 'BARSLAST',
                          'VALUEWHEN', 'BARSCOUNT', 'HHVALL', 'LLVALL', 'CODELIKE',
                          'NAMELIKE'],
                'syntax': 'X:=expr 中间变量; X:expr 输出行; AND OR NOT; {} 或 // 注释; '
                          '输出行应为布尔序列（AND 组合各条件）',
                'example': 'MA5:=MA(C,5); MA10:=MA(C,10); XG:CROSS(MA5,MA10) AND C>=5 AND NOT(CODELIKE(\'9\'));'
            })
        if p.startswith('/api/backtest/factor/analyze/'):
            from urllib.parse import parse_qs
            factor_name = p[len('/api/backtest/factor/analyze/'):]
            q = parse_qs(self.path.split('?', 1)[1]) if '?' in self.path else {}
            codes = q.get('codes', [''])[0].split(',')
            start_date = q.get('start_date', ['20240101'])[0]
            end_date = q.get('end_date', ['20241231'])[0]
            forward = int(q.get('forward', ['5'])[0])
            try:
                result = _get_bt().analyze_factor(factor_name, codes, start_date, end_date, forward)
                return self._send(200, result)
            except Exception as e:
                return self._send(400, {'error': str(e)})
        if p.startswith('/api/backtest/indicator/'):
            from urllib.parse import parse_qs
            code = p[len('/api/backtest/indicator/'):]
            q = parse_qs(self.path.split('?', 1)[1]) if '?' in self.path else {}
            start_date = q.get('start_date', ['20240101'])[0]
            end_date = q.get('end_date', ['20241231'])[0]
            indicators = q.get('indicators', ['MA,MACD,RSI,KDJ,BOLL'])[0].split(',')
            try:
                result = _get_bt().calculate_indicators(code, start_date, end_date, indicators)
                return self._send(200, result)
            except Exception as e:
                return self._send(400, {'error': str(e)})
        self._send(404, {'error': 'not found'})

    def do_POST(self):
        p = self.path.split('?')[0]
        try:
            data = self._body_json()
            if p == '/api/groups':
                g = SYS.add_group(data)
                return self._send(200, g)
            if p == '/api/model/params':
                # 简化前端：更新模型参数（threshold/window/cooldown）
                s = SYS.update_model_params(data.get('id'), data)
                return self._send(200, s)
            if p == '/api/groups/update':
                g = SYS.update_group(data.get('id'), data)
                return self._send(200, g)
            if p.startswith('/api/groups/') and p.endswith('/stocks'):
                # /api/groups/{gid}/{side}/stocks
                parts = p.split('/')
                gid = parts[3]
                side = parts[4]
                s = SYS.add_stock_to_group(gid, side, data)
                return self._send(200, s)
            if p == '/api/qmt/sync':
                r = SYS.sync_qmt_now()
                return self._send(200, {'ok': True, 'result': r})
            if p == '/api/data/download':
                tables = data.get('tables')
                CACHE.start_download(tables)
                # 同时触发公告/新闻/研报即时采集
                try:
                    snc.COLLECTOR.collect_all_once()
                except Exception as e:
                    logging.warning('collect_all_once error: %s', e)   # 修复：原 logger 未定义
                return self._send(200, {'ok': True, 'msg': '全量下载已启动（含公告/新闻/研报采集）'})
            if p == '/api/data/update':
                CACHE.start_download()  # 复用全量下载逻辑（含.meta增量补拉）
                return self._send(200, {'ok': True, 'msg': '增量更新已启动'})
            if p == '/api/data/stop':
                CACHE.stop()
                return self._send(200, {'ok': True, 'msg': '已请求停止'})
            if p == '/api/data/token':
                CACHE.set_token(data.get('token', ''))
                return self._send(200, {'ok': True, 'msg': 'token 已保存'})
            # ===== 回测引擎 POST 端点 =====
            if p == '/api/backtest/run':
                bt = _get_bt()
                result = bt.run_backtest(
                    codes=data.get('codes', []),
                    start_date=data.get('start_date', '20240101'),
                    end_date=data.get('end_date', '20241231'),
                    initial_cash=data.get('initial_cash', 1000000),
                    strategy=data.get('strategy', 'ma_cross'),
                    params=data.get('params', {}),
                    benchmark=data.get('benchmark', '000300.SH'),
                )
                return self._send(200, result)
            # ===== TDX 公式全市场选股（公式原文直跑，方案B·接口封闭） =====
            # body: {formula, date?, top?, include_st?, include_bj?}
            # formula 含中文/分号/括号，POST body 传输避免 URL 编码问题
            if p == '/api/backtest/screen':
                bt = _get_bt()
                result = bt.run_screener(
                    formula=str(data.get('formula', '')),
                    date=data.get('date'),
                    top_n=int(data.get('top', 100)),
                    include_st=bool(data.get('include_st', False)),
                    include_bj=bool(data.get('include_bj', False)))
                return self._send(200, result)
            if p == '/api/backtest/factor/analyze':
                bt = _get_bt()
                result = bt.analyze_factor(
                    factor_name=data.get('factor_name', 'alpha_006'),
                    codes=data.get('codes', []),
                    start_date=data.get('start_date', '20240101'),
                    end_date=data.get('end_date', '20241231'),
                    forward_period=data.get('forward_period', 5),
                )
                return self._send(200, result)
            if p == '/api/backtest/optimize':
                bt = _get_bt()
                result = bt.optimize_portfolio(
                    codes=data.get('codes', []),
                    start_date=data.get('start_date', '20240101'),
                    end_date=data.get('end_date', '20241231'),
                    method=data.get('method', 'mean_variance'),
                    params=data.get('params', {}),
                )
                return self._send(200, result)
            if p == '/api/backtest/strategy/execute':
                bt = _get_bt()
                daily_data = bt.load_daily(
                    data.get('codes', []),
                    data.get('start_date', '20240101'),
                    data.get('end_date', '20241231'),
                )
                result = bt.execute_strategy_code(data.get('code', ''), daily_data)
                return self._send(200, result)
            if p == '/api/backtest/validate':
                bt = _get_bt()
                result = bt.validate_strategy(
                    codes=data.get('codes', []),
                    start_date=data.get('start_date', '20240101'),
                    end_date=data.get('end_date', '20241231'),
                    strategy=data.get('strategy', 'ma_cross'),
                    params=data.get('params', {}),
                    method=data.get('method', 'walk_forward'),
                    config=data.get('config', {}),
                )
                return self._send(200, result)

            # ---- 联合回测 API ----
            if p == '/api/backtest/joint':
                bt = _get_bt()
                result = bt.joint_backtest(
                    selection_config=data.get('selection_config'),
                    trade_config=data.get('trade_config'),
                    mode=data.get('mode', 'JOINT'),
                    start_date=data.get('start_date', '20240101'),
                    end_date=data.get('end_date', '20241231'),
                    initial_cash=data.get('initial_cash', 1000000),
                    benchmark=data.get('benchmark', '000300.SH'),
                    cost_params=data.get('cost_params'),
                )
                return self._send(200, result)

            # ---- 单期选股 API (实时选股, 非全周期回测) ----
            if p == '/api/backtest/select':
                bt = _get_bt()
                selection_config = data.get('selection_config', {})
                # 补充默认值
                selection_config.setdefault('universe', data.get('universe', 'full_a'))
                selection_config.setdefault('rebalance_days', data.get('rebalance_days', 1))
                selection_config.setdefault('top_n', data.get('top_n', 10))
                selection_config.setdefault('exclude_st', data.get('exclude_st', True))
                selection_config.setdefault('exclude_new_days', data.get('exclude_new_days', 30))
                target_date = data.get('date', None)  # None表示最新交易日
                result = bt.execute_selection(selection_config, target_date)
                return self._send(200, result)

            # ---- 策略模板 API ----
            if p == '/api/backtest/strategy/templates':
                from strategy_templates import list_templates
                return self._send(200, {'templates': list_templates()})

            if p == '/api/backtest/strategy/template/run':
                from strategy_templates import run_template_backtest
                result = run_template_backtest(
                    template_key=data.get('template', ''),
                    codes=data.get('codes', []),
                    start_date=data.get('start_date', '20240101'),
                    end_date=data.get('end_date', '20241231'),
                    params=data.get('params', {}),
                    initial_capital=data.get('initial_capital', 1000000),
                    commission_rate=data.get('commission_rate', 0.0003),
                    slippage=data.get('slippage', 0.001),
                    stamp_tax=data.get('stamp_tax', 0.001),
                )
                return self._send(200, result)

            return self._send(404, {'error': 'not found'})
        except Exception as e:
            return self._send(400, {'error': str(e)})

    def do_DELETE(self):
        p = self.path.split('?')[0]
        try:
            if p.startswith('/api/groups/'):
                # /api/groups/{gid}  或  /api/groups/{gid}/{side}/stocks/{code}
                parts = p.split('/')
                if len(parts) == 4:
                    SYS.del_group(parts[3])
                    return self._send(200, {'ok': True})
                elif len(parts) == 7:
                    gid = parts[3]
                    side = parts[4]
                    code = parts[6]
                    SYS.del_stock_from_group(gid, side, code)
                    return self._send(200, {'ok': True})
            return self._send(404, {'error': 'not found'})
        except Exception as e:
            return self._send(400, {'error': str(e)})


# ===================== 定时调度线程 =====================
def _daily_update_scheduler():
    """每日17:00自动增量更新股票数据（仅交易日执行）。
    daemon 线程，服务启动即开始运行。"""
    import time as _time
    import datetime as _dt
    while True:
        try:
            now = _dt.datetime.now()
            # 判断是否交易日（周末直接跳过，节假日查HOLIDAYS）
            is_holiday = now.strftime('%Y-%m-%d') in m.HOLIDAYS if m.HOLIDAYS else False
            if now.weekday() >= 5 or is_holiday:
                # 非交易日，等到明天再检查
                tomorrow = (now + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0)
                _time.sleep((tomorrow - now).total_seconds() + 10)
                continue

            # 计算到今天17:00的等待时间
            target = now.replace(hour=17, minute=0, second=0, microsecond=0)
            wait = (target - now).total_seconds()
            if wait > 0:
                _time.sleep(wait)

            # 再次确认是交易日（sleep跨天后日期可能变）
            check = _dt.datetime.now()
            check_holiday = check.strftime('%Y-%m-%d') in m.HOLIDAYS if m.HOLIDAYS else False
            if check.weekday() >= 5 or check_holiday:
                continue

            logging.info('⏰ 17:00 定时增量更新启动（交易日）')
            CACHE.start_download()  # 复用全量下载逻辑（含.meta增量补拉，16线程并发）
            # 通达信板块数据更新（定义+成分股+日线+分钟线，单线程直连，约3-5分钟）
            # 2026-08-15 重构为两阶段：
            #   phase1（纯TDX，无依赖）：立即执行，与 Tushare 下载并行
            #   phase2（依赖 ts_daily 当日数据）：轮询等 CACHE 下载完成后再跑，
            #         消除「行业对齐/相关性推导」与 ts_daily 下载并行导致的日期错位
            def _tdx_sector_chain():
                try:
                    import tdx_sector_data as tsd
                    ok1 = tsd.run_phase1()          # def/member/daily/minute(回补近10日)
                    # 等 ts_daily 下载完成（最长 90 分钟），再跑对齐+成分股+相关性
                    waited = 0
                    while CACHE.is_running() and waited < 5400:
                        time.sleep(30)
                        waited += 30
                    if CACHE.is_running():
                        logging.warning('ts_daily 下载超 90 分钟未完成，phase2 用现有数据继续')
                    if ok1:
                        tsd.run_phase2()            # align/member/corr
                    logging.info('📊 通达信板块数据两阶段更新完成')
                except Exception as se:
                    logging.error(f'通达信板块数据更新失败: {se}')
            threading.Thread(target=_tdx_sector_chain, daemon=True,
                             name='TdxSectorUpdate').start()
            logging.info('📊 通达信板块数据更新已触发（两阶段）')
            # 触发后休眠到明天0点，避免重复触发
            tomorrow = (check + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0)
            _time.sleep((tomorrow - _dt.datetime.now()).total_seconds() + 10)
        except Exception as e:
            logging.error(f'daily update scheduler error: {e}')
            _time.sleep(3600)


def _sector_realtime_scheduler():
    """交易日盘中每 5 分钟增量拉取 TDX 板块当日分钟线（tdx_sector_min）。

    2026-08-17 新增：原调度只在 17:00 盘后拉一次，盘中 tdx_sector_min 停在 T-1，
    导致 query_members 的「TDX 指数权威成分家数」和 up/down_count 盘中滞后。
    通达信协议盘中分钟线实时可用（实测），本线程保证盘中数据当日可用。
    板块涨幅排名/成分股行情的实时性由 tdx_sector_data.query_rank/query_members
    内置的 get_security_quotes 快照直连保证，不依赖本线程。
    """
    import time as _time
    import datetime as _dt
    while True:
        try:
            now = _dt.datetime.now()
            is_holiday = now.strftime('%Y-%m-%d') in m.HOLIDAYS if m.HOLIDAYS else False
            if now.weekday() >= 5 or is_holiday:
                tomorrow = (now + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0)
                _time.sleep((tomorrow - now).total_seconds() + 10)
                continue
            # 9:30-11:35 / 12:55-15:05 为有效刷新窗口
            t = now.hour * 100 + now.minute
            if not ((930 <= t <= 1135) or (1255 <= t <= 1505)):
                # 等到下一个窗口起点或明天
                if t < 930:
                    nxt = now.replace(hour=9, minute=30, second=0, microsecond=0)
                elif t <= 1255:
                    nxt = now.replace(hour=12, minute=55, second=0, microsecond=0)
                else:
                    nxt = (now + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0)
                _time.sleep(min(max((nxt - now).total_seconds(), 10), 3600))
                continue
            import tdx_sector_data as tsd
            api = tsd.connect()
            if api:
                try:
                    tsd.build_minutes(api, ensure_days=1)
                finally:
                    try:
                        api.disconnect()
                    except Exception:
                        pass
                logging.info('📊 盘中板块分钟线已刷新')
            _time.sleep(300)  # 5 分钟一轮
        except Exception as e:
            logging.error(f'sector realtime scheduler error: {e}')
            _time.sleep(600)
