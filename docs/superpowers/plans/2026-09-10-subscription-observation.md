# 订阅者观察平台策略产出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户订阅内置策略后，能在前端只读地查看该平台策略统一运行的持仓、逐笔成交/信号，且订阅的策略出现在「我的策略」列表。

**Architecture:** 一条独立、只读、与 owner 隔离解耦的链路——新增聚合 service + 一组订阅校验挡门的只读端点（只取平台 `ustrat-legacy-%` 数据），前端「我的策略」纳入订阅策略（复用 `/billing/subscriptions`），详情页展示持仓/成交·信号。经用户决策放开 `trades.py` 的合规红线（仅限本人订阅的策略，订阅校验为唯一授权门）。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2；React 19 / TypeScript / Ant Design 6 / Vitest / Playwright。

**规格：** `docs/superpowers/specs/2026-09-10-subscription-observation-design.md`

---

## 全局约定

- 命令前缀 `cd /Users/join/Desktop/zhice/zhice-os-v2`；后端测试 `PYTHONPATH=src .venv/bin/python -m pytest`。
- ⚠️**前端 vitest/build 必须在 `frontend` 目录跑**（`cd .../frontend && npx vitest run`）；在项目根跑会导致 jsdom 环境不加载、localStorage 报错全崩。
- ⚠️git 提交用 `git -C <root>` 避免切换 cwd（否则后端 uvicorn `--reload` 与前端测试目录错乱）。
- ⚠️本会话有 Fact-Forcing hook：每次 Write/Edit/Bash 前先答 4 问（调用方/无重复/数据结构/逐字引用用户指令）再重试。
- 每个提交结尾附 `Co-Authored-By: Claude <noreply@anthropic.com>`。
- 全程本地实现与验证；合并/部署需另行明确授权。
- 数据口径锚点：缠论 `v1-cn-strat-4` 的版本是 `v1-cn-sv-4`；平台运行实例标识前缀 `ustrat-legacy-`，用户私有实例前缀 `usi-`。

---

# 单元 1：后端观察链路

## Task 1-1：观察 service —— 平台持仓聚合

**Files:**
- Create: `src/zhice_os_v2/application/services/subscription_observation_service.py`
- Test: `tests/test_subscription_observation_service.py`

- [ ] **Step 1：写失败测试（持仓过滤口径）**

```python
# tests/test_subscription_observation_service.py
from types import SimpleNamespace
from zhice_os_v2.application.services.subscription_observation_service import (
    SubscriptionObservationService,
)


class _FakeDB:
    def __init__(self, versions, positions):
        self._versions = versions
        self._positions = positions
    def list_strategy_versions(self):
        return self._versions
    def list_positions(self):
        return self._positions


def _ver(sid, vid):
    return SimpleNamespace(strategy_id=sid, strategy_version_id=vid)


def _pos(vid, usid, **kw):
    base = dict(
        position_id=f"pos-{vid}-{usid}", strategy_version_id=vid, user_strategy_id=usid,
        ts_code="000001", market="CN", direction="LONG", open_price=10.0, close_price=None,
        open_qty=100.0, close_qty=0.0, pnl_amount=5.0, status="OPEN",
        opened_at=None, closed_at=None, account_id="acc-x", trade_plan_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_list_platform_positions_only_legacy_and_matching_version():
    db = _FakeDB(
        versions=[_ver("v1-cn-strat-4", "v1-cn-sv-4"), _ver("other", "v1-cn-sv-9")],
        positions=[
            _pos("v1-cn-sv-4", "ustrat-legacy-v1-cn-sv-4:CN"),  # 平台缠论 → 命中
            _pos("v1-cn-sv-4", "usi-alice-1"),                   # 用户私有实例 → 排除
            _pos("v1-cn-sv-9", "ustrat-legacy-v1-cn-sv-9:CN"),  # 别的策略 → 排除
            _pos("v1-cn-sv-4", None),                            # NULL 归属 → 排除
        ],
    )
    svc = SubscriptionObservationService(db=db)
    out = svc.list_platform_positions("v1-cn-strat-4")
    assert len(out) == 1
    assert out[0]["ts_code"] == "000001"
    assert "account_id" in out[0]  # 不脱敏，完整字段
    assert out[0]["pnl_amount"] == 5.0
```

- [ ] **Step 2：运行确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_subscription_observation_service.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3：实现 service（持仓部分）**

```python
# src/zhice_os_v2/application/services/subscription_observation_service.py
"""订阅者观察平台策略产出（只读）。只取平台统一运行实例（ustrat-legacy-*）的数据，
按策略版本过滤。订阅校验由端点层负责，本 service 不做权限判断。"""
from __future__ import annotations

from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

_LEGACY_PREFIX = "ustrat-legacy-"


def _position_view(p) -> dict:
    return {
        "position_id": p.position_id,
        "ts_code": p.ts_code,
        "market": p.market,
        "direction": p.direction,
        "open_price": float(p.open_price) if p.open_price is not None else None,
        "close_price": float(p.close_price) if p.close_price is not None else None,
        "open_qty": float(p.open_qty) if p.open_qty is not None else None,
        "close_qty": float(p.close_qty) if p.close_qty is not None else None,
        "pnl_amount": float(p.pnl_amount) if p.pnl_amount is not None else None,
        "status": p.status,
        "account_id": getattr(p, "account_id", None),
        "opened_at": p.opened_at.isoformat() if getattr(p, "opened_at", None) else None,
        "closed_at": p.closed_at.isoformat() if getattr(p, "closed_at", None) else None,
    }


class SubscriptionObservationService:
    def __init__(self, db: PersistencePort | None = None) -> None:
        if db is None:
            from zhice_os_v2.api.dependencies import get_db
            db = get_db()
        self._db = db

    def _version_ids(self, strategy_id: str) -> set[str]:
        return {
            v.strategy_version_id
            for v in self._db.list_strategy_versions()
            if getattr(v, "strategy_id", None) == strategy_id
        }

    def list_platform_positions(self, strategy_id: str) -> list[dict]:
        vids = self._version_ids(strategy_id)
        return [
            _position_view(p)
            for p in self._db.list_positions()
            if p.strategy_version_id in vids
            and (p.user_strategy_id or "").startswith(_LEGACY_PREFIX)
        ]
```

- [ ] **Step 4：运行确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_subscription_observation_service.py -v`
Expected: PASS

- [ ] **Step 5：提交**

```bash
git -C /Users/join/Desktop/zhice/zhice-os-v2 add src/zhice_os_v2/application/services/subscription_observation_service.py tests/test_subscription_observation_service.py
git -C /Users/join/Desktop/zhice/zhice-os-v2 commit -m "feat(subscription): aggregate platform positions for subscribers" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

## Task 1-2：观察 service —— 平台成交/信号聚合

**Files:**
- Modify: `src/zhice_os_v2/application/services/subscription_observation_service.py`
- Test: `tests/test_subscription_observation_trades.py`

成交需 `fill→execution→request→intent` join（参照 `trades.py:56-72` 的 join 写法）。用隔离 sqlite + ORM 建最小数据。

- [ ] **Step 1：核对 ORM 字段名**

Run: `grep -n "class OrderIntentModel\|class FillRecordModel\|class ExecutionRecordModel\|class OrderRequestModel\|intent_side\|side\b" src/zhice_os_v2/infrastructure/db/models.py`
用途：确认 `FillRecordModel.side` 是否存在（否则 signal 取 `OrderIntentModel.intent_side`）、各表主外键列名，据此校正 Step 2/3。

- [ ] **Step 2：写失败测试（成交 join + entry/exit 投影 + legacy 过滤）**

```python
# tests/test_subscription_observation_trades.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.db import models as orm


@pytest.fixture
def obs_env(monkeypatch):
    import zhice_os_v2.application.services.subscription_observation_service as mod
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(mod, "get_session", lambda: Session(), raising=False)
    return Session


def test_list_platform_trades_joins_and_projects_signal(obs_env):
    s = obs_env()
    s.add(orm.StrategyVersionModel(strategy_version_id="v1-cn-sv-4", strategy_id="v1-cn-strat-4",
                                   market="CN", version_no="v4", status="ACTIVE", spec={}))
    s.add(orm.OrderIntentModel(intent_id="int-1", user_strategy_id="ustrat-legacy-v1-cn-sv-4:CN",
                               intent_type="OPEN", intent_side="BUY", status="NEW"))
    s.add(orm.OrderRequestModel(request_id="req-1", intent_id="int-1"))
    s.add(orm.ExecutionRecordModel(execution_id="exe-1", request_id="req-1"))
    s.add(orm.FillRecordModel(fill_id="fil-1", execution_id="exe-1", market="CN", ts_code="000001",
                              fill_price=10.0, fill_qty=100.0, fill_amount=1000.0, realized_pnl=3.0, side="BUY"))
    s.commit(); s.close()

    from zhice_os_v2.application.services.subscription_observation_service import (
        SubscriptionObservationService,
    )
    out = SubscriptionObservationService().list_platform_trades("v1-cn-strat-4")
    assert len(out) == 1
    assert out[0]["signal"] == "entry"         # BUY → 入场
    assert out[0]["realized_pnl"] == 3.0        # 含盈亏（红线已放开）
    assert out[0]["ts_code"] == "000001"
```

> ORM 字段名（`OrderIntentModel.intent_side`、`FillRecordModel.side`、各主外键）以 Step 1 核对结果为准，测试构造按真实字段调整。若 `FillRecordModel` 无 `side`，在下方实现里从 `intent.intent_side` 取信号。

- [ ] **Step 3：运行确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_subscription_observation_trades.py -v`
Expected: FAIL（`list_platform_trades` 未定义）

- [ ] **Step 4：实现 list_platform_trades**

```python
# 追加到 subscription_observation_service.py 顶部 import 区
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session


def _signal_of(side: str | None) -> str:
    return "entry" if str(side or "").upper() == "BUY" else "exit"


# SubscriptionObservationService 内新增方法：
    def list_platform_trades(self, strategy_id: str) -> list[dict]:
        vids = self._version_ids(strategy_id)
        legacy_pfxs = [f"ustrat-legacy-{vid}:" for vid in vids]
        session = get_session()
        try:
            rows = (
                session.query(orm.FillRecordModel, orm.OrderIntentModel)
                .join(orm.ExecutionRecordModel,
                      orm.FillRecordModel.execution_id == orm.ExecutionRecordModel.execution_id)
                .join(orm.OrderRequestModel,
                      orm.ExecutionRecordModel.request_id == orm.OrderRequestModel.request_id)
                .join(orm.OrderIntentModel,
                      orm.OrderRequestModel.intent_id == orm.OrderIntentModel.intent_id)
                .all()
            )
            out = []
            for fill, intent in rows:
                usid = intent.user_strategy_id or ""
                if not any(usid.startswith(pfx) for pfx in legacy_pfxs):
                    continue
                side = getattr(fill, "side", None) or getattr(intent, "intent_side", None)
                out.append({
                    "fill_id": fill.fill_id,
                    "market": fill.market,
                    "ts_code": fill.ts_code,
                    "fill_price": float(fill.fill_price) if fill.fill_price is not None else None,
                    "fill_qty": float(fill.fill_qty) if fill.fill_qty is not None else None,
                    "fill_amount": float(fill.fill_amount) if fill.fill_amount is not None else None,
                    "realized_pnl": float(fill.realized_pnl) if fill.realized_pnl is not None else None,
                    "fill_time": fill.fill_time.isoformat() if getattr(fill, "fill_time", None) else None,
                    "signal": _signal_of(side),
                })
            return out
        finally:
            session.close()
```

- [ ] **Step 5：运行确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_subscription_observation_trades.py -v`
Expected: PASS

- [ ] **Step 6：提交**

```bash
git -C /Users/join/Desktop/zhice/zhice-os-v2 add src/zhice_os_v2/application/services/subscription_observation_service.py tests/test_subscription_observation_trades.py
git -C /Users/join/Desktop/zhice/zhice-os-v2 commit -m "feat(subscription): aggregate platform trades with entry/exit signal" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

## Task 1-3：只读端点 + 订阅校验挡门

**Files:**
- Create: `src/zhice_os_v2/api/routers/subscription_observation.py`
- Modify: `src/zhice_os_v2/api/main.py`（注册 router）
- Test: `tests/test_subscription_observation_api.py`

- [ ] **Step 1：写失败测试（订阅者 200 / 未订阅 403）**

```python
# tests/test_subscription_observation_api.py
from fastapi.testclient import TestClient
from zhice_os_v2.api.main import app
from zhice_os_v2.application.services.token_service import create_token


def _headers(uid="u-sub", role="USER"):
    return {"Authorization": f"Bearer {create_token(uid, 'sub', role)}"}


def test_positions_requires_active_subscription(monkeypatch):
    import zhice_os_v2.api.routers.subscription_observation as mod
    monkeypatch.setattr(mod.SubscriptionService, "is_subscribed", lambda self, u, s: False)
    c = TestClient(app)
    r = c.get("/api/v1/my/subscriptions/v1-cn-strat-4/positions", headers=_headers())
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "billing.not_subscribed"


def test_positions_visible_to_subscriber(monkeypatch):
    import zhice_os_v2.api.routers.subscription_observation as mod
    monkeypatch.setattr(mod.SubscriptionService, "is_subscribed", lambda self, u, s: True)
    monkeypatch.setattr(mod.SubscriptionObservationService, "list_platform_positions",
                        lambda self, sid: [{"ts_code": "000001", "pnl_amount": 5.0}])
    c = TestClient(app)
    r = c.get("/api/v1/my/subscriptions/v1-cn-strat-4/positions", headers=_headers())
    assert r.status_code == 200
    assert r.json()[0]["ts_code"] == "000001"
```

- [ ] **Step 2：运行确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_subscription_observation_api.py -v`
Expected: FAIL（路由未注册 → 404）

- [ ] **Step 3：实现 router + 注册**

```python
# src/zhice_os_v2/api/routers/subscription_observation.py
"""订阅者只读观察平台策略产出。订阅校验为唯一授权门（放开了 trades.py 对内置成交的
合规红线——仅限本人订阅的策略，见 spec 合规决策）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.application.services.membership_service import SubscriptionService
from zhice_os_v2.application.services.subscription_observation_service import (
    SubscriptionObservationService,
)
from zhice_os_v2.domain.security.user import User

router = APIRouter(prefix="/my/subscriptions", tags=["subscription-observation"])


def _require_subscribed(user: User, strategy_id: str) -> None:
    if not SubscriptionService().is_subscribed(user.user_id, strategy_id):
        raise HTTPException(status_code=403, detail={"code": "billing.not_subscribed"})


@router.get("/{strategy_id}/positions")
def subscription_positions(strategy_id: str, user: User = Depends(get_current_user)):
    _require_subscribed(user, strategy_id)
    return SubscriptionObservationService().list_platform_positions(strategy_id)


@router.get("/{strategy_id}/trades")
def subscription_trades(strategy_id: str, user: User = Depends(get_current_user)):
    _require_subscribed(user, strategy_id)
    return SubscriptionObservationService().list_platform_trades(strategy_id)
```

在 `api/main.py` 注册（先 `grep -n "include_router" src/zhice_os_v2/api/main.py` 核对现有写法，紧邻 billing router 注册处按同样 prefix 风格加）：

```python
from zhice_os_v2.api.routers import subscription_observation
app.include_router(subscription_observation.router, prefix="/api/v1")
```

- [ ] **Step 4：运行确认通过 + 全量后端回归**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_subscription_observation_api.py -v`
Expected: PASS
Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`
Expected: 全绿

- [ ] **Step 5：提交**

```bash
git -C /Users/join/Desktop/zhice/zhice-os-v2 add src/zhice_os_v2/api/routers/subscription_observation.py src/zhice_os_v2/api/main.py tests/test_subscription_observation_api.py
git -C /Users/join/Desktop/zhice/zhice-os-v2 commit -m "feat(subscription): expose read-only observation endpoints" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

# 单元 2：前端「我的策略」纳入订阅

> **执行前先读** `frontend/src/pages/MyStrategiesPage.tsx`、`frontend/src/services/myStrategiesApi.ts`、`billingApi.ts`（`listSubscriptions`/`listBuiltinStrategies`）了解现有结构与列表渲染，按其模式接入，勿重写无关部分。

## Task 2-1：订阅观察 API 客户端

**Files:**
- Create: `frontend/src/services/subscriptionObservationApi.ts`
- Test: `frontend/src/test/subscriptionObservationApi.test.ts`

- [ ] **Step 1：写失败契约测试**

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getSubscriptionPositions, getSubscriptionTrades } from '../services/subscriptionObservationApi';

let fetchSpy: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  fetchSpy = vi.spyOn(globalThis, 'fetch');
  localStorage.setItem('zhice_token', 't'); localStorage.setItem('zhice_lang', 'zh');
});
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

function mockOnce(data: unknown) {
  fetchSpy.mockResolvedValueOnce({ ok: true, status: 200, json: async () => data, text: async () => JSON.stringify(data) } as unknown as Response);
}

describe('subscriptionObservationApi', () => {
  it('getSubscriptionPositions GET 正确 URL + encode', async () => {
    mockOnce([{ ts_code: '000001' }]);
    await getSubscriptionPositions('v1-cn-strat-4');
    expect(fetchSpy.mock.calls[0][0]).toBe('/api/v1/my/subscriptions/v1-cn-strat-4/positions');
  });
  it('getSubscriptionTrades 透传 AbortSignal', async () => {
    mockOnce([]);
    const ac = new AbortController();
    await getSubscriptionTrades('v1-cn-strat-4', ac.signal);
    expect((fetchSpy.mock.calls[0][1] as RequestInit)?.signal).toBe(ac.signal);
  });
});
```

- [ ] **Step 2：运行确认失败**

Run: `cd /Users/join/Desktop/zhice/zhice-os-v2/frontend && npx vitest run src/test/subscriptionObservationApi.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3：实现客户端**

```typescript
// frontend/src/services/subscriptionObservationApi.ts
import { api } from './api';

export interface PlatformPosition {
  position_id: string; ts_code: string; market: string; direction: string;
  open_price: number | null; close_price: number | null;
  open_qty: number | null; close_qty: number | null; pnl_amount: number | null;
  status: string; account_id: string | null; opened_at: string | null; closed_at: string | null;
}
export interface PlatformTrade {
  fill_id: string; market: string; ts_code: string;
  fill_price: number | null; fill_qty: number | null; fill_amount: number | null;
  realized_pnl: number | null; fill_time: string | null; signal: 'entry' | 'exit';
}

const enc = (v: string) => encodeURIComponent(v);

export function getSubscriptionPositions(strategyId: string, signal?: AbortSignal): Promise<PlatformPosition[]> {
  return api<PlatformPosition[]>(`/my/subscriptions/${enc(strategyId)}/positions`, { signal });
}
export function getSubscriptionTrades(strategyId: string, signal?: AbortSignal): Promise<PlatformTrade[]> {
  return api<PlatformTrade[]>(`/my/subscriptions/${enc(strategyId)}/trades`, { signal });
}
```

- [ ] **Step 4：运行确认通过**

Run: `cd /Users/join/Desktop/zhice/zhice-os-v2/frontend && npx vitest run src/test/subscriptionObservationApi.test.ts`
Expected: PASS

- [ ] **Step 5：提交**

```bash
git -C /Users/join/Desktop/zhice/zhice-os-v2 add frontend/src/services/subscriptionObservationApi.ts frontend/src/test/subscriptionObservationApi.test.ts
git -C /Users/join/Desktop/zhice/zhice-os-v2 commit -m "feat(subscription): add observation api client" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

## Task 2-2：MyStrategiesPage 纳入订阅策略（只读标记）

**Files:**
- Modify: `frontend/src/pages/MyStrategiesPage.tsx`
- Create: `frontend/src/test/MyStrategiesSubscriptions.test.tsx`
- i18n: `frontend/src/i18n/locales/{zh,en}/<MyStrategiesPage 的命名空间>.json`（执行前 grep 该页 `useTranslation('...')` 确认）

- [ ] **Step 1：读组件 + 写失败测试**

先 `cat frontend/src/pages/MyStrategiesPage.tsx`，了解它用哪些 service 加载、列表行结构、自建操作按钮（编辑/删除/发布）。然后写测试（替换下面注释为对齐实现的具体 mock/断言）：

```typescript
// frontend/src/test/MyStrategiesSubscriptions.test.tsx
// mock listMyStrategies（本人私有）+ listSubscriptions（billingApi，返回含 effective_status 的订阅）
// + listBuiltinStrategies/listGrantableStrategies（取订阅策略名称/市场）
// 断言：
//  1. 已订阅的内置策略出现在列表（名称可见）
//  2. 该行有"已订阅" Tag（i18n subscribedTag）
//  3. 该行无"编辑/删除/发布"等自建操作按钮（queryByRole('button',{name:...}) 为 null）
```

- [ ] **Step 2：运行确认失败**

Run: `cd /Users/join/Desktop/zhice/zhice-os-v2/frontend && npx vitest run src/test/MyStrategiesSubscriptions.test.tsx`
Expected: FAIL

- [ ] **Step 3：实现**

在 `MyStrategiesPage` 加载本人私有策略的同时，额外调 `listSubscriptions()`（`billingApi`），取 `effective_status ∈ {active,permanent}` 的订阅策略 id；用 `listBuiltinStrategies()`/catalog 取其名称与市场，作为"已订阅"分组/行渲染：
- 行加 `Tag`「已订阅」（i18n `subscribedTag`，中「已订阅」/英「Subscribed」）。
- 订阅行**只读**：条件渲染排除编辑/删除/发布/新建版本等自建操作。
- 行点击 → 跳 `StrategyDetailPage`（沿用现有详情跳转方式）。

- [ ] **Step 4：运行确认通过 + 前端全量 + build**

Run: `cd /Users/join/Desktop/zhice/zhice-os-v2/frontend && npx vitest run && npm run build`
Expected: 全绿 + 构建成功

- [ ] **Step 5：提交**

```bash
git -C /Users/join/Desktop/zhice/zhice-os-v2 add frontend/src/pages/MyStrategiesPage.tsx frontend/src/test/MyStrategiesSubscriptions.test.tsx frontend/src/i18n/locales/zh/*.json frontend/src/i18n/locales/en/*.json
git -C /Users/join/Desktop/zhice/zhice-os-v2 commit -m "feat(subscription): show subscribed strategies in my strategies" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

# 单元 3：前端详情页观察区 + E2E

> **执行前先读** `frontend/src/pages/StrategyDetailPage.tsx` 了解现有分区/Tab 结构与它已有的名称/performance 展示，按其模式新增区块。

## Task 3-1：StrategyDetailPage 持仓/成交·信号区

**Files:**
- Modify: `frontend/src/pages/StrategyDetailPage.tsx`
- Create: `frontend/src/test/StrategyDetailObservation.test.tsx`
- i18n: `frontend/src/i18n/locales/{zh,en}/<该页命名空间>.json`

- [ ] **Step 1：读组件 + 写失败测试**

先读 `StrategyDetailPage.tsx`。测试（替换注释为对齐实现的 mock/断言）：

```typescript
// frontend/src/test/StrategyDetailObservation.test.tsx
// mock subscriptionObservationApi.getSubscriptionPositions/getSubscriptionTrades
// + 订阅判定来源（listSubscriptions）
// 断言：
//  - 订阅时：渲染"平台持仓"表（含某 ts_code）+ "成交·信号"表（signal 列 入场/出场 Tag）
//  - 未订阅时：不渲染这两块，且不调用 getSubscriptionPositions
```

- [ ] **Step 2：运行确认失败**

Run: `cd /Users/join/Desktop/zhice/zhice-os-v2/frontend && npx vitest run src/test/StrategyDetailObservation.test.tsx`
Expected: FAIL

- [ ] **Step 3：实现观察区**

当前用户对该策略已订阅时（`listSubscriptions` 判定 `effective_status ∈ {active,permanent}`），新增两块（分区或 Tab），各自独立 `useState` loading、`AbortController` 防迟到、失败互不影响：
- **平台持仓**：`getSubscriptionPositions(strategyId, signal)` → `antd Table`：标的/市场/方向/开平价/数量/盈亏/状态/开平时间。
- **成交·信号**：`getSubscriptionTrades(strategyId, signal)` → `antd Table`：成交时间/标的/方向/价格/盈亏/`signal`（`entry`→入场 Tag、`exit`→出场 Tag）。
- 非订阅者不渲染。i18n 新键：`secPlatformPositions`/`secPlatformTrades`/`signalEntry`/`signalExit`。

- [ ] **Step 4：运行确认通过 + 前端全量 + build**

Run: `cd /Users/join/Desktop/zhice/zhice-os-v2/frontend && npx vitest run && npm run build`
Expected: 全绿 + 构建成功

- [ ] **Step 5：提交**

```bash
git -C /Users/join/Desktop/zhice/zhice-os-v2 add frontend/src/pages/StrategyDetailPage.tsx frontend/src/test/StrategyDetailObservation.test.tsx frontend/src/i18n/locales/zh/*.json frontend/src/i18n/locales/en/*.json
git -C /Users/join/Desktop/zhice/zhice-os-v2 commit -m "feat(subscription): show platform positions and trades on strategy detail" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

## Task 3-2：Playwright E2E

**Files:**
- Create: `frontend/e2e/subscription-observation.spec.ts`

- [ ] **Step 1：写 E2E（真实登录 + route mock，参照 e2e/admin-entitlements.spec.ts）**

沿用 `admin-entitlements.spec.ts` 的 `setupAdmin`（真实 admin 登录）与 `page.route` mock 模式；系统 Chrome、无 video。用例：
- route mock `/api/v1/billing/subscriptions`（含已订阅内置）、`/my/subscriptions/v1-cn-strat-4/positions`、`.../trades`、`/billing/strategies`。
- 登录后进「我的策略」→ 断言看到已订阅缠论（只读、无自建按钮）→ 进详情页 → 断言"平台持仓"与"成交·信号"区渲染（signal 列入场/出场）。

- [ ] **Step 2：运行**

Run: `cd /Users/join/Desktop/zhice/zhice-os-v2/frontend && npx playwright test subscription-observation`
Expected: 全绿（webServer 自起 vite，需后端 8102 在跑）

- [ ] **Step 3：提交**

```bash
git -C /Users/join/Desktop/zhice/zhice-os-v2 add frontend/e2e/subscription-observation.spec.ts
git -C /Users/join/Desktop/zhice/zhice-os-v2 commit -m "test(e2e): cover subscription observation" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 最终门禁

- 后端：`PYTHONPATH=src .venv/bin/python -m pytest -q` 全绿；`ruff check src/zhice_os_v2 tests` 干净；`mypy src/zhice_os_v2`（不新增错误，既有 5 个 get_db 联合类型通病除外）。
- 前端：`cd frontend && npx vitest run` 全绿 + `npm run build` 成功 + `npx playwright test` 全绿。
- `git diff --check` 无输出；无 DB 迁移（`alembic heads` 仍 0030）。

## 规格覆盖自检

| 规格要求 | 对应任务 |
|---|---|
| 平台持仓只读观察（只取 ustrat-legacy-） | 1-1 |
| 平台成交/信号 entry/exit（含盈亏，放开红线） | 1-2 |
| 订阅校验挡门（未订阅/退订 403） | 1-3 |
| 订阅策略入「我的策略」只读标记 | 2-1、2-2 |
| 详情页持仓/成交·信号区 | 3-1 |
| E2E | 3-2 |
| 无迁移 / 不动 owner 隔离与老端点红线 | 全程（新端点独立） |
