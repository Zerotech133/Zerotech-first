# governance_upgrade 变更日志

## governance_upgrade.v1 (2026-11-06)
- 初始基线升级包：与 yt-clean-kit v1.2 规则集（yt-clean-rules.v1）完全一致。
- `pii_gate.types = [email, url, phone, contact, handle]`；清洗端 E 路线仅只读守门，不二次变换 pii。
- 本机 `sync-back` 经 unittest 回归门禁（28 用例）通过后应用；`rules_dest` 默认 `D:\rules.json`。
- 后续若 M1 导出端回执要求调整 `pii_gate`（如新增/缩减类型），仅改 `types` 一行即可发 v2，无需动代码。
