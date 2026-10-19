"""命令行多轮对话。"""
import json, os, sys, tempfile
from ollama_client import OllamaClient, OllamaError
from model_routing import DEFAULT_CHAT_MODEL, ModelRoutingError, select_model
DEFAULT_MODEL = DEFAULT_CHAT_MODEL
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")
MAX_HISTORY_MESSAGES = 40
MAX_MESSAGE_CHARS = 12000
VALID_ROLES = {"user", "assistant"}
HELP = "/model <名称> 切换模型\n/clear 清空历史\n/history 查看条数\n/help 显示帮助\n/exit 退出"
def _clean_history(history):
    if not isinstance(history, list):
        return []
    cleaned = []
    for item in history:
        if not isinstance(item, dict) or item.get("role") not in VALID_ROLES:
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content:
            continue
        cleaned.append({"role": item["role"], "content": content[:MAX_MESSAGE_CHARS]})
    return cleaned[-MAX_HISTORY_MESSAGES:]
def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return _clean_history(json.load(f))
    except (OSError, json.JSONDecodeError):
        return []
def save_history(history):
    parent = os.path.dirname(os.path.abspath(HISTORY_FILE))
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=os.path.basename(HISTORY_FILE) + ".", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_clean_history(history), f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, HISTORY_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
def main():
    client, model, history = OllamaClient(), DEFAULT_MODEL, load_history()
    try: models = client.list_models()
    except OllamaError as e: print(f"[错误] {e}"); return
    try:
        selection = select_model("chat", model, installed=models)
        model = selection.model
        if selection.message:
            print(f"[提示] {selection.message}")
    except ModelRoutingError as e:
        print(f"[错误] {e}")
        return
    print(f"当前模型: {model}（/help 查看命令）")
    while True:
        try: text = input("你: ").strip()
        except (EOFError, KeyboardInterrupt): print("\n再见！"); return
        if not text: continue
        if text == "/exit": print("再见！"); return
        if text == "/help": print(HELP); continue
        if text == "/clear": history.clear(); save_history(history); print("[已清空]"); continue
        if text == "/history": print(f"[已保存 {len(history)} 条记录]"); continue
        if text == "/model" or text.startswith("/model "):
            arg = text[6:].strip()
            if arg:
                try:
                    selection = select_model("chat", arg, installed=client.list_models())
                except ModelRoutingError as e:
                    print(f"[错误] {e}")
                    continue
                model = selection.model
                if selection.message:
                    print(f"[提示] {selection.message}")
                print(f"[已切换模型: {model}]")
            else:
                try: print("可用模型: " + ", ".join(client.list_models()))
                except OllamaError as e: print(f"[错误] {e}")
            continue
        history.append({"role":"user","content":text})
        try:
            reply = client.chat(history, model); print("AI: " + reply); history.append({"role":"assistant","content":reply}); save_history(history)
        except OllamaError as e: history.pop(); print(f"[错误] {e}")
if __name__ == "__main__": main()
