# 09 · 前端设计

## 1. 组织方式

从“页面集合”重构为“功能模块集合”：

```
src/
  app/
    router.tsx
    layout/
    theme/
  features/
    market/
      api.ts
      hooks.ts
      types.ts
      components/
      pages/
    portfolio/
    research/
    system/
    ops/
  components/
    ui/
  services/
    http.ts
    websocket.ts
  store/
```

## 2. 工作区

保留 V1 的 4 工作区：

- 交易 Operate
- 资产 Portfolio
- 研究 Research
- 系统 System

加“设置 Settings”。

## 3. 行情模块

- 行情列表页
- 个股详情页
- 分时图
- 多周期K线切换
- 自选股（可选）

## 4. 状态管理

- Zustand 保留
- 按 feature 拆分 store
- WebSocket 状态独立管理
- 服务端状态用 React Query

## 5. 测试

- 每个 feature 至少一个组件测试
- 工具函数单测
- 关键链路 e2e 测试
- 修复当前导航重构导致的测试漂移

## 6. API 类型

建议从 OpenAPI 生成 TypeScript 类型，避免手写类型漂移。

## 7. 设计系统

保留现有 Design Tokens：

- 明亮日常态
- 暗色大屏态
- 市场涨跌配色规则
