# -*- coding: utf-8 -*-
"""MonitorSystem：QMT 同步、行情轮询、模型分发、状态与分组 API。

从原 tdx_monitor_system.py 的 MonitorSystem 类整体迁出。
模型判定不再内置 if/else 分支，而是通过 models.REGISTRY 按 type 分发；
_compute_condition/_compute_buy_condition/_eval_cond_trigger 已迁至
models/cond_model.py，通用涨跌幅判定迁至 models/metric_model.py。
"""

import datetime as dt
import json
import logging
import threading
import time
import urllib.request
from collections import deque

import tdx_speed_alert as m
import stock_data_cache as dc

from .common import (QMT_GET_STOCKS, QMT_MARKET_MAP, QMT_REPORT_SIGNAL,
                     QMT_SYNC_INTERVAL, QMT_TIMEOUT, TDX_MARKET_TO_QMT,
                     market_of, metric_label_of, rec_yclose, rises_on)
from .cond_utils import _cond_window_txt, _parse_qmt_time, cond_price, cond_trigger_desc
from .config import (_config_lock, default_models_for_side, load_config,
                     save_config)
from .models import REGISTRY


class MonitorSystem:
    def __init__(self):
        self.config = load_config()
        self.running = True
        self.history = {}                 # (market,code) -> deque([(datetime,price)])
        self.current = {}                 # (market,code) -> {'price','yclose'}
        self.model_states = {}            # model_id -> {'status','stocks','alerts','last_trigger','last_update','error'}
        self._model_alerts = {}           # model_id -> [alert dict]  (持久，跨轮询保留)
        self._cond_last = {}              # (mid,conditionOrderId) -> 上次触发时间戳（条件单冷却，本地防刷屏；服务端触发后不再下发）
        self._speed_last = {}             # (mid,code) -> 上次上报时间戳（涨速/跌速冷却）
        self.name_map = {}
        self.name_map_lock = threading.Lock()
        self.lock = threading.RLock()   # RLock: api_models 持锁调 plugin.summary 时会再拿锁（嵌套），普通 Lock 会自死锁
        self.server_info = {'ip': '', 'port': 0, 'backend': m.BACKEND,
                            'connected': False, 'trading': False,
                            'holidays': 0, 'started': dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'last_update': ''}
        # QMT 同步状态
        self.qmt_status = {'last_sync': '', 'last_sync_ok': None, 'strategy_count': 0,
                           'synced_stock_count': 0, 'condition_count': 0,
                           'error': '', 'available_strategies': []}
        # QMT 下发的条件单缓存（每轮同步整体替换；后端已触发/撤销/过期的不再下发）
        self.qmt_conditions = []

        # 模型插件实例（type -> plugin；同一 type 多个模型共享实例，无状态判定）
        self._plugins = {t: cls() for t, cls in REGISTRY.items()}

    # -------- 名称映射（akshare 异步加载）--------
    def _load_names(self):
        try:
            import akshare as ak
            df = ak.stock_info_a_code_name()
            nm = {}
            for _, r in df.iterrows():
                nm[str(r['code'])] = str(r['name'])
            with self.name_map_lock:
                self.name_map = nm
            logging.info('已加载股票名称映射 %d 条（来源 akshare）', len(nm))
        except Exception as e:
            logging.warning('股票名称映射加载失败: %s', e)

    def name_of(self, code):
        with self.name_map_lock:
            return self.name_map.get(str(code), '')

    # ===================== QMT 接口对接 ====================
    def _http_get_json(self, url):
        """GET 请求返回 JSON。"""
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=QMT_TIMEOUT) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def _http_post_json(self, url, data):
        """POST JSON 请求返回 JSON。"""
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(url, data=body, headers={
            'Content-Type': 'application/json', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=QMT_TIMEOUT) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def fetch_qmt_stocks(self):
        """调用 getMonitorStocks 拉取全部策略盯盘数据（buy/sell/condition 统一接口）。
        返回 (flat_list, strategies_dict) 或在失败时返回 (None, None)。
        strategies_dict: {strategyId: {'buy':[stocks], 'sell':[stocks]}}
        条件单不进 strategies_dict，全量保存在 self.qmt_conditions（每轮同步整体替换，
        后端触发/撤销/过期后不再下发，天然幂等）。
        """
        try:
            resp = self._http_get_json(QMT_GET_STOCKS)
        except Exception as e:
            logging.warning('QMT getMonitorStocks 拉取失败: %r', e)
            self.qmt_status['error'] = f'{type(e).__name__}: {e}'
            return None, None
        if not resp or resp.get('code') != 0:
            logging.warning('QMT getMonitorStocks 返回异常: %s', resp.get('msg', ''))
            self.qmt_status['error'] = f'返回异常: {resp.get("msg", "")}'
            return None, None
        flat = resp.get('data', []) or []
        # 按 (strategyId, monitorType) 聚合；monitorType=condition 是独立条件单
        strategies = {}
        conditions = []
        for item in flat:
            sid = item.get('strategyId')
            mt = item.get('monitorType', 'buy')
            if sid is None:
                continue
            sid = int(sid)
            if mt == 'condition':
                # ===== 条件单（独立监控对象，非股票属性）=====
                conditions.append({
                    'condition_order_id': item.get('conditionOrderId'),
                    'condition_status': item.get('conditionStatus'),  # 0监控中/1已触发待处理(仍监听,重复提醒中)
                    'strategy_id': sid,
                    'direction': int(item['direction']) if item.get('direction') else 1,
                    'code': str(item.get('stockCode', '')),
                    'name': item.get('stockName', ''),
                    'market': item.get('market', ''),
                    'position_id': item.get('positionId'),
                    'breakout_price': cond_price(item.get('breakoutPrice')),
                    'breakdown_price': cond_price(item.get('breakdownPrice')),
                    'breakout_ratio': cond_price(item.get('breakoutRatio')),
                    'breakdown_ratio': cond_price(item.get('breakdownRatio')),
                    # 比例语义=当日涨跌幅（锚点=昨收价），basePrice 已废弃不下发
                    'after_time': _parse_qmt_time(item.get('afterTime')),
                    'before_time': _parse_qmt_time(item.get('beforeTime')),
                    'valid_trade_days': item.get('validTradeDays'),
                    'expire_date': item.get('expireDate'),
                })
                continue
            s = strategies.setdefault(sid, {'buy': [], 'sell': []})
            stock = {
                'code': str(item.get('stockCode', '')),
                'name': item.get('stockName', ''),
                'market': item.get('market', ''),
                'origin': 'qmt',
                'strategy_id': sid,
            }
            # 卖出携带持仓信息（旧的条件价字段已后端移除，条件单独立下发）
            if mt == 'sell':
                stock['position'] = item.get('position')
                stock['avg_price'] = item.get('averagePrice')
                stock['holding_period'] = item.get('holdingPeriod')
            key = 'sell' if mt == 'sell' else 'buy'
            s[key].append(stock)
        with self.lock:
            self.qmt_conditions = conditions
            self.qmt_status['condition_count'] = len(conditions)
        return flat, strategies

    def get_available_strategies(self):
        """获取可用策略列表（供前端绑定选择器）。"""
        flat, strategies = self.fetch_qmt_stocks()
        if strategies is None:
            return []
        out = []
        for sid, data in sorted(strategies.items()):
            out.append({
                'strategy_id': sid,
                'buy_count': len(data['buy']),
                'sell_count': len(data['sell']),
            })
        return out

    def sync_qmt_groups(self):
        """同步策略组的成分股（DIFF 算法）。返回 (added, removed, updated) 或 None。"""
        flat, strategies = self.fetch_qmt_stocks()
        if strategies is None:
            with self.lock:
                self.qmt_status['last_sync'] = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self.qmt_status['last_sync_ok'] = False
                if not self.qmt_status.get('error'):
                    self.qmt_status['error'] = '接口不可达'
            return None

        added = removed = updated = 0
        with _config_lock:
            # ---- 自动补建缺失的策略组 ----
            existing_strategy_ids = set()
            for grp in self.config.get('groups', []):
                if grp.get('type') != 'strategy':
                    continue
                for bg_key in ('buy_group', 'sell_group'):
                    bg = grp.get(bg_key)
                    if bg:
                        for sid in bg.get('bound_strategies', []):
                            existing_strategy_ids.add(sid)

            for sid in sorted(strategies.keys()):
                if sid not in existing_strategy_ids:
                    d = strategies[sid]
                    grp_name = f'策略{sid}'
                    new_grp = {
                        'id': f'qmt_{sid}',
                        'name': grp_name,
                        'type': 'strategy',
                        'buy_group': {
                            'bound_strategies': [sid] if d.get('buy') else [],
                            'models': default_models_for_side(self.config, 'buy'),
                            'stocks': [],
                        },
                        'sell_group': {
                            'bound_strategies': [sid] if d.get('sell') else [],
                            'models': default_models_for_side(self.config, 'sell'),
                            'stocks': [],
                        },
                    }
                    self.config['groups'].append(new_grp)
                    logging.info('自动创建策略组: %s (strategy_id=%d)', grp_name, sid)

            # ---- DIFF 同步成分股 ----
            for grp in self.config['groups']:
                if grp.get('type') != 'strategy':
                    continue
                # 从组 id 推导策略号，确保买卖两侧都绑定（即使该侧当前无股票）
                gid = grp.get('id', '')
                if gid.startswith('qmt_'):
                    try:
                        grp_sid = int(gid[4:])
                        # 合并两侧的策略：只要 QMT 有该策略的数据，就绑定到两侧
                        if grp_sid in strategies:
                            for side_key in ('buy_group', 'sell_group'):
                                bg0 = grp.get(side_key)
                                if isinstance(bg0, dict) and grp_sid not in bg0.get('bound_strategies', []):
                                    bg0.setdefault('bound_strategies', []).append(grp_sid)
                                    updated += 1
                    except (ValueError, IndexError):
                        pass
                for side_key in ('buy_group', 'sell_group'):
                    bg = grp.get(side_key)
                    if not bg:
                        continue
                    side = 'buy' if side_key == 'buy_group' else 'sell'
                    bound = bg.get('bound_strategies', [])
                    # 从 QMT 数据中取该组绑定的所有策略的同方向股票
                    qmt_stocks = {}  # code -> stock dict
                    for sid in bound:
                        for stk in strategies.get(sid, {}).get(side, []):
                            qmt_stocks[stk['code']] = stk
                    # 与组内现有 origin=qmt 的股票 DIFF
                    existing = {s['code']: s for s in bg['stocks'] if s.get('origin') == 'qmt'}
                    local_codes = {s['code'] for s in bg['stocks'] if s.get('origin') != 'qmt'}
                    # 新增
                    for code, stk in qmt_stocks.items():
                        if code not in existing:
                            bg['stocks'].append(stk)
                            added += 1
                        else:
                            # 更新持仓信息
                            old = existing[code]
                            old_changed = False
                            for k in ('position', 'avg_price', 'holding_period', 'name', 'market'):
                                if stk.get(k) is not None and old.get(k) != stk[k]:
                                    old[k] = stk[k]
                                    old_changed = True
                            # 旧条件价字段(stop_loss/take_profit/breakout_buy/breakdown_buy)
                            # 已迁移到独立条件单，这里顺带清理存量残留
                            for k in ('stop_loss', 'take_profit', 'breakout_buy', 'breakdown_buy'):
                                if k in old:
                                    old.pop(k)
                                    old_changed = True
                            if old_changed:
                                updated += 1
                    # 删除
                    to_remove = [c for c in existing if c not in qmt_stocks and c not in local_codes]
                    bg['stocks'] = [s for s in bg['stocks']
                                    if not (s.get('origin') == 'qmt' and s['code'] in to_remove)]
                    removed += len(to_remove)
            save_config(self.config)

        total_synced = sum(len(s.get('buy', []) + s.get('sell', [])) for s in strategies.values())
        avail = [{'strategy_id': sid, 'buy_count': len(d['buy']), 'sell_count': len(d['sell'])}
                 for sid, d in sorted(strategies.items())]
        with self.lock:
            self.qmt_status.update({
                'last_sync': dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'last_sync_ok': True, 'error': '',
                'strategy_count': len(strategies),
                'synced_stock_count': total_synced,
                'available_strategies': avail,
            })
        logging.info('QMT 同步完成: %d策略 %d股票, 新增%d 删除%d 更新%d',
                     len(strategies), total_synced, added, removed, updated)
        return added, removed, updated

    def _qmt_sync_loop(self):
        """每 QMT_SYNC_INTERVAL 秒同步一次策略组股票。"""
        while self.running:
            try:
                self.sync_qmt_groups()
            except Exception as e:
                logging.error('QMT 同步线程异常: %s', e)
            time.sleep(QMT_SYNC_INTERVAL)

    def report_signals_qmt(self, signals):
        """批量上报买卖信号到 QMT reportSignal 接口。
        signals: list of dict (已组装好的信号体)
        """
        if not signals:
            return
        try:
            resp = self._http_post_json(QMT_REPORT_SIGNAL, signals)
            if resp and resp.get('code') == 0:
                logging.info('QMT 信号上报成功: %d条', len(signals))
            else:
                logging.warning('QMT 信号上报失败: %s', resp.get('msg', '') if resp else '无响应')
        except Exception as e:
            logging.warning('QMT 信号上报异常: %s', e)

    # ===================== 分组工具方法 ====================
    def all_stocks_for_side(self, side, mid=None):
        """展开指定方向的所有股票（跨组去重）。
        mid 非空时只收录「绑定了该监控模型」的买卖组。
        返回 list of (code, name, market_num, groups_info)
        groups_info: [{'group_id':, 'group_name':, 'type':, 'strategy_ids':[], 'models':[]}, ...]
        """
        seen = {}  # code -> {stock info + groups}
        with _config_lock:
            default_mids = default_models_for_side(self.config, side)
            for grp in self.config.get('groups', []):
                gk = 'buy_group' if side == 'buy' else 'sell_group'
                bg = grp.get(gk)
                if not bg:
                    continue
                bg_models = bg.get('models')
                if bg_models is None:
                    bg_models = default_mids
                if mid is not None and mid not in bg_models:
                    continue      # 该组未绑定当前模型，跳过
                bound = bg.get('bound_strategies', [])
                for stk in bg.get('stocks', []):
                    code = str(stk['code'])
                    if code not in seen:
                        mk = stk.get('market', '')
                        market_num = QMT_MARKET_MAP.get(mk, market_of(code))
                        seen[code] = {
                            'code': code, 'name': stk.get('name', ''),
                            'market': market_num,
                            'position': stk.get('position'),
                            'avg_price': cond_price(stk.get('avg_price')),
                            'groups': [],
                        }
                    seen[code]['groups'].append({
                        'group_id': grp['id'],
                        'group_name': grp.get('name', ''),
                        'group_type': grp.get('type', 'custom'),
                        'strategy_ids': list(bound) if grp.get('type') == 'strategy' else [],
                        'models': list(bg_models),
                        'origin': stk.get('origin', 'local'),
                    })
        return list(seen.values())

    # -------- 历史缓冲 --------
    def _backfill(self, stock, api):
        try:
            bars = api.get_security_bars(8, stock[0], stock[1], 0, 240)
        except Exception:
            return
        if not bars:
            return
        buf = self.history.setdefault(stock, deque(maxlen=2000))
        for rec in bars:
            p = m.bar_price(rec)
            ts = m.bar_datetime(rec)
            if p and ts:
                buf.append((ts, p))

    def _update_history(self, stock, now, price):
        buf = self.history.setdefault(stock, deque(maxlen=2000))
        buf.append((now, price))

    def _speed_of(self, stock, now, window):
        buf = self.history.get(stock)
        if not buf or len(buf) < 2:
            return None
        latest_ts, latest_price = buf[-1]
        target = now - dt.timedelta(minutes=window)
        prev_ts = prev_price = None
        for ts, price in buf:
            if ts <= target:
                prev_ts, prev_price = ts, price
            else:
                break
        if prev_price is None or prev_price == 0:
            ts0, p0 = buf[0]
            if p0 and p0 != 0:
                prev_price = p0
            else:
                return None
        signed = (latest_price - prev_price) / prev_price * 100.0
        return signed, prev_price

    # -------- 主循环 --------
    def run(self):
        m.auto_load_holidays(dt.date.today().year)
        threading.Thread(target=self._load_names, daemon=True).start()
        threading.Thread(target=self._qmt_sync_loop, daemon=True).start()
        api = None
        while self.running:
            try:
                if api is None:
                    api = m.connect_api()
                    try:
                        sip, sport = m.get_best_server()
                        with self.lock:
                            self.server_info['ip'] = sip
                            self.server_info['port'] = sport
                    except Exception:
                        pass
                    # 启动时回填所有股票历史（从所有买卖组展开去重 + 条件单股票）
                    all_codes = set()
                    for side in ('buy', 'sell'):
                        for s in self.all_stocks_for_side(side):
                            all_codes.add(s['code'])
                    with self.lock:
                        for co in self.qmt_conditions:
                            if co.get('code'):
                                all_codes.add(str(co['code']))
                    for code in all_codes:
                        self._backfill((market_of(code), code), api)
                now = dt.datetime.now()
                trading = m.is_trading_time(now)
                self._poll(api, now, trading)
                time.sleep(m.POLL_INTERVAL)
            except Exception as e:
                logging.error('监控循环异常: %s', e)
                try:
                    api and api.disconnect()
                except Exception:
                    pass
                api = None
                time.sleep(5)

    def _poll(self, api, now, trading):
        # 从所有买卖组展开股票（跨组去重）+ 条件单股票（可能不在任何分组内，如买入单直接输代码）
        all_codes = {}
        for side in ('buy', 'sell'):
            for s in self.all_stocks_for_side(side):
                code = s['code']
                if code not in all_codes:
                    all_codes[code] = s['market']
        with self.lock:
            for co in self.qmt_conditions:
                code = str(co.get('code', ''))
                if code and code not in all_codes:
                    mk = co.get('market', '')
                    all_codes[code] = QMT_MARKET_MAP.get(mk, market_of(code))
        if all_codes:
            union = [(mk, code) for code, mk in all_codes.items()]
            try:
                quotes = api.get_security_quotes(union)
            except Exception:
                raise
            current = {}
            for q in quotes:
                if isinstance(q, dict):
                    mc = q.get('code') or q.get('symbol')
                    mk = q.get('market')
                else:
                    mc = getattr(q, 'code', None)
                    mk = getattr(q, 'market', None)
                if mc is None:
                    continue
                code = str(mc)
                price = m.rec_price(q)
                if price is None or price == 0:
                    continue
                yclose = rec_yclose(q)
                stock = (int(mk) if mk is not None else market_of(code), code)
                current[stock] = {'price': price, 'yclose': yclose}
                self._update_history(stock, now, price)
        else:
            current = {}

        for model in self.config['models']:
            self._compute_model(model, now, trading, current)

        with self.lock:
            self.server_info.update({
                'connected': True,
                'trading': trading,
                'holidays': len(m.HOLIDAYS),
                'last_update': now.strftime('%Y-%m-%d %H:%M:%S'),
            })

    # -------- 本地推送（自定义组走这条路） --------
    @staticmethod
    def _local_push(title, msg):
        logging.warning(msg)
        for fn in (m.notify_local, m.push_serverchan, m.push_bark,
                   m.push_wecom, m.push_dingtalk, m.push_telegram):
            try:
                fn(title, msg)
            except Exception as e:
                logging.warning('本地推送失败 %s: %s', getattr(fn, '__name__', fn), e)

    # -------- 模型分发（注册表插件化）--------
    def _plugin_of(self, model):
        return self._plugins.get(model.get('type'))

    def _compute_model(self, model, now, trading, current):
        plugin = self._plugin_of(model)
        if plugin is None:
            logging.warning('未知模型类型 %s（id=%s），跳过', model.get('type'), model.get('id'))
            return
        plugin.compute(self, model, now, trading, current)

    # -------- 条件单公共判定 --------
    def _strategy_side_has_model(self, sid, side, mid):
        """策略分组指定方向的买卖组是否绑定了模型 mid。"""
        with _config_lock:
            grp = next((g for g in self.config.get('groups', [])
                        if g.get('type') == 'strategy' and g.get('id') == f'qmt_{sid}'), None)
            if not grp:
                return False
            bg = grp.get('buy_group' if side == 'buy' else 'sell_group')
            if not isinstance(bg, dict):
                return False
            models = bg.get('models')
            if models is None:
                models = default_models_for_side(self.config, side)
            return mid in models

    # 兼容旧引用（test_cond_order.py 以实例方法约定访问 _eval_cond_trigger）：
    # 保持原方法签名 (self, co, price, yclose)，内部转调插件模块的纯函数。
    def _eval_cond_trigger(self, co, price, yclose):
        from .models.cond_model import _eval_cond_trigger
        return _eval_cond_trigger(co, price, yclose)

    # ===================== API 数据 =====================
    def model_summary(self, model):
        mid = model['id']
        st = self.model_states.get(mid, {})
        plugin = self._plugin_of(model)
        if plugin is not None:
            stocks_in = plugin.summary(self, model)
        else:
            stocks_in = self.all_stocks_for_side(model['side'], mid)
        # top mover 从 state rows 算
        top = None
        for r in st.get('stocks', []):
            if r.get('signed') is None:
                continue
            if rises_on(model.get('type')):
                mag = abs(r['signed'])
                disp = r['signed']
            else:
                mag = max(0.0, -r['signed'])
                disp = mag
            if top is None or mag > top[1]:
                top = (r['code'], disp)
        cfgm = model.get('config') or {}
        if plugin is not None:
            param_text = plugin.param_text(self, model)
        else:
            param_text = f"{cfgm.get('threshold')}% / {cfgm.get('window')}分"
        return {
            'id': mid, 'type': model['type'], 'name': model['name'],
            'side': model['side'], 'threshold': cfgm.get('threshold'),
            'window': cfgm.get('window'), 'cooldown': cfgm.get('cooldown'),
            'param_text': param_text,
            'metric_label': metric_label_of(model['type']),
            'stock_count': len(stocks_in), 'status': st.get('status', 'idle'),
            'last_trigger': st.get('last_trigger'),
            'top_mover': ({'code': top[0], 'val': top[1]} if top else None),
        }

    def api_models(self):
        with self.lock:
            return [self.model_summary(mo) for mo in self.config['models']]

    def api_model_detail(self, mid):
        model = next((x for x in self.config['models'] if x['id'] == mid), None)
        if not model:
            return None
        with self.lock:
            st = self.model_states.get(mid, {})
            return {
                'model': self.model_summary(model),
                'stocks': st.get('stocks', []),
                'alerts': st.get('alerts', []),
                'server': dict(self.server_info),
            }

    def api_stocks(self):
        """展开所有分组的股票（兼容旧 API）。models = 所属组绑定的非条件单模型并集。

        条件单模型（type=condition/buy_condition）的股票来源是服务端下发的 qmt_conditions，
        不是分组成分股；如果把它们挂到普通股票上，模型列表里会错误显示条件单模型也有股票。
        """
        cond_ids = {m['id'] for m in self.config.get('models', [])
                    if m.get('type') in ('condition', 'buy_condition')}
        out = []
        for side in ('buy', 'sell'):
            for s in self.all_stocks_for_side(side):
                mids = []
                for g in s['groups']:
                    for x in g.get('models', []):
                        if x in cond_ids:
                            continue
                        if x not in mids:
                            mids.append(x)
                out.append({'code': s['code'], 'name': s['name'] or self.name_of(s['code']) or s['code'],
                            'side': side, 'models': mids,
                            'groups': [g['group_name'] for g in s['groups']]})
        return out

    def api_groups(self):
        """返回所有分组（含成分股和同步状态）。"""
        with _config_lock:
            groups = []
            for grp in self.config.get('groups', []):
                g = json.loads(json.dumps(grp))  # 深拷贝
                # 补全股票名称
                for bg_key in ('buy_group', 'sell_group'):
                    bg = g.get(bg_key)
                    if bg:
                        for stk in bg.get('stocks', []):
                            if not stk.get('name'):
                                stk['name'] = self.name_of(stk['code']) or stk['code']
                groups.append(g)
            return groups

    def api_qmt_status(self):
        with self.lock:
            return dict(self.qmt_status)

    def api_qmt_strategies(self):
        return self.get_available_strategies()

    def api_status(self):
        with self.lock:
            return dict(self.server_info)

    # -------- 分组配置变更 --------
    def _find_model(self, mid):
        return next((mo for mo in self.config['models'] if mo['id'] == mid), None)

    def _find_group(self, gid):
        return next((g for g in self.config.get('groups', []) if g['id'] == gid), None)

    def add_group(self, data):
        """创建新分组。data: {name, type, buy_group?, sell_group?}"""
        name = (data.get('name') or '').strip()
        if not name:
            raise ValueError('分组名称不能为空')
        gtype = data.get('type', 'custom')
        if gtype not in ('strategy', 'custom'):
            raise ValueError('type 必须是 strategy 或 custom')
        gid = data.get('id') or f"g_{int(time.time()) % 100000}"
        if self._find_group(gid):
            raise ValueError('分组ID已存在: ' + gid)
        grp = {'id': gid, 'name': name, 'type': gtype, 'buy_group': None, 'sell_group': None}
        # 可选初始化买卖组（默认绑定该方向全部模型）
        if data.get('buy_group'):
            grp['buy_group'] = {'bound_strategies': data['buy_group'].get('bound_strategies', []),
                                'models': data['buy_group'].get('models',
                                                                default_models_for_side(self.config, 'buy')),
                                'stocks': []}
        if data.get('sell_group'):
            grp['sell_group'] = {'bound_strategies': data['sell_group'].get('bound_strategies', []),
                                 'models': data['sell_group'].get('models',
                                                                  default_models_for_side(self.config, 'sell')),
                                 'stocks': []}
        with _config_lock:
            self.config.setdefault('groups', []).append(grp)
            save_config(self.config)
        return grp

    def del_group(self, gid):
        with _config_lock:
            self.config['groups'] = [g for g in self.config.get('groups', []) if g['id'] != gid]
            save_config(self.config)

    def update_group(self, gid, data):
        """更新分组。data: {name?, buy_models?, sell_models?, buy_bound?, sell_bound?}"""
        grp = self._find_group(gid)
        if not grp:
            raise ValueError('分组不存在: ' + gid)

        def _blank(side):
            return {'bound_strategies': [], 'models': default_models_for_side(self.config, side),
                    'stocks': []}

        def _valid_models(side, raw):
            allow = {m['id'] for m in self.config.get('models', []) if m.get('side') == side}
            out = []
            for x in (raw or []):
                if x not in allow:
                    raise ValueError(f'模型 {x} 不属于{side}方向')
                if x not in out:
                    out.append(x)
            return out

        with _config_lock:
            if 'name' in data:
                grp['name'] = data['name']
            for side, key, field in (('buy', 'buy_group', 'buy_models'),
                                     ('sell', 'sell_group', 'sell_models')):
                if field in data:
                    want = _valid_models(side, data[field])
                    if grp.get(key) is None:
                        if not want and not data.get('create_' + side):
                            continue          # 组不存在且无需绑定 → 不凭空创建
                        grp[key] = _blank(side)
                    grp[key]['models'] = want
            if 'buy_bound' in data:
                if grp.get('buy_group') is None:
                    grp['buy_group'] = _blank('buy')
                grp['buy_group']['bound_strategies'] = [int(x) for x in data['buy_bound']]
            if 'sell_bound' in data:
                if grp.get('sell_group') is None:
                    grp['sell_group'] = _blank('sell')
                grp['sell_group']['bound_strategies'] = [int(x) for x in data['sell_bound']]
            if data.get('create_buy') and grp.get('buy_group') is None:
                grp['buy_group'] = _blank('buy')
            if data.get('create_sell') and grp.get('sell_group') is None:
                grp['sell_group'] = _blank('sell')
            save_config(self.config)
        return grp

    def add_stock_to_group(self, gid, side, data):
        """向自定义组的指定方向添加股票。"""
        grp = self._find_group(gid)
        if not grp:
            raise ValueError('分组不存在: ' + gid)
        code = str(data.get('code', '')).strip()
        if not code:
            raise ValueError('code 不能为空')
        bg_key = 'buy_group' if side == 'buy' else 'sell_group'
        bg = grp.get(bg_key)
        if bg is None:
            raise ValueError(f'该组没有{side}买卖组，请先创建')
        # 去重
        if any(s['code'] == code for s in bg['stocks']):
            raise ValueError('股票已在该组: ' + code)
        name = data.get('name') or self.name_of(code) or ''
        mkt = data.get('market') or TDX_MARKET_TO_QMT.get(market_of(code), '')
        with _config_lock:
            bg['stocks'].append({'code': code, 'name': name, 'market': mkt,
                                 'origin': 'local', 'strategy_id': None})
            save_config(self.config)
        return bg['stocks'][-1]

    def del_stock_from_group(self, gid, side, code):
        """从组中移除股票（仅允许移除 origin=local 的手动股票）。"""
        grp = self._find_group(gid)
        if not grp:
            raise ValueError('分组不存在: ' + gid)
        bg_key = 'buy_group' if side == 'buy' else 'sell_group'
        bg = grp.get(bg_key)
        if bg is None:
            raise ValueError('该方向无买卖组')
        with _config_lock:
            bg['stocks'] = [s for s in bg['stocks']
                            if not (s['code'] == str(code) and s.get('origin') != 'qmt')]
            save_config(self.config)

    def update_model_params(self, mid, data):
        """更新模型参数（threshold/window/cooldown），供简化前端「点条件标签改阈值」用。

        注意：threshold 是模型级全局参数，对该模型监控的所有股票生效。
        """
        model = self._find_model(mid)
        if not model:
            raise ValueError('模型不存在: ' + mid)
        mtype = model.get('type')
        cfgm = model.setdefault('config', {})
        for key, cast in (('threshold', float), ('window', int), ('cooldown', int)):
            if key not in data or data[key] is None:
                continue
            if mtype in ('condition', 'buy_condition') and key in ('threshold', 'window'):
                raise ValueError('条件单模型不支持修改 ' + key)
            v = cast(data[key])
            if v <= 0:
                raise ValueError(f'{key} 必须为正数')
            cfgm[key] = v
        with _config_lock:
            save_config(self.config)
        return self.model_summary(model)

    def sync_qmt_now(self):
        """手动触发 QMT 同步。"""
        return self.sync_qmt_groups()
