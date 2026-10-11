import json
import os
import sys

from ollama_client import OllamaClient, OllamaError

DEFAULT_MODEL = "qwen3.5:35b"
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")

HELP = """
命令列表:
  /model <名称>   切换模型（不带参数则列出可用模型）
  /clear          清空对话历史（含本地文件）
  /history        查看已保存的对话条数
  /help           显示本帮助
  /exit           退出
"""


def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    client = OllamaClient()
    model = DEFAULT_MODEL

    try:
        models = client.list_models()
    except OllamaError as e:
        print(f"[错误] {e}")
        return
    if models:
        print(f"已连接 Ollama，可用模型: {', '.join(models)}")
    if model not in models and models:
        model = models[0]
        print(f"默认模型不可用，已切换为: {model}")

    history = load_history()
    if history:
        print(f"已加载 {len(history)} 条历史记录（/clear 清空，/history 查看）")
    print(f"当前模型: {model}，输入内容开始对话（/help 查看命令）\n")

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            return

        if not user_input:
            continue
        if user_input == "/exit":
            print("再见！")
            return
        if user_input == "/help":
            print(HELP)
            continue
        if user_input == "/clear":
            history.clear()
            save_history(history)
            print("[已清空对话历史（含本地文件）]\n")
            continue
        if user_input == "/history":
            print(f"[已保存 {len(history)} 条记录于 {HISTORY_FILE}]\n")
            continue
        if user_input == "/model" or user_input.startswith("/model "):
            arg = user_input[7:].strip()
            if arg:
                model = arg
                print(f"[已切换模型: {model}]\n")
            else:
                try:
                    print(f"可用模型: {', '.join(client.list_models())}\n")
                except OllamaError as e:
                    print(f"[错误] {e}\n")
            continue

        history.append({"role": "user", "content": user_input})
        print("AI: ", end="", flush=True)
        try:
            reply = client.chat_stream(history, model)
            full = []
            for piece in reply:
                print(piece, end="", flush=True)
                full.append(piece)
            print("\n")
            history.append({"role": "assistant", "content": "".join(full)})
            save_history(history)
        except OllamaError as e:
            print(f"\n[错误] {e}\n")
            history.pop()


if __name__ == "__main__":
    main()
