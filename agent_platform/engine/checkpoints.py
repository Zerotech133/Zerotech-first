# -*- coding: utf-8 -*-
"""checkpoint + trace 落 SQLite（K-STOCK-004，PoC 本地版）

借鉴 LangGraph 思想：节点级状态快照——崩溃后可从最后完成节点恢复（菜鸟缺的能力）。
接口对齐未来 PG 迁移：start_run / save_node / trace / get_run_states。

K-ORG-090/G4：工单线（devtask）checkpoint 扩展——task_checkpoints 表 +
save_task_node / get_task_states / task_trace 三接口，服务工单链续跑。
"""
import json
import sqlite3
import threading
import time
from pathlib import Path


class Checkpointer:
    def __init__(self, db_path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()  # 并行节点共用连接，写入必须串行化
        self._init()

    def _init(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs(
                run_id TEXT PRIMARY KEY, stock TEXT,
                started_at REAL, finished_at REAL, status TEXT);
            CREATE TABLE IF NOT EXISTS checkpoints(
                run_id TEXT, node_key TEXT, agent_id TEXT,
                state_json TEXT, ts REAL,
                PRIMARY KEY(run_id, node_key));
            CREATE TABLE IF NOT EXISTS traces(
                run_id TEXT, node_key TEXT, agent_id TEXT, phase TEXT,
                round INTEGER, output TEXT, similarity REAL,
                status TEXT, latency_ms REAL, ts REAL);
            CREATE TABLE IF NOT EXISTS task_checkpoints(
                task_id TEXT, node_key TEXT, agent_id TEXT,
                state_json TEXT, ts REAL,
                PRIMARY KEY(task_id, node_key));
            """
        )
        self.conn.commit()

    def start_run(self, run_id, stock):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?)",
                (run_id, stock, time.time(), None, "running"),
            )
            self.conn.commit()

    def finish_run(self, run_id, status="done"):
        with self._lock:
            self.conn.execute(
                "UPDATE runs SET finished_at=?, status=? WHERE run_id=?",
                (time.time(), status, run_id),
            )
            self.conn.commit()

    def save_node(self, run_id, node_key, agent_id, state):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?,?)",
                (run_id, node_key, agent_id, json.dumps(state, ensure_ascii=False), time.time()),
            )
            self.conn.commit()

    def trace(self, run_id, node_key, agent_id, phase, round_=None,
              output="", similarity_=None, status="normal", latency_ms=None):
        with self._lock:
            self.conn.execute(
                "INSERT INTO traces VALUES(?,?,?,?,?,?,?,?,?,?)",
                (run_id, node_key, agent_id, phase, round_, output[:2000],
                 similarity_, status, latency_ms, time.time()),
            )
            self.conn.commit()

    def get_run_states(self, run_id):
        with self._lock:
            cur = self.conn.execute(
                "SELECT node_key, state_json FROM checkpoints WHERE run_id=?", (run_id,)
            )
            return {k: json.loads(v) for k, v in cur.fetchall()}

    # ---- K-ORG-090/G4：工单线（devtask）checkpoint ----

    def save_task_node(self, task_id, node_key, agent_id, state):
        """工单节点状态快照：同 (task_id, node_key) 覆盖写（INSERT OR REPLACE）。"""
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO task_checkpoints VALUES(?,?,?,?,?)",
                (task_id, node_key, agent_id,
                 json.dumps(state, ensure_ascii=False), time.time()),
            )
            self.conn.commit()

    def get_task_states(self, task_id):
        """按 node_key 取工单全部节点最新状态，返回 {node_key: state_dict}。"""
        with self._lock:
            cur = self.conn.execute(
                "SELECT node_key, state_json FROM task_checkpoints WHERE task_id=?",
                (task_id,),
            )
            return {k: json.loads(v) for k, v in cur.fetchall()}

    def task_trace(self, task_id):
        """工单轨迹按 ts 升序，返回 [(node_key, agent_id, ts, state_dict)]，无则空列表。"""
        with self._lock:
            cur = self.conn.execute(
                "SELECT node_key, agent_id, ts, state_json FROM task_checkpoints"
                " WHERE task_id=? ORDER BY ts ASC",
                (task_id,),
            )
            return [(k, a, ts, json.loads(v)) for k, a, ts, v in cur.fetchall()]
