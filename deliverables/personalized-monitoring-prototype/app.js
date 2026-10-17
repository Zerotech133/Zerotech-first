const state = { cycle: '周度', risk: '稳健', generated: false, backtested: false, published: false };
const cycleLabels = { 日内: '日内', 周度: '周度', 月度: '月度' };
const riskLimits = { 保守: '回撤 ≤ 8%', 稳健: '回撤 ≤ 15%', 进取: '回撤 ≤ 25%' };
const cycleSchedule = { 日内: '每个交易日 09:30', 周度: '每周一 09:30', 月度: '每月首个交易日 09:30' };

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function showToast(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.add('show');
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove('show'), 2400);
}

function updateSummary() {
  const summary = `${state.risk} · ${state.cycle}`;
  $('#requestedRisk').textContent = summary;
  $('#effectiveRisk').textContent = summary;
  if ($('#draftCycle')) $('#draftCycle').textContent = cycleSchedule[state.cycle];
  const riskCheck = $('.risk-check');
  if (state.risk === '进取') {
    $('#checkScore').textContent = '3 / 4';
    riskCheck.classList.remove('pass');
    riskCheck.classList.add('fail');
    riskCheck.querySelector('span').textContent = '!';
    riskCheck.querySelector('small').textContent = '建议将最大仓位降至 50% 以下';
  } else {
    $('#checkScore').textContent = '4 / 4';
    riskCheck.classList.remove('fail');
    riskCheck.classList.add('pass');
    riskCheck.querySelector('span').textContent = '✓';
    riskCheck.querySelector('small').textContent = '当前最大仓位 62%';
  }
}

function bindCycleControls() {
  $$('#cycleGroup button').forEach((button) => {
    button.addEventListener('click', () => {
      $$('#cycleGroup button').forEach((item) => item.classList.remove('active'));
      button.classList.add('active');
      state.cycle = button.dataset.cycle;
      updateSummary();
      showToast(`已切换至${state.cycle}监控周期`);
    });
  });
}

function bindRiskControls() {
  $$('#riskGroup .risk-option').forEach((button) => {
    button.addEventListener('click', () => {
      $$('#riskGroup .risk-option').forEach((item) => item.classList.remove('active'));
      button.classList.add('active');
      state.risk = button.dataset.risk;
      updateSummary();
      showToast(`风险档位已调整为${state.risk}（${riskLimits[state.risk]}）`);
    });
  });
}

function bindPrompt() {
  const input = $('#promptInput');
  const count = $('#charCount');
  input.addEventListener('input', () => { count.textContent = `${input.value.length} / 240`; });
  input.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') $('#generateBtn').click();
  });
  $('#generateBtn').addEventListener('click', () => {
    state.generated = true;
    $('#generatedCard').classList.remove('hidden');
    $('#draftTheme').textContent = input.value.trim() ? '新能源 · 半导体 · 自定义主题' : '新能源 · 半导体';
    $('#draftCycle').textContent = cycleSchedule[state.cycle];
    $('#publishBtn').disabled = true;
    const backtestButton = $('#backtestBtn');
    backtestButton.disabled = false;
    backtestButton.textContent = '▶ 运行回测';
    $('.timeline-step.active b').textContent = '生成配置';
    $('.timeline-step.active small').textContent = '待回测验证';
    showToast('配置草案已生成，请运行回测后发布');
  });
}

function bindBacktest() {
  $('#backtestBtn').addEventListener('click', () => {
    if (!state.generated) {
      showToast('请先生成 Agent 配置，再运行回测');
      return;
    }
    state.backtested = false;
    $('#publishBtn').disabled = true;
    const button = $('#backtestBtn');
    button.disabled = true;
    button.textContent = '回测运行中…';
    $('#gateStatus').textContent = '计算中';
    $('#gateStatus').style.color = '#2563eb';
    $('#gateStatus').style.background = '#e9f0ff';
    window.setTimeout(() => {
      state.backtested = true;
      $('#gateStatus').textContent = '已通过';
      $('#gateStatus').style.color = '#17a673';
      $('#gateStatus').style.background = '#e7f8f1';
      $('#backtestResult').innerHTML = '<div class="backtest-success"><span class="score">+18.6%</span><div><b>稳健性通过</b><small>3 年模拟收益 · 最大回撤 11.2%</small></div></div>';
      button.disabled = false;
      button.textContent = '↻ 重新运行回测';
      $('#publishBtn').disabled = false;
      showToast('回测通过，已解锁灰度发布');
    }, 900);
  });
}

function bindPublish() {
  $('#publishBtn').addEventListener('click', () => {
    if (!state.backtested) return;
    state.published = true;
    const button = $('#publishBtn');
    button.innerHTML = '<span>灰度运行中</span><i>✓</i>';
    button.disabled = true;
    button.style.background = '#2563eb';
    $('.timeline-step.active').classList.remove('active');
    $$('.timeline-step')[2].classList.add('active');
    $$('.timeline-step')[2].querySelector('b').textContent = '灰度运行';
    $$('.timeline-step')[2].querySelector('small').textContent = '进行中 · 第 1 天';
    showToast('Agent 已发布到灰度，开始 7 天观察期');
  });
}

bindCycleControls();
bindRiskControls();
bindPrompt();
bindBacktest();
bindPublish();
updateSummary();
