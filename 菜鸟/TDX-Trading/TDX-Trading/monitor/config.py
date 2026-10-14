# -*- coding: utf-8 -*-
"""配置加载/持久化/迁移。

从原 tdx_monitor_system.py 的「配置加载/持久化」区整体迁出，行为不变：
- DEFAULT_CONFIG 定义 4 个内置模型（对应 monitor/models/ 下 4 个插件）
- 历史配置自动迁移（模型方向对调 / 扁平 stocks → groups / 内置模型补全）
"""

import json
import logging
import os
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, 'monitor_config.json')

_config_lock = threading.Lock()

DEFAULT_CONFIG = {
    "models": [
        {"id": "drop_buy", "type": "drop", "name": "跌幅预警", "side": "buy",
         "config": {"threshold": 2.0, "window": 3, "cooldown": 60}},
        {"id": "speed_buy", "type": "speed", "name": "涨幅预警（买）", "side": "buy",
         "config": {"threshold": 2.88, "window": 3, "cooldown": 60}},
        {"id": "speed_sell", "type": "speed", "name": "涨幅预警", "side": "sell",
         "config": {"threshold": 2.88, "window": 3, "cooldown": 60}},
        {"id": "drop_sell", "type": "drop", "name": "跌幅预警（卖）", "side": "sell",
         "config": {"threshold": 2.0, "window": 3, "cooldown": 60}},
        {"id": "cond_sell", "type": "condition", "name": "卖出条件单", "side": "sell",
         "config": {"cooldown": 300}},
        {"id": "cond_buy", "type": "buy_condition", "name": "买入条件单", "side": "buy",
         "config": {"cooldown": 300}},
    ],
    "groups": [],
    # 种子即新结构：全新安装也带迁移完成标记，防止新 speed_buy 被旧迁移映射误改名
    "models_side_migrated": True,
}

# 手动启用模型：种子进配置但不自动绑定任何买卖组（含新建组默认绑定），
# 由用户在「股票管理→编辑分组」里按组勾选启用，避免升级后信号量突变
MANUAL_BIND_MODELS = {'speed_buy', 'drop_sell'}   # 手动绑定：不自动进新组，由用户在界面选择模板时显式启用

# 旧模型 id -> 新模型 id（2026-08-10 涨跌方向与买卖语义对调）
# 旧: speed_buy(涨→买) / drop_sell(跌→卖)
# 新: speed_sell(涨→卖出止盈) / drop_buy(跌→买入抄底)
MODEL_ID_MIGRATION = {'speed_buy': 'speed_sell', 'drop_sell': 'drop_buy'}
# 分组内模型绑定的迁移：保持「该方向原本有监控」这一事实，只把检测方向换过来
GROUP_MODEL_MIGRATION = {
    'buy':  {'speed_buy': 'drop_buy',   'drop_sell': 'drop_buy'},
    'sell': {'drop_sell': 'speed_sell', 'speed_buy': 'speed_sell'},
}


def default_models_for_side(cfg, side):
    """该方向可用的全部模型 id（新建买卖组时的默认绑定）。

    手动启用模型（MANUAL_BIND_MODELS）不进默认绑定——新组保持与旧行为一致，
    需要涨幅买入监控的用户自行在编辑弹窗勾选。
    """
    return [m['id'] for m in cfg.get('models', [])
            if m.get('side') == side and m['id'] not in MANUAL_BIND_MODELS]


def _ensure_group_models(cfg):
    """为历史配置的买卖组补全 models 字段（缺失=默认全绑，空列表=用户明确不绑）。"""
    valid = {m['id'] for m in cfg.get('models', [])}
    for grp in cfg.get('groups', []):
        for key, side in (('buy_group', 'buy'), ('sell_group', 'sell')):
            bg = grp.get(key)
            if not isinstance(bg, dict):
                continue
            if 'models' not in bg or bg['models'] is None:
                bg['models'] = default_models_for_side(cfg, side)
            else:
                # 过滤已删除/方向不符的模型 id
                bg['models'] = [x for x in bg['models'] if x in valid]
    return cfg


def _ensure_builtin_models(cfg):
    """把内置模型合并进配置（已存在的保留用户参数）。返回本次新加入的模型 id 列表。"""
    have = {mo.get('id') for mo in cfg.get('models', [])}
    added = []
    for mo in DEFAULT_CONFIG['models']:
        if mo['id'] not in have:
            cfg.setdefault('models', []).append(json.loads(json.dumps(mo)))
            added.append(mo['id'])
    # 旧配置迁移完成标记缺失时补上（迁移在补种之前执行，走到这里说明已迁完或全新安装）
    cfg.setdefault('models_side_migrated', True)
    return added


def _autobind_new_models(cfg, new_ids):
    """新引入的内置模型自动绑定到「已启用同方向监控」的买卖组，避免用户升级后一片空白。
    models 为空列表（用户明确关掉全部监控）的组不动。
    手动启用模型（MANUAL_BIND_MODELS）跳过自动绑定。"""
    by_id = {mo['id']: mo for mo in cfg.get('models', [])}
    for mid in new_ids:
        if mid in MANUAL_BIND_MODELS:
            continue
        mo = by_id.get(mid)
        if not mo:
            continue
        key = 'buy_group' if mo.get('side') == 'buy' else 'sell_group'
        for grp in cfg.get('groups', []):
            bg = grp.get(key)
            if isinstance(bg, dict) and bg.get('models') and mid not in bg['models']:
                bg['models'].append(mid)


def _migrate_model_sides(cfg):
    """涨跌方向与买卖语义对调的一次性迁移（2026-08-10）。

    旧设计把「判涨还是判跌」耦合在 side 上：buy→判涨(追涨买入)、sell→判跌(跌破止损)。
    新设计由 type 决定涨跌、side 只表示交易语义：
        涨幅达标 → 卖出止盈(side=sell)
        跌幅达标 → 买入抄底(side=buy)
    迁移内容：重命名模型 id、对调 side、改写各买卖组的 models 引用。

    ⚠️ 2026-08-30 新增内置模型 speed_buy（type=speed, side=buy，涨幅追买），
    与迁移映射中的旧 speed_buy 同名但语义不同。若不加完成标记，每次启动
    _ensure_builtin_models 补种的新 speed_buy 都会被本函数改名成 speed_sell
    导致模型消失。故用 models_side_migrated 标记：迁移只执行一次，
    标记存在时（新装/已迁移/已含新内置模型）直接跳过。
    """
    if cfg.get('models_side_migrated'):
        return False
    models = cfg.get('models', [])
    if not any(mo.get('id') in MODEL_ID_MIGRATION for mo in models):
        return False                       # 全新安装（或已迁移并清理过旧 id）

    for mo in models:
        old_id = mo.get('id')
        if old_id not in MODEL_ID_MIGRATION:
            continue
        mo['id'] = MODEL_ID_MIGRATION[old_id]
        if mo.get('type') == 'drop':
            mo['side'] = 'buy'
            if mo.get('name') in ('跌幅预警', ''):
                mo['name'] = '跌幅预警'
        else:
            mo['side'] = 'sell'
            if mo.get('name') in ('涨速预警', ''):
                mo['name'] = '涨幅预警'

    # 去重（万一新旧 id 同时存在，保留先出现的）
    seen, uniq = set(), []
    for mo in models:
        if mo.get('id') in seen:
            continue
        seen.add(mo.get('id'))
        uniq.append(mo)
    cfg['models'] = uniq

    # 改写分组内的模型绑定
    for grp in cfg.get('groups', []):
        for key, side in (('buy_group', 'buy'), ('sell_group', 'sell')):
            bg = grp.get(key)
            if not isinstance(bg, dict) or not isinstance(bg.get('models'), list):
                continue
            mapping = GROUP_MODEL_MIGRATION[side]
            out = []
            for x in bg['models']:
                nx = mapping.get(x, x)
                if nx not in out:
                    out.append(nx)
            bg['models'] = out
    cfg['models_side_migrated'] = True
    logging.warning('已迁移模型方向：涨幅预警→卖出(止盈)、跌幅预警→买入(抄底)')
    return True


def _migrate_config(cfg):
    """将旧版扁平 stocks 结构迁移为 groups 三层结构。"""
    if 'groups' in cfg and cfg['groups']:
        return cfg
    old_stocks = cfg.get('stocks', [])
    if not old_stocks:
        cfg['groups'] = []
        return cfg
    from .common import TDX_MARKET_TO_QMT, market_of
    # 旧股票按 side 分为买卖两组
    buy_stocks = [{'code': s['code'], 'name': s.get('name', ''), 'market': TDX_MARKET_TO_QMT.get(market_of(s['code']), ''),
                   'origin': 'local', 'strategy_id': None} for s in old_stocks if s.get('side') == 'buy']
    sell_stocks = [{'code': s['code'], 'name': s.get('name', ''), 'market': TDX_MARKET_TO_QMT.get(market_of(s['code']), ''),
                    'origin': 'local', 'strategy_id': None} for s in old_stocks if s.get('side') == 'sell']
    grp = {"id": "g_migrated", "name": "导入的股票", "type": "custom", "buy_group": None, "sell_group": None}
    if buy_stocks:
        grp["buy_group"] = {"bound_strategies": [], "stocks": buy_stocks}
    if sell_stocks:
        grp["sell_group"] = {"bound_strategies": [], "stocks": sell_stocks}
    cfg['groups'] = [grp]
    cfg.pop('stocks', None)
    return cfg


def load_config():
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        cfg.setdefault('models', [])
        cfg.setdefault('groups', [])
        sides_migrated = _migrate_model_sides(cfg)    # 先对调涨跌/买卖语义，再补内置模型
        new_model_ids = _ensure_builtin_models(cfg)   # 合并新增的内置模型
        cfg = _migrate_config(cfg)   # 自动迁移旧 stocks 结构
        if 'stocks' in cfg:
            cfg.pop('stocks')        # 清理已迁移的旧字段
        _ensure_group_models(cfg)    # 补全买卖组的模型绑定
        if new_model_ids:
            _autobind_new_models(cfg, new_model_ids)
            autobind_skipped = [i for i in new_model_ids if i in MANUAL_BIND_MODELS]
            if autobind_skipped:
                logging.info('手动启用模型已加入: %s（不自动绑定，请在「股票管理→编辑分组」勾选启用）',
                             ', '.join(autobind_skipped))
            other_new = [i for i in new_model_ids if i not in MANUAL_BIND_MODELS]
            if other_new:
                logging.info('新增内置监控模型: %s（已自动绑定到启用中的买卖组）', ', '.join(other_new))
        if new_model_ids or sides_migrated:
            save_config(cfg)
        return cfg
    except Exception as e:
        logging.error('读取配置失败，使用默认: %s', e)
        return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg):
    tmp = CONFIG_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_FILE)
