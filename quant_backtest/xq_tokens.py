# -*- coding: utf-8 -*-
"""雪球多 token 池（P1-4，RESEARCH_MEDIACRAWLER_XUEQIU_20261017.md §E）。

设计裁剪（对照 MediaCrawler 账号池构件——开源版无账号池，用其三原语
拼装：pong 健康检查=token_probe、冷却过滤、状态存储=JSON 持久化）：
- 池文件 config/xq_tokens.json：[{"name": "tok-1", "token": "…"}, …]；
  缺失/坏文件 → 空池，调用方降级走现有三级链（L1 .env.local → L2
  浏览器提取 → L3 空），与未引入本模块行为一致，零风险；
- pick()：冷却过滤 + 最早使用优先（last_used 升序，从未用过者优先）
  ——分摊单账号请求压力（MediaCrawler Pro 账号池的平价替代）；
- mark_fail()：400016 类明确失效 → 冷却 cooldown 秒（默认 30min），
  fail_count 累计；mark_ok()：probe 通过 → fail_count 清零；
- 状态持久化（与 xq_proxy 的进程内冷却不同）：计划任务日级频次，
  冷却/last_used 不落盘则轮换无意义；save() 原子写（tmp+replace）；
- 本模块不 import 项目内其他模块（供 sync 单向引用，无环）。
配置文件含 token 明文（凭证），仅本机使用；日志绝不打印 token 值，
只打 name 与 4 位掩码。
"""

import json
import os
import time
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config", "xq_tokens.json")

# token 被判定失效后的冷却（秒）：到期后 pick() 重新纳入候选
# （雪球 token 多为「过期」而非「封禁」，冷却≠永久拉黑；换回有效
# 前提是用户重登续期，到期重试成本低）
TOKEN_COOLDOWN_SEC = 30 * 60


def mask(tok: str) -> str:
    """token 掩码（日志用）：前 4 位 + 长度，绝不完整打印。"""
    return "%s…(len=%d)" % (tok[:4], len(tok)) if tok else "(empty)"


@dataclass
class TokenEntry:
    """池内单条 token。name 为人工可读标识（日志/运维）。"""

    name: str
    token: str
    last_used: float = 0.0  # time.time() 纪元；0=从未使用
    fail_count: int = 0
    cooldown_until: float = 0.0  # time.time() 纪元；0=不在冷却

    def healthy(self) -> bool:
        return time.time() >= self.cooldown_until


class TokenPool:
    """token 池：冷却过滤 + 最早使用优先轮换 + JSON 持久化。"""

    def __init__(
        self, entries: list[TokenEntry] | None = None, path: str | None = None
    ) -> None:
        self.entries: list[TokenEntry] = list(entries or [])
        self.path = path or CONFIG_PATH

    def enabled(self) -> bool:
        return bool(self.entries)

    def pick(self) -> TokenEntry | None:
        """选下一条 token：冷却过滤 + last_used 升序（0=从未用最优先）。

        全冷却/空池 → None（调用方降级三级链）。绝不 raise。
        """
        cands = [e for e in self.entries if e.healthy()]
        if not cands:
            if self.entries:
                print(
                    "[tokens] 全部 %d 条 token 冷却中 → 降级三级链" % len(self.entries)
                )
            return None
        return min(cands, key=lambda e: e.last_used)

    def mark_used(self, e: TokenEntry) -> None:
        e.last_used = time.time()

    def mark_fail(self, e: TokenEntry, cooldown: int = TOKEN_COOLDOWN_SEC) -> None:
        """400016 类明确失效：fail_count+1 并冷却（其余批次跳过它）。"""
        e.fail_count += 1
        e.cooldown_until = time.time() + max(1, cooldown)
        print(
            "[tokens] %s 失效进冷却 %ds（fail=%d）" % (e.name, cooldown, e.fail_count)
        )

    def mark_ok(self, e: TokenEntry) -> None:
        """探测通过：fail_count 清零、解除冷却。"""
        e.fail_count = 0
        e.cooldown_until = 0.0

    def save(self) -> None:
        """状态持久化（原子写 tmp+replace）。失败仅打印，绝不 raise
        ——池状态丢失只影响轮换精度，不值得让采集失败。"""
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            data = [
                {
                    "name": e.name,
                    "token": e.token,
                    "last_used": e.last_used,
                    "fail_count": e.fail_count,
                    "cooldown_until": e.cooldown_until,
                }
                for e in self.entries
            ]
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception as e:
            print("[tokens] save 失败（忽略）：%s" % str(e)[:80])


def load_tokens(path: str | None = None) -> TokenPool:
    """从 JSON 加载 token 池；恢复持久化状态（last_used/fail_count/
    cooldown_until）。文件缺失/JSON 坏/条目缺字段 → 空池，绝不 raise。
    """
    p = path or CONFIG_PATH
    entries: list[TokenEntry] = []
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, list):
                for it in data:
                    if isinstance(it, dict) and it.get("name") and it.get("token"):
                        entries.append(
                            TokenEntry(
                                name=str(it["name"]),
                                token=str(it["token"]),
                                last_used=float(it.get("last_used") or 0.0),
                                fail_count=int(it.get("fail_count") or 0),
                                cooldown_until=float(it.get("cooldown_until") or 0.0),
                            )
                        )
        except Exception:
            entries = []
    if entries:
        healthy_n = sum(1 for e in entries if e.healthy())
        print(
            "[tokens] token 池已启用：%d 条（健康 %d，%s）"
            % (len(entries), healthy_n, ",".join(e.name for e in entries))
        )
    return TokenPool(entries, path)


_POOL: TokenPool | None = None


def get_pool(path: str | None = None) -> TokenPool:
    """进程内单例（首调加载，之后缓存——配置热更需重启进程）。"""
    global _POOL
    if _POOL is None:
        _POOL = load_tokens(path)
    return _POOL
