"""命令行多轮对话。"""
import json, os, sys
from ollama_client import OllamaClient, OllamaError
DEFAULT_MODEL = "qwen3.5:35b"
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")
HELP = "/model <名称> 切换模型\n/clear 清空历史\n/history 查看条数\n/help 显示帮助\n/exit 退出"
def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f: data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError): return []
def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f: json.dump(history, f, ensure_ascii=False, indent=2)
def main():
    client, model, history = OllamaClient(), DEFAULT_MODEL, load_history()
    try: models = client.list_models()
    except OllamaError as e: print(f"[错误] {e}"); return
    if models and model not in models: model = models[0]
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
            if arg: model = arg; print(f"[已切换模型: {model}]")
            else:
                try: print("可用模型: " + ", ".join(client.list_models()))
                except OllamaError as e: print(f"[错误] {e}")
            continue
        history.append({"role":"user","content":text})
        try:
            reply = client.chat(history, model); print("AI: " + reply); history.append({"role":"assistant","content":reply}); save_history(history)
        except OllamaError as e: history.pop(); print(f"[错误] {e}")
if __name__ == "__main__": main()
