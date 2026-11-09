"""测试共用的数据库替身。

原先 ``test_story_group.py`` 与 ``test_story_llm_judge.py`` 各维护一份 ``FakeDB``，
两份实现靠人工同步。真实事故就发生在同步缝隙里：算子把"建事件"的 INSERT 从
``fetch_all`` 改成写入通道时，桩没跟着改，于是单测全绿、第一次真跑就外键冲突。
收成一份后，改桩只需改一处，且下面这条结构性护栏对全部用例同时生效。
"""

from __future__ import annotations


class FakeDB:
    """最小 fake：只区分算子实际发出的几类语句。"""

    def __init__(self, units: list[dict], anchors: list[dict] | None = None) -> None:
        self.units = units
        #: 窗口外已归属单元（跨窗口召回的锚点）。默认空表——不传时跨窗口
        #: 召回拿不到锚点，与"库里窗外 42 天确实没有已归属历史"同形。
        self.anchors = anchors or []
        self.story_inserts: list[dict] = []
        self.member_inserts: list[dict] = []
        self.refresh_calls = 0
        #: 锚点查询次数。用于断言"开关关着就不查"，光看 anchors_loaded=0
        #: 区分不了"没查"和"查了但库里没有"。
        self.anchor_queries = 0
        self._next_id = 1

    def fetch_all(self, sql: str, params: dict | None = None):
        # 护栏：真实 fetch_all 用 engine.connect()，退出即回滚。写入走这条路会
        # 静默丢行，而 RETURNING 仍返回一个指向空行的自增 ID，故障推迟到后续
        # 外键校验才爆。让桩直接失败，回归时一眼可见。
        if "INSERT INTO osint_story" in sql:
            raise AssertionError(
                "写入语句必须走 insert_returning；fetch_all 会回滚事务，"
                "RETURNING 拿到的自增 ID 指向不存在的行"
            )
        if "FROM osint_knowledge_unit u" in sql and "NOT EXISTS" in sql:
            return list(self.units)
        # 锚点查询靠参数名识别（far_days 只出现在 _SELECT_ANCHORS），比匹配
        # SQL 片段可靠——两段 SQL 的表名与别名完全相同。
        if params and "far_days" in params:
            self.anchor_queries += 1
            return list(self.anchors)
        return []

    def insert_returning(self, sql: str, params: dict | None = None):
        if "INSERT INTO osint_story" in sql:
            payload = dict(params or {})
            # 模拟 ON CONFLICT (story_key) DO UPDATE ... RETURNING story_id：
            # 同一 story_key 再次插入必须复用既有 id，而不是分配新 id 或抛错。
            # 真库上这条路径由唯一约束 osint_story_story_key_key 兜着，桩不模拟
            # 就等于放过"删成员边重算会崩"这类故障。
            for existing in self.story_inserts:
                if existing["params"].get("story_key") == payload.get("story_key"):
                    existing["params"] = payload
                    return [{"story_id": existing["story_id"], "inserted": False}]
            story_id = self._next_id
            self._next_id += 1
            self.story_inserts.append({"story_id": story_id, "params": payload})
            return [{"story_id": story_id, "inserted": True}]
        return []

    def execute(self, sql: str, params: dict | None = None) -> int:
        if "INSERT INTO osint_story_member" in sql:
            payload = dict(params or {})
            # 模拟 ON CONFLICT DO NOTHING：同一单元只写一次
            for existing in self.member_inserts:
                if (
                    existing["unit_key"] == payload["unit_key"]
                    and existing["unit_version"] == payload["unit_version"]
                ):
                    return 0
            self.member_inserts.append(payload)
            return 1
        if "UPDATE osint_story" in sql:
            self.refresh_calls += 1
            return 1
        return 0
