"""独立的聊天与知识库问答网页渲染器。"""
from __future__ import annotations

from html import escape
from typing import Sequence


def _model_options(models: Sequence[str]) -> str:
    """生成经过 HTML 转义的模型选项，避免模型名影响页面结构。"""
    return "".join(
        '<option value="{value}">{label}</option>'.format(
            value=escape(str(model), quote=True),
            label=escape(str(model)),
        )
        for model in models
    )


def render_chat_page(models: Sequence[str]) -> str:
    """渲染聊天页面，不启动服务，也不依赖 RAG 后端。"""
    options = _model_options(models)
    return """<!doctype html>
<meta charset="utf-8">
<title>AI 聊天</title>
<h1>AI 聊天</h1>
<label>模型 <select id="model">{options}</select></label>
<label>模式 <select id="mode">
  <option value="chat">普通聊天</option>
  <option value="rag">知识库问答</option>
</select></label>
<br>
<textarea id="message" placeholder="输入消息"></textarea>
<button id="send">发送</button>
<pre id="out"></pre>
<pre id="sources"></pre>
<script>
const send = document.getElementById("send");
const model = document.getElementById("model");
const mode = document.getElementById("mode");
const message = document.getElementById("message");
const out = document.getElementById("out");
const sources = document.getElementById("sources");

function renderSources(items) {
  sources.textContent = "";
  if (!Array.isArray(items) || items.length === 0) return;
  sources.textContent = "来源：\\n" + items.map((item) => {
    const name = item && item.source ? String(item.source) : "未知来源";
    const score = item && item.score !== undefined ? `（分数：${item.score}）` : "";
    return `- ${name}${score}`;
  }).join("\\n");
}

send.onclick = async () => {
  const text = message.value.trim();
  if (!text) {
    out.textContent = "请输入消息";
    return;
  }
  out.textContent = "思考中...";
  sources.textContent = "";
  send.disabled = true;
  try {
    const endpoint = mode.value === "rag" ? "/api/rag" : "/api/chat";
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({model: model.value, message: text})
    });
    if (!response.ok) {
      out.textContent = await response.text();
      return;
    }
    out.textContent = "";
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, {stream: true});
      const lines = buffer.split("\\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const record = JSON.parse(line);
          if (record.message && typeof record.message.content === "string") {
            out.textContent += record.message.content;
          }
          if (typeof record.response === "string") out.textContent += record.response;
          if (record.sources) renderSources(record.sources);
          if (record.done) renderSources(record.sources || []);
        } catch (_) {
          // 忽略不完整或无效的流记录，继续处理后续内容。
        }
      }
    }
    if (buffer.trim()) {
      try {
        const record = JSON.parse(buffer);
        if (record.message && typeof record.message.content === "string") {
          out.textContent += record.message.content;
        }
        if (typeof record.response === "string") out.textContent += record.response;
        if (record.sources) renderSources(record.sources);
      } catch (_) {
        // 服务端应返回 NDJSON；末尾残片无需阻断已显示的答案。
      }
    }
  } catch (_) {
    out.textContent = "无法连接 Ollama，请确认服务已启动。";
  } finally {
    send.disabled = false;
  }
};
</script>""".replace("{options}", options)


__all__ = ["render_chat_page"]
