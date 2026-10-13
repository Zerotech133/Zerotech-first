# -*- coding: utf-8 -*-
"""
K-ORG-036g: 首个真实编码工单全自动链（F1 降级预案·纯 API 通道）
  生成(chat_auto/cloud) → repo_map 注入 → 编辑协议 → lint 门 → 冒烟 → 按单提交 → 轨迹
  工单: model_route 绝对路径重写（load_route_config 相对路径在非仓库根 CWD 下炸）
  失败自动还原备份（禁把坏文件留在工作区）。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import agent_coder as ac
import repo_map

TARGET = os.path.join("tools", "model_route.py")
KID = "K-ORG-036g"

# 1) 备份（失败回退用）
bak = os.path.join(tempfile.gettempdir(), "model_route.py.bak_036g")
shutil.copy2(TARGET, bak)
print("[backup]", bak)

# 2) 仓库地图注入（F2 骨架）
rmap = repo_map.build_map(keywords=["model_route", "route", "config"])
print("[repo-map]", len(rmap), "chars")

# 3) 工单 prompt
prompt = """【工单 %s】重写 tools/model_route.py：配置路径绝对化。

现状缺陷：load_route_config 的 DEFAULT_PATH = "config/ollama_model_route.json"
是相对路径，仅当 CWD=仓库根时可用；从其他目录导入调用会 FileNotFoundError。

要求：
1. DEFAULT_PATH 改为基于本文件位置推导的绝对路径：
   os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config", "ollama_model_route.json")
2. 三个函数 load_route_config / select_model / fallback_for 的对外行为与签名保持不变。
3. 只改这一个文件，且【必须用 SEARCH/REPLACE 块】做最小修改（禁止整文件重写）：
   块 1：在 "import json" 后补一行 "import os"（SEARCH 用现有 import 行）。
   块 2：把 DEFAULT_PATH 三行赋值换成基于本文件位置的绝对路径推导：
          os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "config", "ollama_model_route.json")
   三个函数的实现体一字不动（SEARCH 原文必须逐字符照抄）。
4. 格式：
<<<<<<< SEARCH
（目标文件中原样存在的文本，必须唯一命中）
=======
（替换后的文本）
>>>>>>> REPLACE
5. 【禁止占位】任何函数体不得出现 pass / ... / NotImplemented 占位。

相关仓库地图（供上下文参考）：
%s
""" % (KID, rmap)

# 4) 冒烟钩子：从陌生 CWD 验绝对路径 + 三函数行为回归
def smoke(path):
    bad = os.path.join(tempfile.gettempdir(), "model_route.bad_036g.py")
    shutil.copy2(path, bad)                      # 入口无条件留样（可检视）
    print("[sample saved]", bad)
    src = open(path, encoding="utf-8").read()
    for fn in ("def load_route_config", "def select_model", "def fallback_for"):
        assert fn in src, "完整性缺 %s" % fn
    assert os.path.abspath(path) == os.path.abspath(TARGET)
    tools_dir = os.path.abspath("tools")
    old_cwd = os.getcwd()
    try:
        os.chdir(tempfile.gettempdir())          # 陌生 CWD
        sys.path.remove(tools_dir) if tools_dir in sys.path else None
        sys.path.insert(0, tools_dir)
        for m in [k for k in list(sys.modules) if k == "model_route"]:
            del sys.modules[m]
        import model_route as mr
        cfg = mr.load_route_config()             # 相对路径版在这里必炸
        assert mr.select_model("embedding", cfg) == "bge-m3", "embed 路由错"
        assert mr.select_model("no_such", cfg) == cfg["default_model"], "回落错"
        fb = mr.fallback_for("主模型", cfg)
        assert isinstance(fb, list) and fb, "fallback 链空: %r" % (fb,)
    except Exception:
        print("[bad-output at]", bad)
        raise
    finally:
        os.chdir(old_cwd)
        for m in [k for k in list(sys.modules) if k == "model_route"]:
            del sys.modules[m]
    print("[smoke] 绝对路径+行为回归 PASS")

# 5) 全自动链：生成→协议→lint门→落盘→冒烟
ok, detail = ac.run(
    label="model_route绝对路径重写", agent_id="DEV2", out_path=TARGET,
    prompt=prompt, smoke=smoke, k_id=KID)
if not ok:
    shutil.copy2(bak, TARGET)                    # 还原，坏产出不留工作区
    print("[RESTORED from backup]")
    sys.exit("FULL-CHAIN FAIL: " + detail)

# 6) 按单提交（F4 骨架·真提交·轨迹自动入账）
cok, cdet = ac.commit_work(TARGET, KID, "DEV2",
                           "model_route绝对路径重写", channel="cloud")
print("[commit]", cok, cdet)
print("FULL-CHAIN", "PASS" if cok else "COMMIT-FAIL", "-", detail)
