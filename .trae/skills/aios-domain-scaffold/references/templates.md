# 代码模板速查（按七步顺序）

## 1. config.json 骨架

```json
{
  "model": "qwen3:8b-8k",
  "material_hours": 24,
  "material_limit": 80,
  "push": true
}
```

域专属参数（watchlist、voice、resolution 等）平铺追加，脚本内 `json.load(open(CFG_PATH, encoding="utf-8"))` 每次运行重读。

## 2. LLM 调用（Ollama，关闭思考）

```python
import requests, re

OLLAMA = "http://127.0.0.1:11434/api/chat"
SYS = ("你是一名……。规则：①只依据给定素材，禁止编造数据、引语或事件；"
       "②……风格要求……；③全部简体中文；④严格按用户要求的格式输出。")

def llm(user, num_predict=1200, temperature=0.6):
    r = requests.post(OLLAMA, json={
        "model": CFG["model"],
        "messages": [{"role": "system", "content": SYS},
                     {"role": "user", "content": user}],
        "stream": False, "think": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }, timeout=600)
    r.raise_for_status()
    return r.json()["message"]["content"].strip()

# JSON 兜底抽取 + 硬校验
text = llm(prompt)
m = re.search(r"\{.*\}", text, re.S)
obj = json.loads(m.group(0))
if not obj.get("scenes") or not obj.get("title"):
    raise ValueError("结构不完整: " + text[:200])
```

注意：LLM 常不守分隔符格式，行解析要兼容 4/5 段（见 gzh/gzh_writer.py 的 parse_topics）。

## 3. 飞书推送（唯一正确方式）

```python
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))

def push_message(text):
    tmp = os.path.join(NEWS_DIR, "outbox", "pending_<域>.txt")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    r = subprocess.run([sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    return (r.stdout or "").strip().startswith("SEND_OK")

# 卡片模式：追加第三参 "interactive"，文件内容为卡片 JSON（header/body 结构
# 参考 backup_alert.py / quant_backtest/runner.py 的 build_card；卡片失败记得降级纯文本）。
# 卡片 img（如二维码）三个坑（均为 ErrCode 200621，实测 2026-09）：
#   1. 顶层不要带 "schema": "2.0" 键——带 schema+img 会被严格校验拒绝，去掉走兼容模式即可发；
#   2. img 不能裸放在 body.elements 下，必须包在 column_set > column > elements 里；
#   3. img 所在 column_set 之后最好再跟一个元素（markdown/hr），避免作为末元素；
#   二维码图片复用 quant_backtest/card_assets.py 的 ensure_qr_image_key(portal)（qr_cache.json 缓存）。
# 卡片骨架（ErrCode 200410 教训，实测 2026-09）：必须用 config+header+body.elements
#   三键骨架（update_multi/width_mode 同 runner.build_card）；按钮直接放
#   column_set>column>elements 里（tag:button + width:fill + size:medium），
#   不要用 action 包裹、不要把 elements 挂顶层（旧 1.0 裸结构会 200410）。
```

## 4. ASCII 启动器 `<域>/start_<域>.ps1`

```powershell
# <域> launcher (ASCII only, junction path)
$log = 'C:\<域>_task.log'
try {
    $p = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList 'C:\aios\<域>\<主脚本>.py' `
        -RedirectStandardOutput 'C:\<域>_out.log' `
        -RedirectStandardError 'C:\<域>_err.log' `
        -PassThru -WindowStyle Hidden
    Wait-Process -Id $p.Id -ErrorAction SilentlyContinue
    ('<域> exit=' + $p.ExitCode + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    exit 0
} catch {
    ('<域> ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
```

## 5. 计划任务注册（XML 导出改写，保 principal 与时区）

```powershell
$xml = (schtasks /query /tn '金融新闻AI晨报' /xml) -join "`n"   # 任何已验证任务作母版
$xml = $xml -replace '<Command>.*?</Command>', '<Command>powershell.exe</Command>'
$xml = $xml -replace '<Arguments>.*?</Arguments>', '<Arguments>-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\aios\<域>\start_<域>.ps1"</Arguments>'
# 北京时刻 T → 边界 (T-6h)+02:00；09:30 北京 = 03:30+02:00
$xml = $xml -replace '<StartBoundary>.*?</StartBoundary>', '<StartBoundary>2026-09-26T03:30:00+02:00</StartBoundary>'
$xml | Out-File 'C:\Temp\<域>_task.xml' -Encoding unicode
Register-ScheduledTask -TaskName '<域名>' -Xml (Get-Content 'C:\Temp\<域>_task.xml' -Raw) -Force | Out-Null
# 验证：NextRunTime(巴黎) + 6h（夏令时）= 预期北京时刻；Start-ScheduledTask 后 LastTaskResult=0
```

重复间隔类任务另需 Repetition 节点，参考母版 `金融新闻采集` 的 XML。

## 6. 门户接入

portal_api.py：

```python
def get_<域>():
    try:
        with open(os.path.join(STATION, "<域>", "output", "index.json"),
                  "r", encoding="utf-8") as f:
            items = json.load(f)
        return {"items": items[:8]}
    except Exception:
        return {"items": []}
```

- `TASKS` 列表追加任务名（与计划任务名完全一致）
- overview dict 加 `"<域>": get_<域>()`
- 改完杀 8091 进程，`Start-ScheduledTask 'AI工作站门户API'`，5min 自愈兜底
- 验证：`(Invoke-WebRequest http://127.0.0.1:8091/api/overview).Content` 含新字段

index.html（三处）：

```javascript
// 1) TASK_SHORT 映射加短名
'公众号选题撰文':'公众号 09:30',
// 2) 面板 div（panel 网格内）
// 3) loadConsole 渲染段：空数据显示"待首发"黄徽标，非红叉
```

## 7. 已有任务时刻表（北京时刻，注册时核对用）

| 任务名 | 北京时刻 | 模式 |
|---|---|---|
| 金融新闻采集 | 全天 15min 重复 | 重复 |
| 金融新闻AI晨报 | 每日 08:00 | 单次 |
| 金融新闻日报 | 每日 08:30 | 单次 |
| 金融新闻收盘复盘 | 周一至五 15:30 | 单次 |
| 自选股行情快照 | 周一至五 09:25-15:05 每 5min | 重复 |
| 公众号选题撰文 | 每日 09:30 | 单次 |
| 金融量化每日扫描 | 每日 16:00 | 单次（runner.py --daily 读 config daily_scan） |
| AI工作站门户API | 常驻 | 5min 自愈 |
| ZhiceWatchdog / 备份 / 告警转发 | 见 system-task-deploy | — |

## 常见坑速查

| 症状 | 原因与解法 |
|---|---|
| SYSTEM 任务秒退、LastResult=0 假成功 | 路径含中文 → 全程走 `C:\aios` junction |
| 任务 Action 返回 4294967295 | 用了 `-Command "& ..."` → 必须 `-File` 启动器 |
| 任务时刻差 6/7 小时 | 系统时区巴黎；边界必须写绝对偏移 `+02:00`（=北京-6h） |
| 沙箱 pip/安装报 hit restricted | 改 `pip --target <域>/pylibs`；软件静态版进 `tools/` |
| 沙箱经 junction 写库静默丢失 | 沙箱测试用 `E:\` 直路径；建表保持幂等（CREATE TABLE IF NOT EXISTS） |
| ffmpeg filter 报 Stray % | drawtext 加 `expansion=none`；textfile 用相对名 + `cwd=WORK` |
| pyttsx3 只合成出一段 | 引擎不可复用，每段独立子进程（见 video/tts_one.py） |
| PS 里 stderr 渲染成红 CLIXML | 内容正常即忽略，只看退出码与产物 |
| 中文按字符切行末字落单 | 按显示宽度换行（中文 2 单位，见 pipeline.wrap_units） |
