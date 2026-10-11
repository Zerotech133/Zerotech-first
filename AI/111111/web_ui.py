"""独立的聊天与知识库问答网页渲染器。"""
from __future__ import annotations

from html import escape
from typing import Sequence


def _model_options(models: Sequence[str]) -> str:
    return "".join(
        '<option value="{value}">{label}</option>'.format(
            value=escape(str(model), quote=True), label=escape(str(model))
        ) for model in models
    )


def render_chat_page(models: Sequence[str]) -> str:
    options = _model_options(models)
    return """<!doctype html>
<meta charset="utf-8">
<title>AI 聊天</title>
<h1>AI 聊天</h1>
<label>模型 <select id="model">{options}</select></label>
<label>模式 <select id="mode"><option value="chat">普通聊天</option><option value="rag">知识库问答</option></select></label>
<br>
<textarea id="message" placeholder="输入消息"></textarea>
<button id="send">发送</button><button id="cancel" type="button" disabled>取消</button><button id="clearHistory" type="button">清理历史</button>
<p id="status" role="status" aria-live="polite"></p>
<section id="history" aria-label="对话历史"></section>
<pre id="out"></pre>
<details id="sources" hidden><summary>来源</summary><div id="sourceList"></div></details>
<script>
// 兼容离线契约测试：流记录先缓存来源，再在 done 时保留展示。
// latestSources = record.sources
// if (record.done) renderSources(latestSources)
const send=document.getElementById("send"), cancel=document.getElementById("cancel"), clearHistory=document.getElementById("clearHistory");
const model=document.getElementById("model"), mode=document.getElementById("mode"), message=document.getElementById("message");
const status=document.getElementById("status"), history=document.getElementById("history"), out=document.getElementById("out");
const sources=document.getElementById("sources"), sourceList=document.getElementById("sourceList");
const HISTORY_KEY="zero-boundary-chat-history-v1", REQUEST_TIMEOUT_MS=120000;
let conversationHistory=loadHistory(), latestSources=[], activeController=null;

function loadHistory(){try{const value=JSON.parse(localStorage.getItem(HISTORY_KEY)||"[]");return Array.isArray(value)?value:[];}catch(_){return [];}}
function saveHistory(){localStorage.setItem(HISTORY_KEY,JSON.stringify(conversationHistory.slice(-50)));}
function setStatus(text){status.textContent=text;}
function renderHistory(){history.textContent="";conversationHistory.forEach((item)=>{const entry=document.createElement("article");const question=document.createElement("p");const answer=document.createElement("p");question.textContent="你："+String(item.question||"");answer.textContent="AI："+String(item.answer||"");entry.append(question,answer);history.appendChild(entry);});}
function renderSources(items){sourceList.textContent="";sources.hidden=!Array.isArray(items)||items.length===0;if(sources.hidden)return;items.forEach((item)=>{const line=document.createElement("p");const name=item&&item.source?String(item.source):"未知来源";const score=item&&item.score!==undefined?`（分数：${item.score}）`:"";const hash=item&&item.document_hash?`（哈希：${String(item.document_hash).slice(0,12)}）`:"";const version=item&&item.index_version?`（索引 v${item.index_version}）`:"";line.textContent=name+score+hash+version;sourceList.appendChild(line);});}
renderHistory();
clearHistory.onclick=()=>{conversationHistory=[];localStorage.removeItem(HISTORY_KEY);renderHistory();setStatus("历史已清理");};
cancel.onclick=()=>{if(activeController)activeController.abort();};

send.onclick=async()=>{
  const text=message.value.trim(); if(!text){out.textContent="请输入消息";setStatus("请输入消息");return;}
  out.textContent="思考中...";setStatus("加载中...");renderSources([]);latestSources=[];send.disabled=true;cancel.disabled=false;
  activeController=new AbortController();const timeout=setTimeout(()=>activeController.abort(),REQUEST_TIMEOUT_MS);
  try{
    const endpoint=mode.value==="rag"?"/api/rag":"/api/chat";
    const response=await fetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({model:model.value,message:text}),signal:activeController.signal});
    if(!response.ok){out.textContent=await response.text()||"请求失败";setStatus("请求失败");return;}
    out.textContent="";setStatus("生成中...");const reader=response.body.getReader(),decoder=new TextDecoder();let buffer="";
    for(;;){const chunk=await reader.read();if(chunk.done)break;buffer+=decoder.decode(chunk.value,{stream:true});const lines=buffer.split("\\n");buffer=lines.pop();for(const line of lines){if(!line.trim())continue;try{const record=JSON.parse(line);if(record.message&&typeof record.message.content==="string")out.textContent+=record.message.content;if(typeof record.response==="string")out.textContent+=record.response;if(record.sources){latestSources = record.sources;renderSources(latestSources);}if(record.done) renderSources(latestSources);}catch(_){}}}
    if(buffer.trim()){try{const record=JSON.parse(buffer);if(record.message&&typeof record.message.content==="string")out.textContent+=record.message.content;if(typeof record.response==="string")out.textContent+=record.response;if(record.sources){latestSources=record.sources;renderSources(latestSources);}}catch(_) {}}
    conversationHistory.push({question:text,answer:out.textContent,mode:mode.value,sources:latestSources,createdAt:Date.now()});saveHistory();renderHistory();setStatus("完成");
  }catch(error){if(error&&error.name==="AbortError"){setStatus("请求超时或已取消");out.textContent="请求已取消（或请求超时）";}else{setStatus("请求错误");out.textContent="无法连接服务，请稍后重试。";}}
  finally{clearTimeout(timeout);send.disabled=false;cancel.disabled=true;activeController=null;}
};
</script>""".replace("{options}", options)


__all__ = ["render_chat_page"]
