# 报告基线模板定版声明（2026-11-10 P2）

## 裁决

- **基线 = `report_baseline.html`**（源自 fp `c4ab8b5c` 盘后复盘模板，GitHub Dark 族，
  已把硬编码 hex 转成 `/static/tokens.css` 语义变量引用）。
- **适用**：今后**新增**的报告类生成器（盘后/扫描/复盘/RPS/板块…）一律以本文件为骨架。
- **存量不追改**：已生成的 ~158 页报告是一次性产物会过期，按普查报告裁定不改写；
  各稳定模板（缠论日报/RPS/板块平台/纸面持仓/许褚营/分析台）在自然重写时迁移到基线。

## 三条硬规矩

1. **禁止新模板**：新报告再出现独立 `<style>` 指纹（普查用 style 块 md5 分组检测）
   即为违规——只允许基线层 + 页面极少量特有布局层。
2. **配色只读 tokens**：`var(--gray-*/--blu-500/--color-up/--color-dn/…)`，
   唯一例外是 tokens 无对应阶的混合底（基线中已标注保留的 `#2b2f1a`）。
3. **涨红跌绿**：buy/多/利多 = `--color-up`（红），avoid/空/利空 = `--color-dn`（绿），
   与状态色（成功绿/故障红）严格分开——见 tokens.css 头部 2026-10-01 拍板语义。

## 指纹自检

```bash
python - <<'EOF'
import io, re, hashlib
raw = io.open('report_baseline.html', encoding='utf-8').read()
css = ''.join(re.findall(r'<style[^>]*>([\s\S]*?)</style>', raw))
print('baseline fp =', hashlib.md5(re.sub(r'\s+',' ',css).encode()).hexdigest()[:8])
EOF
```

新报告页的 fp 允许 = 基线 fp，或「基线 fp + 页面特有层」；出现全新 fp 须先在本文件登记再生成。

## 背景

普查报告：`deliverables/2026-11-10_全项目UI普查与研究_v1.md` §P2
（26 种模板并存，6 个稳定模板 + ~20 一次性模板，全部内联无 token）。
