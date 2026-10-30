# -*- coding: utf-8 -*-
"""军团分析 AI · LCS-1 全链固化版（K-ORG-120/LCS-1 判例产物）

固化自 2026-10-16 群辩演练（legion_debate_600337.py 临时版）。
流水线：xsec_profile v2 漏斗(L1-L4) → T1⇄T2 假设攻防 → N1/F1 佐证 → D1 裁决
        → R1⇄R2 风控辩论 → R3 仲裁 → TR1 执行单 → LCS-1 标准报告

工程判例内置：
  - 模型降级链 qwen3.5:9b（qwen2.5:7b 已于 10-24 清理，旧名会 404；900s 超时/JSON 抖动判例见 K-STOCK-428）
  - 断点续跑（ckpt，Ollama 断连不废整场）
  - 4 次重试 × 指数退避
  - 五虎 LCS-1 新人设（T1/T2 假设攻防制 / D1 存疑禁 BUY/SELL 铁律）

用法：
  python tools/legion_debate.py 600337            # 全链分析
  python tools/legion_debate.py 600337 --json     # 纯结构化输出
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from xsec_profile import profile as xsec_profile  # noqa: E402
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

MODEL_CHAIN = ["qwen3.5:9b"]          # 10-26：qwen2.5:7b 已清理（404），改在役 9b
PROMPTS_DIR = ROOT / "agent_platform" / "prompts"
OUT_DIR = ROOT / "reports" / "legion"
CKPT_DIR = Path(r"C:/Temp/legion_ckpt")


def load_persona(aid: str) -> str:
    """从军团 prompts 目录读人设（单一权威源：改人设不改本工具）。"""
    m = {"T1": "t1_bull", "T2": "t2_bear", "N1": "n1_sentiment", "F1": "f1_fundamental",
         "D1": "d1_decision", "R1": "r1_risk", "R2": "r2_risk", "R3": "r3_risk", "TR1": "trade_executor"}
    return (PROMPTS_DIR / f"{m[aid]}.md").read_text(encoding="utf-8").strip()


def chat(prompt: str, tag: str, ckpt: dict, max_tokens: int = 600) -> str:
    if tag in ckpt:
        return ckpt[tag]
    body = json.dumps({"model": MODEL_CHAIN[0], "prompt": prompt, "stream": False,
                       "options": {"temperature": 0.6, "num_predict": max_tokens}}).encode()
    for attempt in range(4):
        try:
            req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body,
                                         headers={"Content-Type": "application/json"})
            with safe_urlopen(req, timeout=300) as r:
                out = json.loads(r.read())["response"].strip()
                ckpt[tag] = out
                return out
        except Exception as e:
            if attempt == 3:
                out = f"[{aid_absent(type(e).__name__)}] 此席缺席，按数据不足纪律处理"
                ckpt[tag] = out
                return out
            time.sleep(8 * (attempt + 1))
    return ""


def aid_absent(err: str) -> str:
    return f"模型不可达:{err}"


def funnel_digest(p: dict) -> str:
    fu = p.get("funnel", {})
    pr = fu.get("primary_hypothesis", {})
    l2c = fu.get("layer2_event_couple", [])
    couples = "; ".join(f"抢跑{c['runup_day']}→公告耦合" for c in l2c[:2]) or "无"
    return f"""【LCS-1 漏斗事实底座（xsec_profile v2）】
L1 主假设: {pr.get('name', '?')}（{pr.get('next_verification', '')}）
L2 事件佐证: {fu.get('layer2_state', '?')} | {couples}
L3 资金佐证: {fu.get('layer3_fund_state', '?')}
L4 定性: {fu.get('verdict', '?')}
K线: 收{p['line2_kline']['close']}元({p['line2_kline']['pct_chg']:+.1f}%) 审美={p['line2_kline']['aesthetic']} {'; '.join(p['line2_kline']['signals'])}
席位: {p['line3_seats']['verdict']} | 游资{p['line3_seats']['hotmoney_seat_count']}席 | 拉萨{p['line3_seats']['lasa_retail_count']}
数据血统: K线=机器级 | 公告=法定级 | 席位=法定级(冰山一角)"""


def run(code: str, save: bool = True) -> dict:
    CKPT_DIR.mkdir(exist_ok=True)
    ckpt_file = CKPT_DIR / f"debate_{code}.json"
    ckpt = {}
    if ckpt_file.exists():
        try:
            ckpt = json.loads(ckpt_file.read_text(encoding="utf-8"))
        except Exception:
            ckpt = {}

    def A(aid: str, ctx: str = "", tag: str = None) -> str:
        tag = tag or aid
        persona = load_persona(aid)
        return chat(f"{persona}\n\n{FACTS}\n{ctx}\n\n按你的人设与 LCS-1 纪律输出：", tag, ckpt)

    # 0) 事实底座
    p = xsec_profile(code)
    FACTS = funnel_digest(p)

    # 1) T1⇄T2 假设攻防（单轮即止：LCS-1 下攻防以漏斗字段为锚，减少漂移）
    t1 = A("T1")
    t2 = A("T2", f"\n【T1 攻方论点】{t1[:400]}\n针对其引用字段反驳：", "T2")
    # 2) 佐证席
    n1 = A("N1")
    f1 = A("F1")
    # 3) 裁决（存疑铁律在人设里）
    d1 = A("D1", f"\n【T1】{t1[:300]}\n【T2】{t2[:300]}\n【N1】{n1[:150]}\n【F1】{f1[:150]}", "D1")
    # 4) 风控辩论
    r1 = A("R1", f"\n【D1裁决】{d1[:200]}")
    r2 = A("R2", f"\n【D1裁决】{d1[:200]}\n【激进席】{r1[:250]}\n针对其参数反驳：", "R2")
    r3 = A("R3", f"\n【激进席】{r1[:250]}\n【保守席】{r2[:250]}", "R3")
    # 5) 执行单
    tr1 = A("TR1", f"\n【D1】{d1[:180]}\n【R3】{r3[:220]}", "TR1")

    result = {"code": code, "ts": time.strftime("%Y-%m-%d %H:%M"), "model": MODEL_CHAIN[0],
              "facts": FACTS, "acts": {"T1": t1, "T2": t2, "N1": n1, "F1": f1, "D1": d1,
                                        "R1": r1, "R2": r2, "R3": r3, "TR1": tr1},
              "funnel": p.get("funnel", {})}
    if save:
        ckpt_file.write_text(json.dumps(ckpt, ensure_ascii=False), encoding="utf-8")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"legion_{code}_{time.strftime('%Y%m%d_%H%M')}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    from legion_pulse import heartbeat
    heartbeat("LEGION_DEBATE", "run", f"{code} 全链分析完成", {"verdict": result.get('funnel', {}).get('verdict', '?')})
    return result


def render(r: dict) -> str:
    fu = r["funnel"]
    pr = fu.get("primary_hypothesis", {})
    a = r["acts"]
    def brief(s, n=110):
        s = s.strip()
        return s[:n] + ("…" if len(s) > n else "")
    return f"""═══ 军团分析 AI · LCS-1 报告 {r['code']} ═══  {r['ts']} ({r['model']})
[漏斗] L1={pr.get('name','?')} | L2={fu.get('layer2_state','?')} | L3={fu.get('layer3_fund_state','?')} | L4={fu.get('verdict','?')}
[T1 攻] {brief(a['T1'])}
[T2 防] {brief(a['T2'])}
[N1 佐] {brief(a['N1'], 70)}   [F1 佐] {brief(a['F1'], 70)}
[D1 裁] {brief(a['D1'])}
[R1 左] {brief(a['R1'], 90)}   [R2 右] {brief(a['R2'], 90)}
[R3 仲] {brief(a['R3'])}
[TR1 单] {brief(a['TR1'])}
存档: reports/legion/"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="军团分析 AI（LCS-1 全链）")
    ap.add_argument("code")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = run(args.code)
    print(json.dumps(res, ensure_ascii=False, indent=2) if args.json else render(res))
