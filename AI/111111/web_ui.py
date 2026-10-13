"""独立的聊天与知识库问答网页渲染器。"""
from __future__ import annotations

from html import escape
from typing import Sequence

try:
    from .model_routing import CHAT_CONTEXT_CHAR_BUDGET, DEFAULT_CHAT_MODEL
except ImportError:
    from model_routing import CHAT_CONTEXT_CHAR_BUDGET, DEFAULT_CHAT_MODEL


def _model_options(models: Sequence[str], selected: str = DEFAULT_CHAT_MODEL) -> str:
    selected = selected if selected in models else (str(models[0]) if models else selected)
    return "".join(
        '<option value="{value}"{selected}>{label}</option>'.format(
            value=escape(str(model), quote=True), label=escape(_model_label(str(model)))
            , selected=" selected" if str(model) == selected else ""
        ) for model in models
    )


def _model_label(model: str) -> str:
    if model.startswith("zhipu/"):
        return "智谱 · " + model.split("/", 1)[1]
    return model


def _profile_options(profiles: Sequence[object]) -> str:
    selected = tuple(profiles) or ("general",)
    options = []
    for profile in selected:
        if isinstance(profile, dict):
            profile_id = profile.get("id", "general")
            name = profile.get("name", profile_id)
            model = profile.get("model")
            label = f"{name}（{model}）" if model else name
        else:
            profile_id = name = str(profile)
            label = name
        options.append(
            '<option value="{value}">{label}</option>'.format(
                value=escape(str(profile_id), quote=True), label=escape(str(label))
            )
        )
    return "".join(options)


def render_chat_page(models: Sequence[str], profiles: Sequence[str] = (), default_model: str = DEFAULT_CHAT_MODEL) -> str:
    options = _model_options(models, default_model)
    profile_options = _profile_options(profiles)
    return """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>零界 · AI 聊天</title>
<link rel="stylesheet" href="/static/tokens.css">
<script src="/static/nav.js" defer></script>
<script src="/static/shortscreen.js" defer></script>
<style>
:root{color-scheme:dark}
body{margin:0;background:var(--bg-page);color:var(--text);font-family:var(--font-ui);font-size:var(--fs-m);line-height:1.55}
button,select,textarea{font:inherit}
button,select,textarea{color:var(--text);background:var(--bg-raised);border:1px solid var(--border);border-radius:var(--r-s)}
button,select{min-height:36px;padding:0 var(--sp-3)}
button{cursor:pointer}
button:hover:not(:disabled),select:hover{border-color:var(--link)}
button:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid var(--link);outline-offset:2px}
button:disabled{cursor:not-allowed;opacity:.55}
.ai-page-shell{max-width:var(--page-w);margin:0 auto;padding:var(--sp-5) var(--sp-4) 40px}
.panel{background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--r-l);box-shadow:0 10px 26px rgba(0,0,0,.18)}
.ai-header{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--sp-5);padding:var(--sp-5);margin-bottom:var(--sp-4)}
.eyebrow{display:block;color:var(--color-warn);font-size:var(--fs-xs);font-weight:600;letter-spacing:.08em;margin-bottom:var(--sp-2)}
h1,h2,h3{margin:0;color:var(--text);line-height:1.25}
h1{font-size:var(--fs-xl);letter-spacing:-.02em}
h2{font-size:var(--fs-l)}
.hero-subtitle,.muted{color:var(--text-dim);margin:var(--sp-2) 0 0}
.header-status{display:flex;align-items:flex-end;flex-direction:column;gap:2px;color:var(--color-ok);font-size:var(--fs-s);white-space:nowrap}
.header-status small{color:var(--text-dim);font-family:var(--font-mono);font-size:var(--fs-xs)}
.status-dot{width:8px;height:8px;border-radius:50%;background:var(--color-ok);box-shadow:0 0 0 4px rgba(63,185,80,.12);margin-bottom:2px}
.chat-grid{display:grid;grid-template-columns:280px minmax(0,1fr);gap:var(--sp-4);align-items:start}
.context-panel,.chat-panel{min-width:0;padding:var(--sp-4)}
.context-panel{position:sticky;top:58px}
.panel-heading,.chat-panel-header,.composer-actions,.composer-meta{display:flex;align-items:center;justify-content:space-between;gap:var(--sp-2)}
.panel-heading{margin-bottom:var(--sp-3)}
.status-chip{color:var(--color-ok);border:1px solid var(--color-ok);border-radius:var(--r-pill);padding:2px 8px;font-size:var(--fs-xs);font-family:var(--font-mono)}
.control-group{display:grid;gap:6px;margin-top:var(--sp-4)}
.control-group label{color:var(--text-dim);font-size:var(--fs-s)}
.control-group select{width:100%;box-sizing:border-box}
.switch-row{display:flex;align-items:center;gap:var(--sp-2);color:var(--text-dim);font-size:var(--fs-s);margin-top:var(--sp-3)}
.switch-row input{accent-color:var(--accent)}
.notice{color:var(--text-dim);font-size:var(--fs-xs);margin:var(--sp-2) 0 0}
.runtime-card{margin-top:var(--sp-5);padding-top:var(--sp-4);border-top:1px solid var(--border-weak)}
.runtime-card h2{font-size:var(--fs-m);margin-bottom:var(--sp-2)}
.runtime-card dl{display:grid;grid-template-columns:1fr auto;gap:6px 12px;margin:0;font-size:var(--fs-s)}
.runtime-card dt{color:var(--text-dim)}
.runtime-card dd{margin:0;color:var(--text)}
.chat-panel{display:flex;flex-direction:column;min-height:620px}
.chat-panel-header{padding-bottom:var(--sp-3);border-bottom:1px solid var(--border-weak)}
.chat-panel-header p{margin:2px 0 0;color:var(--text-dim);font-size:var(--fs-s)}
#status{color:var(--text-dim);font-size:var(--fs-s);margin:var(--sp-3) 0}
#history{display:grid;gap:var(--sp-3);padding:var(--sp-4) 0;flex:1}
#history:empty:before{content:"从一个问题开始，回答会在这里流式出现。";color:var(--text-dim);padding:var(--sp-5) 0}
#history article{padding:var(--sp-3) var(--sp-4);border:1px solid var(--border-weak);border-radius:var(--r-m);background:var(--bg-raised)}
#history article p{margin:0;white-space:pre-wrap}
#history article p+p{margin-top:var(--sp-3);padding-top:var(--sp-3);border-top:1px solid var(--border-weak);color:var(--gray-6)}
#out{min-height:24px;margin:0 0 var(--sp-3);padding:var(--sp-3) var(--sp-4);white-space:pre-wrap;color:var(--text);background:var(--bg-raised);border-left:3px solid var(--accent);border-radius:var(--r-s);font-family:var(--font-ui)}
.composer{border-top:1px solid var(--border-weak);padding-top:var(--sp-4)}
#message{display:block;width:100%;min-height:100px;box-sizing:border-box;resize:vertical;padding:var(--sp-3);line-height:1.6}
.composer-meta{margin-top:var(--sp-2);color:var(--text-dim);font-size:var(--fs-xs)}
.composer-actions{margin-top:var(--sp-3);justify-content:flex-end}
#send{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600;padding:0 var(--sp-4)}
#clearHistory{color:var(--text-dim)}
#sources{margin-bottom:var(--sp-3);color:var(--text-dim);font-size:var(--fs-s)}
#sourceList p{margin:4px 0;color:var(--text)}
@media (max-width:760px){.ai-page-shell{padding:var(--sp-3) var(--sp-2) 28px}.ai-header{padding:var(--sp-4);flex-direction:column}.header-status{align-items:flex-start}.chat-grid{grid-template-columns:1fr}.context-panel{position:static}.chat-panel{min-height:0}}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style>
<main class="ai-page-shell">
<header class="panel ai-header">
<div><span class="eyebrow">⚡ 本地模型 · 对话工作台</span><h1>零界对话台</h1><p class="hero-subtitle">AI 聊天 · 私有上下文 · 可追溯回答</p></div>
<div class="header-status"><span class="status-dot" aria-hidden="true"></span><span>服务在线</span><small>127.0.0.1:8899</small></div>
</header>
<div class="chat-grid">
<aside class="panel context-panel" aria-label="工作上下文">
<div class="panel-heading"><h2>工作上下文</h2><span class="status-chip">LOCAL</span></div>
<p class="muted">选择模型、角色和回答模式，设置会保存在本机浏览器。</p>
<div class="control-group"><label for="model">模型</label><select id="model">{options}</select></div>
<p id="modelNotice" class="notice" role="status" aria-live="polite">当前会话模型：{default_model}</p>
<div class="control-group"><label for="profile">角色</label><select id="profile">{profile_options}</select></div>
<div class="control-group"><label for="mode">模式</label><select id="mode"><option value="chat">普通聊天</option><option value="rag">知识库问答</option></select></div>
<section id="runtimeStatus" class="runtime-card" aria-label="运行状态"><h2>运行状态</h2><dl><div><dt>当前模式</dt><dd id="modeStatus">普通聊天</dd></div><div><dt>索引版本</dt><dd id="indexStatus">—</dd></div><div><dt>文档来源</dt><dd id="sourceStatus">不适用</dd></div></dl></section>
</aside>
<section class="panel chat-panel" aria-label="对话工作区">
<div class="chat-panel-header"><div><h2>对话流</h2><p>回答会逐字出现，历史只保存在本机。</p></div><button id="clearHistory" type="button">清理历史</button></div>
<p id="status" role="status" aria-live="polite"></p>
<section id="history" aria-label="对话历史"></section>
<pre id="out" aria-live="polite"></pre>
<details id="sources" hidden><summary>来源</summary><div id="sourceList"></div></details>
<div class="composer"><label for="message">输入消息</label><textarea id="message" placeholder="问问你的本地 AI 助手"></textarea><div class="composer-meta"><span>上下文预算 {context_budget} 字符</span><label class="switch-row"><input id="think" type="checkbox"> 深度思考</label></div><div class="composer-actions"><button id="cancel" type="button" disabled>取消</button><button id="send" type="button">发送消息</button></div></div>
</section>
</div>
</main>
<script>
// 兼容离线契约测试：流记录先缓存来源，再在 done 时保留展示。
// latestSources = record.sources
// if (record.done) renderSources(latestSources)
const send=document.getElementById("send"), cancel=document.getElementById("cancel"), clearHistory=document.getElementById("clearHistory");
const model=document.getElementById("model"), think=document.getElementById("think"), modelNotice=document.getElementById("modelNotice"), profile=document.getElementById("profile"), mode=document.getElementById("mode"), message=document.getElementById("message");
const status=document.getElementById("status"), history=document.getElementById("history"), out=document.getElementById("out");
const sources=document.getElementById("sources"), sourceList=document.getElementById("sourceList");
const modeStatus=document.getElementById("modeStatus"), indexStatus=document.getElementById("indexStatus"), sourceStatus=document.getElementById("sourceStatus");
const HISTORY_KEY="zero-boundary-chat-history-v1", REQUEST_TIMEOUT_MS=120000, MAX_CONTEXT_CHARS={context_budget};
let conversationHistory=loadHistory(), latestSources=[], activeController=null, sessionModel=model.value;

function loadHistory(){try{const value=JSON.parse(localStorage.getItem(HISTORY_KEY)||"[]");return Array.isArray(value)?value:[];}catch(_){return [];}}
function saveHistory(){localStorage.setItem(HISTORY_KEY,JSON.stringify(conversationHistory.slice(-50)));}
function setStatus(text){status.textContent=text;}
function setRuntimeStatus(state,items){const isRag=mode.value==="rag";modeStatus.textContent=isRag?"知识库问答":"普通聊天";if(!isRag){indexStatus.textContent="—";sourceStatus.textContent="不适用";return;}const list=Array.isArray(items)?items:[];if(state==="loading"){indexStatus.textContent="读取中…";sourceStatus.textContent="等待来源";return;}const versions=[...new Set(list.map((item)=>item&&item.index_version).filter((version)=>version!==undefined&&version!==null))];indexStatus.textContent=versions.length?versions.map((version)=>`v${version}`).join(", "):"—";sourceStatus.textContent=list.length?`已命中 ${list.length} 个来源`:state==="error"?"不可用":state==="done"?"未命中来源":"未加载";}
function renderHistory(){history.textContent="";conversationHistory.forEach((item)=>{const entry=document.createElement("article");const question=document.createElement("p");const answer=document.createElement("p");question.textContent="你："+String(item.question||"");answer.textContent="AI："+String(item.answer||"");entry.append(question,answer);history.appendChild(entry);});}
function historyMessages(){const rows=conversationHistory.flatMap((item)=>{const out=[];if(item&&item.question)out.push({role:"user",content:String(item.question)});if(item&&item.answer)out.push({role:"assistant",content:String(item.answer)});return out;});let total=0,selected=[];for(let i=rows.length-1;i>=0;i--){let row=rows[i],size=row.content.length;if(selected.length&&total+size>MAX_CONTEXT_CHARS)break;if(!selected.length&&size>MAX_CONTEXT_CHARS){row={role:row.role,content:row.content.slice(-MAX_CONTEXT_CHARS)};size=row.content.length;}selected.unshift(row);total+=size;}return selected;}
function renderSources(items){sourceList.textContent="";sources.hidden=!Array.isArray(items)||items.length===0;if(sources.hidden)return;items.forEach((item)=>{const line=document.createElement("p");const name=item&&item.source?String(item.source):"未知来源";const score=item&&item.score!==undefined?`（分数：${item.score}）`:"";const hash=item&&item.document_hash?`（哈希：${String(item.document_hash).slice(0,12)}）`:"";const version=item&&item.index_version?`（索引 v${item.index_version}）`:"";line.textContent=name+score+hash+version;sourceList.appendChild(line);});}
renderHistory();
setRuntimeStatus("idle",latestSources);
mode.onchange=()=>{latestSources=[];renderSources([]);setRuntimeStatus("idle",latestSources);};
model.onchange=()=>{if(model.value!==sessionModel){modelNotice.textContent=`切换到 ${model.value} 将触发冷启动（当前会话：${sessionModel}）`;setStatus("模型已切换，下一条消息将触发冷启动");}else{modelNotice.textContent=`当前会话模型：${sessionModel}`;}};
clearHistory.onclick=()=>{conversationHistory=[];localStorage.removeItem(HISTORY_KEY);renderHistory();setStatus("历史已清理");};
cancel.onclick=()=>{if(activeController)activeController.abort();};

send.onclick=async()=>{
  const text=message.value.trim(); if(!text){out.textContent="请输入消息";setStatus("请输入消息");return;}
  out.textContent="思考中...";setStatus("加载中...");renderSources([]);latestSources=[];setRuntimeStatus("loading",latestSources);send.disabled=true;cancel.disabled=false;
  activeController=new AbortController();const timeout=setTimeout(()=>activeController.abort(),REQUEST_TIMEOUT_MS);
  try{
    const endpoint=mode.value==="rag"?"/api/rag":"/api/chat";
    const requestModel=model.value;
    const requestBody=mode.value==="rag"?{message: text, profile: profile.value}:{model: requestModel, message: text, profile: profile.value, history: historyMessages(), think: think.checked};
    const response=await fetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(requestBody),signal:activeController.signal});
    if(!response.ok){out.textContent=await response.text()||"请求失败";setStatus("请求失败");setRuntimeStatus("error",latestSources);return;}
    out.textContent="";setStatus("生成中...");const reader=response.body.getReader(),decoder=new TextDecoder();let buffer="";
    for(;;){const chunk=await reader.read();if(chunk.done)break;buffer+=decoder.decode(chunk.value,{stream:true});const lines=buffer.split("\\n");buffer=lines.pop();for(const line of lines){if(!line.trim())continue;try{const record=JSON.parse(line);if(record.message&&typeof record.message.content==="string")out.textContent+=record.message.content;if(typeof record.response==="string")out.textContent+=record.response;if(record.sources){latestSources = record.sources;renderSources(latestSources);}if(record.done) renderSources(latestSources);}catch(_){}}}
    if(buffer.trim()){try{const record=JSON.parse(buffer);if(record.message&&typeof record.message.content==="string")out.textContent+=record.message.content;if(typeof record.response==="string")out.textContent+=record.response;if(record.sources){latestSources=record.sources;renderSources(latestSources);}}catch(_) {}}
    sessionModel=requestModel;modelNotice.textContent=`当前会话模型：${sessionModel}`;conversationHistory.push({question:text,answer:out.textContent,mode:mode.value,profile:profile.value,sources:latestSources,createdAt:Date.now()});saveHistory();renderHistory();setStatus("完成");setRuntimeStatus("done",latestSources);
  }catch(error){if(error&&error.name==="AbortError"){setStatus("请求超时或已取消");out.textContent="请求已取消（或请求超时）";}else{setStatus("请求错误");out.textContent="无法连接服务，请稍后重试。";}setRuntimeStatus("error",latestSources);}
  finally{clearTimeout(timeout);send.disabled=false;cancel.disabled=true;activeController=null;}
};
</script>""".replace("{options}", options).replace("{profile_options}", profile_options).replace("{context_budget}", str(CHAT_CONTEXT_CHAR_BUDGET)).replace("{default_model}", escape(str(default_model)))


__all__ = ["render_chat_page"]
