# 指挥联动台 · 盒子部署包（3588/AMD 云端交付形态 · 2026-10-23）

> 第三拼图：council_ops 零外部依赖，任意 Linux 盒子 10 分钟部署。

---

## 一、包内容（本目录即交付物）

| 文件 | 作用 |
|---|---|
| `portal/council_ops.py` | 交互桥（纯 Python 标准库，零 pip 依赖） |
| `portal/os_council.html` | 前端（单文件，零构建） |
| `portal/themes/*.json` | 皮肤包（客户复制改字段即品牌化） |
| `portal/council_theme.default.json` | 默认主题模板 |
| `tools/council_review_client.py` | 真席位客户端（可选，接 Hermes WebUI） |

## 二、安装（Linux 盒子，root）

```bash
# 1. 拷贝到盒子（scp/U盘均可）
mkdir -p /opt/council && cp -r portal /opt/council/

# 2. systemd 服务（开机自启）
cat > /etc/systemd/system/council-ops.service <<'EOF'
[Unit]
Description=Council Ops (指挥联动台交互桥)
After=network.target

[Service]
WorkingDirectory=/opt/council/portal
ExecStart=/usr/bin/python3 council_ops.py
Restart=always
RestartSec=5
Environment=COUNCIL_FEISHU_WEBHOOK=${客户飞书机器人URL:留空则落待发队列}

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable --now council-ops

# 3. 前端静态直服（nginx 或 python -m http.server 皆可）
#    nginx: location /os_council.html { root /opt/council/portal; }
#    数据接口跨域已内置 CORS（127.0.0.1 桥 + 任意前端源）
```

## 三、验收清单（部署后逐项打勾）

- [ ] `curl http://127.0.0.1:8798/healthz` 返回 `{"ok": true}`
- [ ] 浏览器开 `/os_council.html`，顶部输入「现在开会测试」→ 对话流出现议题
- [ ] 说「预约明天9点开会」→ 右栏已预约出现条目
- [ ] 说「我有个想法：xxx」→ 想法池 + （若配 Hermes）三席位对答
- [ ] 说「定稿：决议x。张三做y」→ 建档 + 纪要库可点开全文
- [ ] （配了飞书 webhook）飞书收到定稿卡片，按钮可开页面
- [ ] 换肤：顶栏切 tech_night → 全站变色、专家路由随皮肤

## 四、规模化要点

- **ARM 盒（3588）**：Python 3.9+ 即跑（无 arch 依赖）；模型推理建议放云端/AMD 盒，3588 只跑桥与前端（云边协同：边缘开会、云端思考）
- **AMD 大盒**：可同时承载 Hermes WebUI（8787）= 完整真席位对答
- **多客户**：每盒一份 themes/，隔离数据在 portal/data/
- 升级 = 覆盖两个文件（council_ops.py + os_council.html），数据不动

## 五、边界（诚实）

- 飞书 webhook 未配时通知落待发队列（不丢），配了即发
- 真席位对答需 Hermes WebUI（8787）在线；无则对答降级为规则兜底（标注代行）
- 辩论协议全速需快速模型（9b 慢，glm-flash 档已列路线）
