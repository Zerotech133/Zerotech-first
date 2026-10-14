"""独立的聊天与知识库问答网页渲染器。"""
from __future__ import annotations

from html import escape
from typing import Sequence


def _model_options(models: Sequence[str], default_model: str | None = None) -> str:
    values = tuple(str(model) for model in models)
    selected_model = default_model if default_model in values else (values[0] if values else None)
    options = []
    for model in values:
        label = model + ("（高级·可能冷启动）" if model == "qwen3.5:35b" else "")
        selected = " selected" if model == selected_model else ""
        options.append(
            '<option value="{value}"{selected}>{label}</option>'.format(
                value=escape(model, quote=True), selected=selected, label=escape(label)
            )
        )
    return "".join(options)


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


def render_chat_page(
    models: Sequence[str], profiles: Sequence[object] = (), default_model: str | None = None
) -> str:
    options = _model_options(models, default_model=default_model)
    profile_options = _profile_options(profiles)
    return r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0d1117">
<title>零界 · AI 聊天对话台</title>
<link rel="stylesheet" href="http://127.0.0.1:8734/static/tokens.css">
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
html, body { min-height: 100%; margin: 0; }
body { background: var(--bg-page); color: var(--text); font: 14px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; }
button, select, textarea { font: inherit; }
button, select { color: var(--text); background: var(--bg-raised); border: 1px solid var(--border); border-radius: var(--r-s); }
button { cursor: pointer; }
button:focus-visible, select:focus-visible, textarea:focus-visible, summary:focus-visible { outline: 2px solid var(--link); outline-offset: 2px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.workspace-shell { max-width: 1500px; margin: 0 auto; padding: 20px var(--sp-5) 32px; }
.workspace-header { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-4); margin-bottom: var(--sp-4); }
.brand-lockup { display: flex; align-items: center; gap: var(--sp-3); }
.brand-mark { display: grid; place-items: center; width: 38px; height: 38px; border-radius: var(--r-m); color: var(--text); background: var(--accent); font-size: 18px; font-weight: 800; }
.brand-lockup h1 { margin: 0; font-size: var(--fs-xl); letter-spacing: -.02em; }
.brand-lockup p { margin: 1px 0 0; color: var(--text-dim); font-size: var(--fs-s); }
.header-actions { display: flex; align-items: center; gap: var(--sp-2); }
.connection-badge, .mode-chip { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border: 1px solid var(--border-weak); border-radius: var(--r-pill); color: var(--text-dim); background: var(--bg-surface); font-size: var(--fs-s); }
.connection-badge::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--color-ok); }
.header-actions button { padding: 7px 11px; }
.workspace-grid { display: grid; grid-template-columns: 235px minmax(0, 1fr); gap: var(--sp-4); align-items: stretch; min-height: calc(100vh - 122px); }
.contextRail, .chatStage { min-width: 0; border: 1px solid var(--border-weak); background: var(--bg-surface); }
.contextRail { padding: var(--sp-4); }
.rail-heading { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-2); margin: 0 0 var(--sp-3); }
.rail-heading h2 { margin: 0; font-size: var(--fs-l); }
.rail-heading span { color: var(--text-dim); font-size: var(--fs-xs); }
.conversation-tools { display: grid; gap: var(--sp-2); margin-bottom: var(--sp-3); }
.conversation-tools input { width: 100%; padding: 8px 9px; border: 1px solid var(--border); border-radius: var(--r-s); color: var(--text); background: var(--bg-raised); }
.conversation-tools button { padding: 8px 10px; }
.conversation-list { display: grid; gap: 5px; max-height: 220px; margin-bottom: var(--sp-4); overflow: auto; }
.conversation-item { display: grid; gap: 2px; width: 100%; padding: 8px 9px; border: 1px solid transparent; border-radius: var(--r-s); color: var(--text-dim); background: transparent; text-align: left; }
.conversation-item:hover, .conversation-item.is-active { border-color: var(--border); background: var(--bg-raised); color: var(--text); }
.conversation-item strong { overflow: hidden; font-size: var(--fs-s); text-overflow: ellipsis; white-space: nowrap; }
.conversation-item span { font-size: var(--fs-xs); }
.control-card { display: grid; gap: var(--sp-2); margin-bottom: var(--sp-4); }
.control-card label { display: grid; gap: 5px; color: var(--text-dim); font-size: var(--fs-s); }
.control-card select { width: 100%; padding: 8px 9px; }
.runtime-card { padding-top: var(--sp-3); border-top: 1px solid var(--border-weak); }
.runtime-card dl { display: grid; gap: 8px; margin: 0; }
.runtime-row { display: flex; align-items: baseline; justify-content: space-between; gap: var(--sp-2); }
.runtime-row dt { color: var(--text-dim); font-size: var(--fs-s); }
.runtime-row dd { margin: 0; color: var(--text); font-size: var(--fs-s); text-align: right; }
.runtime-row dd[data-state="error"] { color: var(--color-err); }
.runtime-row dd[data-state="ok"] { color: var(--color-ok); }
.rail-note { margin: var(--sp-4) 0 0; color: var(--text-dim); font-size: var(--fs-xs); }
.chatStage { display: flex; flex-direction: column; overflow: hidden; }
.stage-toolbar { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-3); padding: 14px var(--sp-4); border-bottom: 1px solid var(--border-weak); }
.stage-toolbar h2 { margin: 0; font-size: var(--fs-l); }
.stage-toolbar p { margin: 2px 0 0; color: var(--text-dim); font-size: var(--fs-s); }
.stage-meta { display: flex; align-items: center; gap: var(--sp-2); }
.mobile-toggle { display: none; padding: 6px 9px; }
.transcript { flex: 1; min-height: 300px; overflow: auto; padding: var(--sp-5); }
.empty-state { display: grid; place-items: center; min-height: 260px; padding: var(--sp-5); color: var(--text-dim); text-align: center; }
.empty-state strong { display: block; margin-bottom: var(--sp-2); color: var(--text); font-size: 18px; }
.message-pair { display: grid; gap: var(--sp-3); margin: 0 auto var(--sp-5); max-width: 760px; }
.message-bubble { padding: 12px 14px; border: 1px solid var(--border-weak); border-radius: var(--r-m); white-space: pre-wrap; overflow-wrap: anywhere; }
.message-bubble p { margin: 0; }
.message-user { margin-left: 10%; background: var(--bg-raised); }
.message-assistant { margin-right: 10%; background: var(--bg-page); }
.message-label { margin-bottom: 4px; color: var(--text-dim); font-size: var(--fs-xs); }
.message-actions { display: flex; gap: 6px; margin-top: 8px; }
.message-actions button { padding: 3px 7px; border: 1px solid var(--border-weak); border-radius: var(--r-s); color: var(--text-dim); background: transparent; font-size: var(--fs-xs); }
.message-actions button:hover { color: var(--text); background: var(--bg-raised); }
.reasoning-block { max-width: 760px; margin: 0 auto var(--sp-3); border: 1px solid var(--border-weak); border-radius: var(--r-s); color: var(--text-dim); background: var(--bg-page); }
.reasoning-block summary { padding: 8px 10px; cursor: pointer; font-size: var(--fs-s); }
.reasoning-block pre { margin: 0; padding: 0 10px 10px; white-space: pre-wrap; overflow-wrap: anywhere; }
.live-answer { display: none; max-width: 760px; margin: 0 auto var(--sp-4); padding: 12px 14px; border-left: 2px solid var(--accent); color: var(--text); white-space: pre-wrap; overflow-wrap: anywhere; }
.live-answer.is-visible { display: block; }
.composer { padding: var(--sp-4); border-top: 1px solid var(--border-weak); background: var(--bg-page); }
.composer textarea { display: block; width: 100%; min-height: 84px; resize: vertical; padding: 11px 12px; border: 1px solid var(--border); border-radius: var(--r-m); color: var(--text); background: var(--bg-raised); }
.composer-footer { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-3); margin-top: var(--sp-2); }
.composer-hint { color: var(--text-dim); font-size: var(--fs-xs); }
.composer-options { display: flex; align-items: center; gap: var(--sp-3); margin-top: 9px; color: var(--text-dim); font-size: var(--fs-xs); }
.composer-options label { display: inline-flex; align-items: center; gap: 5px; }
.composer-options input { accent-color: var(--accent); }
.composer-options input:disabled + span { opacity: .55; }
.composer-actions { display: flex; gap: var(--sp-2); }
.composer-actions button { padding: 8px 13px; }
.primary-action { border-color: var(--accent); background: var(--accent); color: var(--text); font-weight: 700; }
.secondary-action { background: transparent; }
.status-line { min-height: 22px; color: var(--text-dim); font-size: var(--fs-s); }
.status-line[data-state="error"] { color: var(--color-err); }
.status-line[data-state="ok"] { color: var(--color-ok); }
.evidence-rail details { border: 1px solid var(--border-weak); border-radius: var(--r-s); background: var(--bg-page); }
.evidence-rail summary { padding: 10px 11px; cursor: pointer; color: var(--text); font-weight: 650; }
.source-list { display: grid; gap: var(--sp-2); padding: 0 10px 10px; }
.source-card { padding: 9px; border: 1px solid var(--border-weak); border-radius: var(--r-s); background: var(--bg-surface); }
.source-card strong { display: block; overflow: hidden; color: var(--text); font-size: var(--fs-s); text-overflow: ellipsis; white-space: nowrap; }
.source-card span { display: block; color: var(--text-dim); font-size: var(--fs-xs); }
.source-card > summary { cursor: pointer; list-style: none; }
.source-card > summary::-webkit-details-marker { display: none; }
.source-detail { display: grid; gap: 7px; margin-top: 8px; }
.source-snippet { margin: 0; color: var(--text-dim); font-size: var(--fs-xs); white-space: pre-wrap; overflow-wrap: anywhere; }
.source-copy { justify-self: start; padding: 4px 7px; border: 1px solid var(--border-weak); border-radius: var(--r-s); color: var(--text-dim); background: transparent; font-size: var(--fs-xs); }
.source-copy:hover { color: var(--text); background: var(--bg-raised); }
.inline-source-panel { max-width: 760px; margin: 0 auto var(--sp-3); border: 1px solid var(--border-weak); border-radius: var(--r-s); color: var(--text-dim); background: var(--bg-page); }
.inline-source-panel > summary { padding: 8px 10px; cursor: pointer; color: var(--text); font-size: var(--fs-s); font-weight: 650; }
.inline-source-panel .source-list { padding: 0 10px 10px; }
.history-sources { margin: 10px 0 0; }
.quick-prompts { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px var(--sp-4) 0; }
.quick-prompts button { padding: 6px 11px; border: 1px solid var(--border-weak); border-radius: var(--r-pill); color: var(--text-dim); background: transparent; font-size: var(--fs-xs); }
.quick-prompts button:hover { border-color: var(--accent); color: var(--text); background: var(--bg-raised); }
.evidence-empty { margin: var(--sp-3) 0 0; color: var(--text-dim); font-size: var(--fs-s); }
@media (max-width: 980px) {
  .workspace-grid { grid-template-columns: minmax(0, 1fr); }
  .contextRail { position: fixed; inset: 66px auto 0 0; z-index: 90; width: 260px; transform: translateX(-105%); transition: transform .2s ease; box-shadow: 12px 0 28px rgba(0,0,0,.35); }
  body.context-open .contextRail { transform: translateX(0); }
  .mobile-toggle { display: inline-flex; }
}
@media (max-width: 700px) {
  .workspace-shell { padding: 12px 10px 20px; }
  .workspace-header { align-items: flex-start; }
  .header-actions { flex-wrap: wrap; justify-content: flex-end; }
  .connection-badge { display: none; }
  .workspace-grid { display: block; min-height: calc(100vh - 82px); }
  .chatStage { min-height: calc(100vh - 82px); }
  .transcript { padding: var(--sp-3); }
  .message-user, .message-assistant { margin-left: 0; margin-right: 0; }
  .composer-footer { align-items: flex-start; flex-direction: column; }
  .composer-actions { width: 100%; }
  .composer-actions button { flex: 1; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; }
}
</style>
</head>
<body>
<main id="appShell" class="workspace-shell">
  <header class="workspace-header">
    <div class="brand-lockup">
      <div class="brand-mark" aria-hidden="true">零</div>
      <div><h1>零界对话台</h1><p>本地 Ollama · 私有上下文 · 可追溯回答</p></div>
    </div>
    <div class="header-actions">
      <span id="connectionBadge" class="connection-badge">本地服务</span>
      <button id="mobileContextToggle" class="mobile-toggle" type="button" aria-controls="contextRail" aria-expanded="false">上下文</button>
      <button id="clearHistory" class="secondary-action" type="button">清理历史</button>
    </div>
  </header>
  <div class="workspace-grid">
    <aside id="contextRail" class="contextRail" aria-label="工作上下文">
      <div class="rail-heading"><h2>工作上下文</h2><span>本地</span></div>
      <div class="conversation-tools">
        <button id="newChat" class="primary-action" type="button">＋ 新对话</button>
        <label class="sr-only" for="conversationSearch">搜索会话</label>
        <input id="conversationSearch" type="search" placeholder="搜索会话" autocomplete="off">
      </div>
      <nav id="conversationList" class="conversation-list" aria-label="会话列表"></nav>
      <div class="control-card">
        <label for="profile">角色<select id="profile">{profile_options}</select></label>
        <label for="model">模型<select id="model">{options}</select></label>
        <label for="mode">模式<select id="mode"><option value="chat">普通聊天</option><option value="rag">知识库问答</option></select></label>
      </div>
      <section id="runtimeStatus" class="runtime-card" aria-label="运行状态">
        <div class="rail-heading"><h2>运行状态</h2><span id="statusSignal">待命</span></div>
        <dl><div class="runtime-row"><dt>当前模式</dt><dd id="modeStatus">普通聊天</dd></div><div class="runtime-row"><dt>索引版本</dt><dd id="indexStatus">—</dd></div><div class="runtime-row"><dt>文档来源</dt><dd id="sourceStatus">不适用</dd></div></dl>
      </section>
      <p class="rail-note">普通聊天按约 12000 字符保留上下文；知识库回答会展示来源和索引版本。</p>
    </aside>
    <section class="chatStage" aria-label="对话工作区">
      <div class="stage-toolbar"><div><h2>对话流</h2><p id="status" role="status" aria-live="polite">准备就绪</p></div><div class="stage-meta"><span id="modeChip" class="mode-chip">普通聊天</span><button id="scrollTop" class="secondary-action" type="button" title="回到顶部">回到顶部</button></div></div>
      <div id="history" class="transcript" aria-label="对话历史"><div id="emptyState" class="empty-state"><div><strong>从一个问题开始</strong><span>选择角色和模式，回答会留在本地浏览器。</span></div></div></div>
      <details id="reasoning" class="reasoning-block" hidden><summary>思考过程</summary><pre id="reasoningText"></pre></details>
      <pre id="out" class="live-answer" aria-live="polite"></pre>
      <details id="sources" class="inline-source-panel" hidden><summary>来源 · 检索结果</summary><div id="sourceList" class="source-list"></div><p id="evidenceEmpty" class="evidence-empty">本次回答未命中知识库来源。</p></details>
      <div id="quickPrompts" class="quick-prompts" aria-label="快捷提问">
        <button type="button" data-prompt="请总结上一段对话的要点">总结要点</button>
        <button type="button" data-prompt="请解释这份回答依据了什么">解释依据</button>
        <button type="button" data-prompt="请列出下一步可执行的行动">行动清单</button>
      </div>
      <form id="composer" class="composer"><label class="sr-only" for="message">输入消息</label><textarea id="message" placeholder="输入消息，Ctrl + Enter 发送" rows="3"></textarea><div class="composer-options"><label><input id="useContext" type="checkbox" checked><span>保留上下文</span></label><label title="开启后会显著增加首字延迟"><input id="thinkMode" type="checkbox"><span>深度思考</span></label><label title="本地服务尚未接入联网搜索"><input id="webSearch" type="checkbox" disabled><span>联网搜索（待接入）</span></label></div><div class="composer-footer"><span class="composer-hint">回答会流式显示 · 历史只保存在本机</span><div class="composer-actions"><button id="cancel" class="secondary-action" type="button" aria-label="取消请求" disabled>取消</button><button id="send" class="primary-action" type="submit" aria-label="发送消息">发送</button></div></div></form>
    </section>
  </div>
</main>
<script src="http://127.0.0.1:8734/static/nav.js" defer></script>
<script>
const send=document.getElementById("send"),cancel=document.getElementById("cancel"),clearHistory=document.getElementById("clearHistory"),composer=document.getElementById("composer"),newChat=document.getElementById("newChat"),conversationSearch=document.getElementById("conversationSearch"),conversationList=document.getElementById("conversationList"),scrollTop=document.getElementById("scrollTop"),useContext=document.getElementById("useContext"),thinkMode=document.getElementById("thinkMode"),webSearch=document.getElementById("webSearch");
const model=document.getElementById("model"),profile=document.getElementById("profile"),mode=document.getElementById("mode"),message=document.getElementById("message");
const status=document.getElementById("status"),history=document.getElementById("history"),out=document.getElementById("out"),emptyState=document.getElementById("emptyState");
const sources=document.getElementById("sources"),sourceList=document.getElementById("sourceList"),evidenceEmpty=document.getElementById("evidenceEmpty");
const modeStatus=document.getElementById("modeStatus"),modeChip=document.getElementById("modeChip"),indexStatus=document.getElementById("indexStatus"),sourceStatus=document.getElementById("sourceStatus"),statusSignal=document.getElementById("statusSignal");
const mobileContextToggle=document.getElementById("mobileContextToggle"),contextRail=document.getElementById("contextRail");
const reasoning=document.getElementById("reasoning"),reasoningText=document.getElementById("reasoningText"),quickPrompts=document.getElementById("quickPrompts");
const HISTORY_KEY="zero-boundary-chat-history-v1",CONVERSATIONS_KEY="zero-boundary-conversations-v1",REQUEST_TIMEOUT_MS=120000,MAX_CONTEXT_CHARS=12000;
let conversations=loadConversations(),activeConversationId=conversations[0]?.id||"conversation-"+Date.now(),conversationHistory=loadActiveHistory(),latestSources=[],latestReasoning="",activeController=null,sessionModel=model.value;if(conversations[0]?.model){sessionModel=conversations[0].model;model.value=sessionModel;}
function loadLegacyHistory(){try{const value=JSON.parse(localStorage.getItem(HISTORY_KEY)||"[]");return Array.isArray(value)?value:[];}catch(_){return [];}}
function loadConversations(){try{const value=JSON.parse(localStorage.getItem(CONVERSATIONS_KEY)||"null");if(Array.isArray(value)&&value.length)return value.filter((item)=>item&&item.id&&Array.isArray(item.messages));}catch(_){}const legacy=loadLegacyHistory();return [{id:"conversation-"+Date.now(),title:legacy[0]?.question||"新对话",messages:legacy,updatedAt:Date.now()}];}
function loadActiveHistory(){const current=conversations.find((item)=>item.id===activeConversationId);return Array.isArray(current?.messages)?current.messages.slice(-50):[];}
function saveConversations(){const current=conversations.find((item)=>item.id===activeConversationId);if(current)current.model=sessionModel;localStorage.setItem(CONVERSATIONS_KEY,JSON.stringify(conversations.slice(0,30)));}
function saveHistory(){const current=conversations.find((item)=>item.id===activeConversationId);if(current){current.messages=conversationHistory.slice(-50);current.title=current.messages[0]?.question||current.title||"新对话";current.updatedAt=Date.now();}saveConversations();localStorage.setItem(HISTORY_KEY,JSON.stringify(conversationHistory.slice(-50)));}
function setStatus(text,state=""){status.textContent=text;status.dataset.state=state;statusSignal.textContent=state==="error"?"异常":state==="ok"?"完成":"处理中";}
function setRuntimeStatus(state,items){const isRag=mode.value==="rag",label=isRag?"知识库问答":"普通聊天";modeStatus.textContent=label;modeChip.textContent=label;if(!isRag){indexStatus.textContent="—";sourceStatus.textContent="不适用";return;}const list=Array.isArray(items)?items:[];if(state==="loading"){indexStatus.textContent="读取中…";sourceStatus.textContent="等待来源";return;}const versions=[...new Set(list.map((item)=>item&&item.index_version).filter((version)=>version!==undefined&&version!==null))];indexStatus.textContent=versions.length?versions.map((version)=>`v${version}`).join(", "):"—";sourceStatus.textContent=list.length?`已命中 ${list.length} 个来源`:state==="error"?"不可用":state==="done"?"未命中来源":"未加载";indexStatus.dataset.state=state==="done"?"ok":state==="error"?"error":"";sourceStatus.dataset.state=state==="done"&&list.length?"ok":state==="error"?"error":"";}
function renderConversationList(){const query=conversationSearch.value.trim().toLowerCase();conversationList.textContent="";conversations.filter((item)=>!query||String(item.title||"").toLowerCase().includes(query)).forEach((item)=>{const button=document.createElement("button");button.type="button";button.className="conversation-item"+(item.id===activeConversationId?" is-active":"");button.dataset.conversationId=item.id;const title=document.createElement("strong");title.textContent=item.title||"新对话";const meta=document.createElement("span");meta.textContent=(item.messages||[]).length?`${item.messages.length} 条消息`:"空会话";button.append(title,meta);conversationList.appendChild(button);});}
function renderHistory(){history.textContent="";if(!conversationHistory.length){history.appendChild(emptyState);return;}conversationHistory.forEach((item,index)=>{const entry=document.createElement("article");entry.className="message-pair";entry.dataset.messageIndex=String(index);const question=document.createElement("div");question.className="message-bubble message-user";question.innerHTML='<div class="message-label">你</div>';const questionText=document.createElement("p");questionText.textContent=String(item.question||"");question.appendChild(questionText);const answer=document.createElement("div");answer.className="message-bubble message-assistant";const answerLabel=document.createElement("div");answerLabel.className="message-label";answerLabel.textContent="AI · "+String(item.profile||"通用助手");answer.appendChild(answerLabel);const answerText=document.createElement("p");answerText.textContent=String(item.answer||"");answer.appendChild(answerText);if(item.reasoning){const detail=document.createElement("details");detail.className="reasoning-block";const summary=document.createElement("summary");summary.textContent="思考过程";const pre=document.createElement("pre");pre.textContent=String(item.reasoning);detail.append(summary,pre);answer.appendChild(detail);}if(Array.isArray(item.sources)&&item.sources.length){const sourcePanel=document.createElement("details");sourcePanel.className="inline-source-panel history-sources";const sourceSummary=document.createElement("summary");sourceSummary.textContent="来源 · "+item.sources.length+" 条";const sourceListEl=document.createElement("div");sourceListEl.className="source-list";sourcePanel.append(sourceSummary,sourceListEl);renderSourceCards(sourceListEl,item.sources);answer.appendChild(sourcePanel);}const actions=document.createElement("div");actions.className="message-actions";const copy=document.createElement("button");copy.type="button";copy.dataset.action="copy";copy.textContent="复制";const retry=document.createElement("button");retry.type="button";retry.dataset.action="retry";retry.textContent="重试";actions.append(copy,retry);answer.appendChild(actions);entry.append(question,answer);history.appendChild(entry);});}
function renderReasoning(){reasoning.hidden=!latestReasoning;reasoningText.textContent=latestReasoning;}
function copyMessage(index){const item=conversationHistory[index];if(!item)return;const text=String(item.answer||"");if(navigator.clipboard?.writeText)navigator.clipboard.writeText(text).then(()=>setStatus("已复制回答","ok"));else setStatus("当前浏览器不支持复制","error");}
function retryMessage(index){const item=conversationHistory[index];if(!item||activeController)return;message.value=String(item.question||"");composer.requestSubmit();}
function switchConversation(id){if(id===activeConversationId)return;saveHistory();const target=conversations.find((item)=>item.id===id);if(!target)return;activeConversationId=id;conversationHistory=Array.isArray(target.messages)?target.messages.slice(-50):[];sessionModel=target.model||model.value;model.value=sessionModel;latestSources=[];latestReasoning="";renderHistory();renderConversationList();renderReasoning();setStatus("已切换会话 · "+sessionModel,"ok");}
function createConversation(){saveHistory();const item={id:"conversation-"+Date.now(),title:"新对话",model:sessionModel,messages:[],updatedAt:Date.now()};conversations=[item,...conversations];activeConversationId=item.id;conversationHistory=[];latestSources=[];latestReasoning="";renderHistory();renderConversationList();renderSources([]);renderReasoning();setStatus("已新建会话 · "+sessionModel,"ok");message.focus();}
// 兼容旧请求契约：history: historyMessages()
function historyMessages(){let used=0,rows=[];for(let index=conversationHistory.length-1;index>=0;index--){const item=conversationHistory[index],pair=[];if(item&&item.question)pair.push({role:"user",content:String(item.question)});if(item&&item.answer)pair.push({role:"assistant",content:String(item.answer)});const size=pair.reduce((total,row)=>total+row.content.length,0);if(rows.length&&used+size>MAX_CONTEXT_CHARS)break;if(size>MAX_CONTEXT_CHARS-used){const room=Math.max(1,MAX_CONTEXT_CHARS-used);pair.splice(0,pair.length,...pair.map((row)=>({...row,content:row.content.slice(-room)})));}rows.unshift(...pair);used+=pair.reduce((total,row)=>total+row.content.length,0);if(used>=MAX_CONTEXT_CHARS)break;}return rows;}
function sourceCitation(item){const source=item&&item.source?String(item.source):"未知来源";const excerpt=String(item&&(item.text||item.snippet)||"").trim();const score=item&&item.score!==undefined?"相似度："+item.score:"未提供相似度";const version=item&&item.index_version?"索引：v"+item.index_version:"";return ["来源："+source,excerpt?"片段："+excerpt:"",score,version].filter(Boolean).join("\n");}
function copySourceCitation(item){const text=sourceCitation(item);if(navigator.clipboard?.writeText)navigator.clipboard.writeText(text).then(()=>setStatus("已复制来源引用","ok")).catch(()=>setStatus("复制失败","error"));else setStatus("当前浏览器不支持复制","error");}
function renderSourceCards(container,items){container.textContent="";(Array.isArray(items)?items:[]).forEach((item)=>{const card=document.createElement("details");card.className="source-card";const summary=document.createElement("summary");const name=document.createElement("strong");name.textContent=item&&item.source?String(item.source):"未知来源";const meta=document.createElement("span");const score=item&&item.score!==undefined?"相似度 "+item.score:"未提供分数";const hash=item&&item.document_hash?" · 哈希 "+String(item.document_hash).slice(0,12):"";const version=item&&item.index_version?" · 索引 v"+item.index_version:"";meta.textContent=score+hash+version;summary.append(name,meta);const detail=document.createElement("div");detail.className="source-detail";const snippet=document.createElement("p");snippet.className="source-snippet";snippet.textContent=String(item&&(item.text||item.snippet)||"未返回片段");const copy=document.createElement("button");copy.type="button";copy.className="source-copy";copy.dataset.action="copy-source";copy.textContent="复制引用";copy.onclick=()=>copySourceCitation(item);detail.append(snippet,copy);card.append(summary,detail);container.appendChild(card);});}
function renderInlineSources(container,items){const list=Array.isArray(items)?items:[];container.hidden=list.length===0;const sourceListEl=container.querySelector(".source-list");if(sourceListEl)renderSourceCards(sourceListEl,list);}
function renderSources(items){const list=Array.isArray(items)?items:[];sources.hidden=list.length===0;evidenceEmpty.hidden=list.length>0;if(!list.length){sourceList.textContent="";return;}renderSourceCards(sourceList,list);}
renderConversationList();renderHistory();renderReasoning();setRuntimeStatus("idle",latestSources);setStatus("准备就绪 · "+sessionModel,"ok");model.onchange=()=>{const previous=sessionModel;sessionModel=model.value;const current=conversations.find((item)=>item.id===activeConversationId);if(current)current.model=sessionModel;if(previous!==sessionModel&&conversationHistory.length)setStatus("已切换模型，下一次请求可能冷启动","ok");else setStatus("当前会话模型 · "+sessionModel,"ok");saveConversations();};mode.onchange=()=>{latestSources=[];renderSources([]);setRuntimeStatus("idle",latestSources);};quickPrompts.onclick=(event)=>{const button=event.target.closest("[data-prompt]");if(button){message.value=button.dataset.prompt||"";message.focus();}};newChat.onclick=createConversation;conversationSearch.oninput=renderConversationList;conversationList.onclick=(event)=>{const button=event.target.closest("[data-conversation-id]");if(button)switchConversation(button.dataset.conversationId);};clearHistory.onclick=()=>{conversationHistory=[];latestSources=[];latestReasoning="";saveHistory();localStorage.removeItem(HISTORY_KEY);renderHistory();renderConversationList();renderSources([]);renderReasoning();setStatus("历史已清理","ok");};scrollTop.onclick=()=>history.scrollTo({top:0,behavior:"smooth"});mobileContextToggle.onclick=()=>{const open=document.body.classList.toggle("context-open");mobileContextToggle.setAttribute("aria-expanded",String(open));};history.onclick=(event)=>{const button=event.target.closest("[data-action]");if(!button)return;const entry=button.closest("[data-message-index]");const index=Number(entry?.dataset.messageIndex);if(button.dataset.action==="copy")copyMessage(index);if(button.dataset.action==="retry")retryMessage(index);};message.addEventListener("keydown",(event)=>{if((event.ctrlKey||event.metaKey)&&event.key==="Enter"){event.preventDefault();composer.requestSubmit();}});cancel.onclick=()=>{if(activeController)activeController.abort();};
composer.onsubmit=async(event)=>{event.preventDefault();const text=message.value.trim();if(!text){out.textContent="请输入消息";out.classList.add("is-visible");setStatus("请输入消息","error");return;}out.textContent="思考中…";out.classList.add("is-visible");setStatus("加载中…");renderSources([]);latestSources=[];latestReasoning="";renderReasoning();setRuntimeStatus("loading",latestSources);send.disabled=true;cancel.disabled=false;activeController=new AbortController();const timeout=setTimeout(()=>activeController.abort(),REQUEST_TIMEOUT_MS);try{const endpoint=mode.value==="rag"?"/api/rag":"/api/chat",requestBody=mode.value==="rag"?{message:text, profile: profile.value}:{model:sessionModel, message:text, profile: profile.value, think: thinkMode.checked, history: useContext.checked ? historyMessages() : []},response=await fetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(requestBody),signal:activeController.signal});if(!response.ok){out.textContent=await response.text()||"请求失败";setStatus("请求失败","error");setRuntimeStatus("error",latestSources);return;}out.textContent="";setStatus("生成中…");const reader=response.body.getReader(),decoder=new TextDecoder();let buffer="";for(;;){const chunk=await reader.read();if(chunk.done)break;buffer+=decoder.decode(chunk.value,{stream:true});const lines=buffer.split("\n");buffer=lines.pop();for(const line of lines){if(!line.trim())continue;try{const record=JSON.parse(line);if(record.message&&typeof record.message.content==="string")out.textContent+=record.message.content;if(record.message&&typeof record.message.thinking==="string")latestReasoning+=record.message.thinking;if(typeof record.thinking==="string")latestReasoning+=record.thinking;if(typeof record.response==="string")out.textContent+=record.response;if(record.sources){ latestSources = record.sources;renderSources(latestSources);}renderReasoning();if (record.done) renderSources(latestSources);}catch(_) {}}}if(buffer.trim()){try{const record=JSON.parse(buffer);if(record.message&&typeof record.message.content==="string")out.textContent+=record.message.content;if(record.message&&typeof record.message.thinking==="string")latestReasoning+=record.message.thinking;if(typeof record.thinking==="string")latestReasoning+=record.thinking;if(typeof record.response==="string")out.textContent+=record.response;if(record.sources){latestSources=record.sources;renderSources(latestSources);}renderReasoning();}catch(_) {}}conversationHistory.push({question:text,answer:out.textContent,reasoning:latestReasoning,mode:mode.value,profile:profile.value,model:sessionModel,sources:latestSources,createdAt:Date.now()});saveHistory();renderHistory();renderConversationList();setStatus("完成","ok");setRuntimeStatus("done",latestSources);}catch(error){if(error&&error.name==="AbortError"){setStatus("请求超时或已取消","error");out.textContent="请求已取消（或请求超时）";}else{setStatus("连接错误","error");out.textContent="无法连接服务，请稍后重试。";}out.classList.add("is-visible");setRuntimeStatus("error",latestSources);}finally{clearTimeout(timeout);send.disabled=false;cancel.disabled=true;activeController=null;}};
</script>
</body>
</html>'''.replace("{options}", options).replace("{profile_options}", profile_options)


__all__ = ["render_chat_page"]
