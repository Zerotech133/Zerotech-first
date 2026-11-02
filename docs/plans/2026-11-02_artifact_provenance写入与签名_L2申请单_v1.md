# artifact provenance 写入与签名 L2 申请单 v1（2026-11-02）

> STATUS: pending_approval(L2：待A0/老田批准，未执行)

> 状态：**pending_approval（未执行）**
> 目的：为当前 8103 artifact 补 provenance 并做 HMAC 签名；执行前需 A0/老田批准。

## 一、现状（只读证据）

```text
命令：python -X utf8 tools/release_provenance.py --verify --json
结果：rc=1
  provenance.json 不存在：
    C:\项目\_vault\legion_ide\release\legion-ide\20261031_143832_bfd3d5ec\provenance.json
  signature.status = unsigned / present=false
  manifest_sha256 无法验证；provenance target 为 None
```

结论：artifact 完整性/来源证据缺位，当前**不能**宣称 provenance verified。

## 二、拟执行命令（L2）

```powershell
python -X utf8 tools/release_provenance.py --write --key-file <vault-hmac-key-file>
```

影响面：

```text
写：C:\项目\_vault\legion_ide\release\legion-ide\20261031_143832_bfd3d5ec\provenance.json
不改：artifact 文件、manifest、current.json/current junction、release_legion_ide.py
签名：HMAC-SHA256（只做来源签名，不替代完整性校验）
```

## 三、不可逆点

```text
provenance.json 为新增文件；重复写入会覆盖同名文件。
执行前先备份旧 provenance.json（若存在）。
```

## 四、替代方案 / 已尝试

```text
1. --verify 只读：已执行，证明缺 provenance。
2. 不写 provenance，仅记录缺口：当前状态（不补齐证据）。
3. 写 unsigned provenance：不满足签名要求，不推荐。
4. 写 + HMAC 签名：本申请方案。
```

## 五、回滚

```text
删除/恢复 provenance.json 备份；不触碰 artifact/manifest/current。
```

## 六、五要素批准

```text
命令全文：见第二节
影响面：仅新增 provenance.json
不可逆点：覆盖同名 provenance（先备份）
替代方案：见第四节
回滚预案：见第五节
批准人：A0/老田（待批）
执行窗口：批准后立即，避开 merge 窗口
```

## 七、边界

```text
本单未执行 --write；未生成 provenance.json；未改 artifact。
```
