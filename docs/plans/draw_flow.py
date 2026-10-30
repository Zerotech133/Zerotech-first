from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

W, H = 1900, 1180
OUT = Path(r"C:\项目\老田的AI助手\.worktrees\WO-LEGION-ACAD-D1-FLOW-20261030\docs\plans\2026-10-30_司马徽鲁肃研究到决策闭环_flow.png")
FONT = r"C:\Windows\Fonts\msyh.ttc"
BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

img = Image.new("RGB", (W, H), "#f6f8fc")
d = ImageDraw.Draw(img)
font = ImageFont.truetype(FONT, 19)
small = ImageFont.truetype(FONT, 16)
tiny = ImageFont.truetype(FONT, 14)
title = ImageFont.truetype(BOLD, 32)
lane_font = ImageFont.truetype(BOLD, 22)


def box(x, y, w, h, text, fill="#ffffff", border="#4a6fa5", fg="#1c2a44", fs=None):
    d.rounded_rectangle([x, y, x + w, y + h], radius=12, fill=fill, outline=border, width=2)
    fs = fs or font
    lines = text.split("\n")
    heights = [d.textbbox((0, 0), ln, font=fs)[3] for ln in lines]
    total = sum(heights) + (len(lines) - 1) * 4
    cy = y + (h - total) / 2
    for ln, hh in zip(lines, heights):
        tw = d.textlength(ln, font=fs)
        d.text((x + (w - tw) / 2, cy), ln, font=fs, fill=fg)
        cy += hh + 4


def arrow(p1, p2, color="#4a78b8", width=3):
    d.line([p1, p2], fill=color, width=width)
    x2, y2 = p2
    d.polygon([(x2, y2), (x2 - 10, y2 - 6), (x2 - 10, y2 + 6)], fill=color)


def label(x, y, text, color="#4a6fa5", fs=None):
    d.text((x, y), text, font=fs or tiny, fill=color)


# lanes
d.rounded_rectangle([40, 110, 720, 1100], radius=18, fill="#eef5ff", outline="#8fb3d9", width=2)
d.rounded_rectangle([1030, 110, 1860, 1100], radius=18, fill="#fff8ea", outline="#d5b36a", width=2)
d.text((70, 128), "研究院 · 司马徽（ACAD1）", font=lane_font, fill="#24507f")
d.text((1060, 128), "投研裁决 · 鲁肃（D1）", font=lane_font, fill="#7a5410")

d.text((50, 45), "司马徽 × 鲁肃：研究到决策闭环 v1", font=title, fill="#16243d")
d.text((52, 84), "研究有效性 → 裁决可解释 → 结果可校准；无 L4 / 无冻结 = 不输出方向；零执行权", font=small, fill="#5a6a85")

# research boxes
x1, w1 = 70, 610
research = [
    (150, "ACAD1 立项\n问题 · 对象 · 风险边界"),
    (265, "STRAT1 假设\n可证伪条件 + 预期证据"),
    (380, "DATA1 数据冻结\nversion + lineage + sha256"),
    (495, "BACK1 回测\n样本外 + 成本/滑点压力"),
    (610, "ACAD1 评审\n八字段 + 证据分级"),
    (725, "联合评审\nNo-Go / 补证 / Go"),
    (870, "研究产物 vN\nL4 + 策略Spec + 证据包"),
]
for y, text in research:
    box(x1, y, w1, 78, text, border="#5a86b8")
for i in range(len(research) - 1):
    arrow((x1 + w1 // 2, research[i][0] + 78), (x1 + w1 // 2, research[i + 1][0] - 6), "#4a78b8")

# no-go loop
arrow((x1 - 2, 764), (x1 - 2, 304), "#c0504d", 3)
label(22, 500, "No-Go\n补证", "#c0504d")
# decision boxes
x2, w2 = 1055, 760
decision = [
    (150, "研究产物注册表\nartifact_id · version · L4 · spec · evidence"),
    (290, "D1 决策请求\nartifact_ref + xsec_profile(L1-L4) + T1/T2/N1/F1 + ST1"),
    (430, "D1 裁决\n五档 recommendation · confidence · l1_basis\n无 L4/无 artifact → WATCH / 弃权"),
    (570, "Decision Record\ndecision_id · artifact_ref · model · input_hash"),
    (710, "Paper / Shadow Outcome\n只读执行结果，不实盘下单"),
    (850, "Eval & Calibration\nBrier · 命中率 · 回撤 · ECE"),
    (990, "反馈到研究\n修正假设 / 标记 artifact 失效 / 新课题"),
]
for y, text in decision:
    box(x2, y, w2, 88, text, border="#c49a3a")
for i in range(len(decision) - 1):
    arrow((x2 + w2 // 2, decision[i][0] + 88), (x2 + w2 // 2, decision[i + 1][0] - 6), "#b8862f")

# go/试点 arrow: research product -> artifact registry (top right)
d.line([(x1 + w1, 909), (1000, 909), (1000, 190), (1055, 190)], fill="#2f7d4f", width=4)
d.polygon([(1055, 190), (1043, 184), (1043, 196)], fill="#2f7d4f")
label(725, 880, "Go / 试点", "#2f7d4f", small)

# feedback path: right feedback -> bottom margin -> left outside -> ACAD1
d.line([(x2, 1034), (1000, 1034), (1000, 1120), (20, 1120), (20, 190), (70, 190)],
       fill="#2f7d4f", width=3)
d.polygon([(70, 190), (58, 184), (58, 196)], fill="#2f7d4f")
label(1005, 1005, "结果回写 → 下一轮假设", "#2f7d4f", tiny)
label(735, 1100, "闭环反馈", "#2f7d4f", tiny)

# gates / legend
box(70, 1010, 610, 66, "硬门：冻结回执 + 回测登记 + L4 证据，缺一不得 Go", fill="#eaf4ec", border="#2f7d4f", fg="#1d4f34", fs=small)
box(1055, 150, 760, 0, "")  # no-op keep spacing
# bottom note
d.text((50, 1130), "红线：研究院不代 D1 裁决；D1 不写代码/不下单；任何方向建议都须经人审批。", font=small, fill="#3a4a63")

OUT.parent.mkdir(parents=True, exist_ok=True)
img.save(OUT)
print(OUT)
