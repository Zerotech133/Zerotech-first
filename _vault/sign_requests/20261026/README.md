# 7 张判例卡 · 待签 payload 清单（2026-10-26）

> 生成物：**未签名**。签名由本人在自己的终端执行，私钥不经过 Agent。

## 签名命令（逐条）

```powershell
$env:DSH_PRINCIPAL_PASSPHRASE = '<口令，或改用 --passphrase-file>'
python tools\sign_approval.py --kind experience_write --payload-file "C:\项目\老田的AI助手\_vault\sign_requests\20261026\EXP-SUPERVISION-001.payload.json" --private-key _vault\keys\principal_private.pem --i-am-human
python tools\sign_approval.py --kind experience_write --payload-file "C:\项目\老田的AI助手\_vault\sign_requests\20261026\EXP-SUPERVISION-002.payload.json" --private-key _vault\keys\principal_private.pem --i-am-human
python tools\sign_approval.py --kind experience_write --payload-file "C:\项目\老田的AI助手\_vault\sign_requests\20261026\EXP-SUPERVISION-003.payload.json" --private-key _vault\keys\principal_private.pem --i-am-human
python tools\sign_approval.py --kind experience_write --payload-file "C:\项目\老田的AI助手\_vault\sign_requests\20261026\EXP-SUPERVISION-004.payload.json" --private-key _vault\keys\principal_private.pem --i-am-human
python tools\sign_approval.py --kind experience_write --payload-file "C:\项目\老田的AI助手\_vault\sign_requests\20261026\EXP-SUPERVISION-005.payload.json" --private-key _vault\keys\principal_private.pem --i-am-human
python tools\sign_approval.py --kind experience_write --payload-file "C:\项目\老田的AI助手\_vault\sign_requests\20261026\EXP-SUPERVISION-006.payload.json" --private-key _vault\keys\principal_private.pem --i-am-human
python tools\sign_approval.py --kind experience_write --payload-file "C:\项目\老田的AI助手\_vault\sign_requests\20261026\EXP-SUPERVISION-007.payload.json" --private-key _vault\keys\principal_private.pem --i-am-human
```

## 入库（签名齐备后，人工执行）

```powershell
python tools\evolve_sign_payloads.py --apply --signature-dir "C:\项目\老田的AI助手\_vault\sign_requests\20261026"
```

## 清单

| EXP-SUPERVISION-001 | Z4-01 假成功 | conf 0.90 | `e97bcfd380ed3b2f…` |
| EXP-SUPERVISION-002 | Z4-02 快照假绿 | conf 0.90 | `3bdc93b16f80ce9b…` |
| EXP-SUPERVISION-003 | Z4-03 无对照组不算统计 | conf 0.85 | `bbbd9db02bff1cd5…` |
| EXP-SUPERVISION-004 | Z4-04 检测器自证 | conf 0.90 | `a297bf2e16889b61…` |
| EXP-SUPERVISION-005 | Z4-05 修A漏B | conf 0.85 | `fdab9a2439e6b9fa…` |
| EXP-SUPERVISION-006 | Z4-06 窗口对比的种子时序 | conf 0.80 | `0beedcc25654aa68…` |
| EXP-SUPERVISION-007 | Z4-07 零引用必须带对照组 | conf 0.95 | `80b4d42c205ccfc6…` |
