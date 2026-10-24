# os_goal_ref — 合同参考实现（随 A1 冻结件入仓）

> **非生产代码**：G0 内核把等价逻辑迁入 \portal/os_goal_contract.py\ / \portal/os_goal_store.py\。
> 来源：交付包 \deliverables/2026-10-24_OS首版单事项闭环_研发计划与方案/tools/\。
> 范围：\os_gate_check.py\=单事项 v1.0 判据；\os_goal_check.py\=v1.1 三级关系判据；\os_samples.py\=合成样本；
> \	ools/contracts/item_contract_v1.schema.json\=v1.0 schema（v1.1 增量由 G0 补）。
> 运行：\python -m pytest tools/os_goal_ref/tests -q -p no:cacheprovider\（本仓 A1 已跑：43 passed）。
> 纪律：只读、零依赖、合成数据；不写真实账本、不启停服务。