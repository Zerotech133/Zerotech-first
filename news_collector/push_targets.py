# -*- coding: utf-8 -*-
"""推送收件人单一事实源（K-STOCK-403 / A4）。

news_config.json 的 push_targets 是收件人（个人/群）唯一权威注册表，
全部推送方按名取用，禁止在业务代码里写死 ou_/oc_ 字面量。

边界判例（K-STOCK-401 cross app 事故）：
  - open_id 是 **app 维度** 的——同一用户在不同 app 下的 open_id 不同。
    push_targets 登记的是 apps[0]（news 主 app）视角的 id；jev app 视角
    的收件人（如 LARK_ALERT_OPEN_ID）由各链自带 env 管理，本模块不越
    权代理，勿把这里的 id 用于其他 app 的凭证发消息。
  - bot 自身 open_id（@提及用，如 p_g_robot.BOT_OPEN_ID）不是收件人，
    不在本表范畴。
  - user 应用域身份（send_feishu.USER_ID_LARK）是另一身份维度，同样不在
    本表范畴。

miss 策略：查无此名返回 None 并响亮告警（一次）——不做字面量静默回退
（错误上下文的 id 静默发出 = 99992361 级事故重演，响亮失败可被
push_liveness/晨检捕获）。
"""
import json
import os
import sys

_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "news_config.json")
_TARGETS = None
_MISSED: set = set()


def targets() -> list:
    """push_targets 全表（进程内缓存；news_config 非热更配）。"""
    global _TARGETS
    if _TARGETS is None:
        try:
            with open(_CONFIG, encoding="utf-8") as f:
                _TARGETS = json.load(f).get("push_targets") or []
        except Exception as e:  # noqa: BLE001
            print("[push_targets] news_config.json 读取失败: %r" % e,
                  file=sys.stderr)
            # 2026-10-16 审计：不缓存失败结果——原 _TARGETS=[] 落缓存后，
            # 长驻进程（gate/qd_bridge）瞬态读取失败变永久 miss。
            return []
    return _TARGETS


def target_id(name: str) -> str | None:
    """按名取收件人 id（open_id/chat_id）；查无此名告警一次并返回 None。"""
    for t in targets():
        if t.get("name") == name:
            return t.get("id")
    if name not in _MISSED:
        _MISSED.add(name)
        print("[push_targets] 收件人 %r 不在 push_targets（检查 "
              "news_config.json）" % name, file=sys.stderr)
    return None


def reload() -> None:
    """清缓存（测试用；生产 news_config 非热更配）。"""
    global _TARGETS
    _TARGETS = None
    _MISSED.clear()
