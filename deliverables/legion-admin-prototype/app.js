(() => {
  'use strict';
  const API_BASE = window.LEGION_ADMIN_API_BASE || '/api/v1';
  const tree = document.getElementById('org-tree');
  const title = document.getElementById('agent-title');
  const description = document.getElementById('agent-description');
  const identityName = document.getElementById('identity-name');
  const breadcrumb = document.getElementById('breadcrumb-current');
  const statusText = document.getElementById('status-text');
  const toast = document.getElementById('toast');
  const toastText = document.getElementById('toast-text');
  const modal = document.getElementById('adapter-modal');
  const unavailable = 'unavailable';
  let toastTimer = 0;
  let currentAgentId = null;

  function showToast(message) {
    toastText.textContent = message;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 2600);
  }
  function text(id, value) { const el = document.getElementById(id); if (el) el.textContent = value == null || value === '' ? unavailable : String(value); }
  function value(obj, ...keys) { for (const key of keys) { const v = obj?.[key]; if (v != null && v !== '') return v; } return null; }
  function clearDynamic() {
    ['identity-id','identity-name','identity-title','identity-rank','identity-endpoint','identity-onboarded','identity-supervisor','identity-department','identity-chief','agent-title','agent-description','status-text','heartbeat-status','heartbeat-relative','heartbeat-ts'].forEach(id => text(id, unavailable));
    text('availability-value', '—');
  }
  function renderTree(root) {
    tree.innerHTML = '';
    const group = document.createElement('div'); group.className = 'tree-group';
    const rootRow = row(root, true); group.appendChild(rootRow);
    const children = document.createElement('div'); children.className = 'tree-children';
    (root.children || []).forEach(dept => {
      const deptRow = row(dept, false); children.appendChild(deptRow);
      const members = document.createElement('div'); members.className = 'tree-children nested';
      (dept.children || []).forEach(agent => members.appendChild(row(agent, false)));
      if (members.children.length) children.appendChild(members);
    });
    group.appendChild(children); tree.appendChild(group);
  }
  function row(node, isRoot) {
    const button = document.createElement('button'); button.type = 'button'; button.className = `tree-row${isRoot ? ' root expanded' : ''}`;
    button.dataset.target = node.agent_id || node.dept_id || node.node_id;
    const icon = node.type === 'agent' ? '◇' : (isRoot ? '✦' : '⌘');
    button.innerHTML = `<span class="tree-caret">${isRoot || node.children?.length ? '⌄' : ''}</span><span class="node-icon">${icon}</span><span class="tree-label"></span><span class="node-count"></span>`;
    button.querySelector('.tree-label').textContent = node.label || node.node_id || unavailable;
    if (node.type === 'agent') { button.querySelector('.node-count').textContent = node.live_state === 'online' ? '在线' : (node.live_state || 'unknown'); }
    else if (node.count != null) button.querySelector('.node-count').textContent = String(node.count);
    return button;
  }
  async function get(path) {
    const response = await fetch(`${API_BASE}${path}`, { headers: { Accept: 'application/json' } });
    const envelope = await response.json();
    if (!response.ok && response.status !== 206) throw new Error(envelope.message || `HTTP ${response.status}`);
    return envelope;
  }
  async function loadAgent(agentId) {
    currentAgentId = agentId;
    clearDynamic();
    try {
      const envelope = await get(`/agents/${encodeURIComponent(agentId)}`); const data = envelope.data || {};
      text('identity-id', data.agent_id); text('identity-name', data.name); text('identity-title', data.title); text('identity-rank', data.rank); text('identity-endpoint', data.endpoint); text('identity-onboarded', data.employment?.onboarded); text('identity-supervisor', data.supervisor_id); text('identity-department', data.dept_name || data.dept_id); text('identity-chief', data.appointment?.chief || data.resume?.chief);
      text('agent-title', data.name || data.agent_id); text('agent-description', data.resume?.self_statement || data.title); text('breadcrumb-current', data.name || data.agent_id); text('status-text', data.live_state || data.admin_status);
      text('heartbeat-status', data.runtime?.state || data.live_state); text('heartbeat-relative', data.runtime?.checked_at ? '已检查' : unavailable); text('heartbeat-ts', data.runtime?.checked_at);
      text('availability-value', '—');
      const permissionList = document.getElementById('permission-list');
      if (permissionList) {
        const permissions = data.permissions;
        const normalized = Array.isArray(permissions) ? permissions.map((item, index) => [String(index + 1), item]) : (permissions && typeof permissions === 'object' ? Object.entries(permissions) : []);
        const entries = normalized.filter(([key]) => key !== 'danger');
        permissionList.innerHTML = entries.length ? entries.map(([key, value]) => `<div class="permission-row"><span class="permission-name"></span><b class="unavailable"></b></div>`).join('') : '<div class="empty-state">暂无真实权限数据源</div>';
        entries.forEach(([key, permission], index) => { const row = permissionList.children[index]; row.querySelector('.permission-name').textContent = key; row.querySelector('b').textContent = Array.isArray(permission) ? permission.join(', ') : String(permission ?? unavailable); });
      }
    } catch (error) { clearDynamic(); showToast(`数据暂不可用：${error.message}`); }
  }
  async function loadTree() {
    try {
      const envelope = await get('/org/tree'); renderTree(envelope.data || {});
      const first = (envelope.data?.children || []).flatMap(d => d.children || []).find(n => n.agent_id) || 'WATCH_TOWER';
      await loadAgent(first);
      if (envelope.code === 'PARTIAL') showToast('组织树部分数据不可用，已标记 unknown');
    } catch (error) { tree.innerHTML = '<div class="empty-state">组织数据 unavailable</div>'; clearDynamic(); showToast(`组织数据暂不可用：${error.message}`); }
  }
  tree.addEventListener('click', event => { const rowEl = event.target.closest('.tree-row'); if (rowEl && rowEl.dataset.target) loadAgent(rowEl.dataset.target); });
  document.getElementById('org-search').addEventListener('input', event => { const q = event.target.value.trim().toLowerCase(); tree.querySelectorAll('.tree-row').forEach(r => { r.style.display = !q || r.textContent.toLowerCase().includes(q) ? 'flex' : 'none'; }); });
  document.getElementById('open-adapter').addEventListener('click', () => showToast('P0 未开放：Agent 注册尚未接入')); 
  document.getElementById('close-modal')?.addEventListener('click', () => modal.classList.remove('open'));
  document.getElementById('pause-btn').addEventListener('click', () => showToast('P0 未开放：暂停/恢复为只读范围外'));
  document.getElementById('restart-btn').addEventListener('click', () => showToast('P0 未开放：重启为只读范围外'));
  document.getElementById('audit-btn').addEventListener('click', () => showToast('P0 未开放：审计查询尚未接入'));
  document.querySelector('.copy-btn')?.addEventListener('click', () => showToast('复制功能待接入'));
  loadTree();
})();
