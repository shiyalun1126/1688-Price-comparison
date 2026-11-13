/**
 * 智能体控制台：ReAct 循环、工具调用、推理轨迹展示
 */

const TOOL_CN = {
  list_targets: '列出目标款', inspect_target: '查看目标款详情', match_products: '执行视觉匹配',
  compare_prices: '生成比价结论', inquiry_script: '生成询价话术', make_search_link: '生成1688搜索链接',
  import_products: '导入商品链接', plan_search: '规划搜索策略', auto_import: '浏览器自动搜索导入',
  create_plan: '创建采购计划', get_plan: '查看计划进度', update_plan_step: '更新计划进度',
};

const PLAN_MARK = { done: '✅', in_progress: '▶️', blocked: '⚠️', pending: '⬜' };

// ==================== Agent 核心 ====================
async function runAgent() {
  const goal = (document.getElementById('agent-goal').value || '').trim();
  if (!goal) return alert('请先输入你想让智能体做什么');

  const btn = document.getElementById('btn-agent');
  const box = document.getElementById('agent-trace');
  btn.disabled = true;
  btn.textContent = '思考中…';
  box.innerHTML = `<div class="trace-step"><div class="rnd">RUNNING</div>
    <div class="trace-thought"><span class="trace-label">启动</span>智能体正在规划任务…（每一步都会调用真实工具，可能需要几十秒）</div></div>`;

  let r;
  try {
    r = await api('/api/agent/run', {
      method: 'POST',
      body: JSON.stringify({ goal, target_id: currentTargetId }),
    });
  } catch (e) {
    box.innerHTML = `<div class="agent-final" style="background:var(--danger-soft);border-color:var(--danger)"><span class="trace-label" style="color:var(--danger)">失败</span><div class="final-body">请求失败：${escapeHtml(e.message)}</div></div>`;
    btn.disabled = false; btn.textContent = '分析';
    return;
  } finally {
    if (btn.textContent === '思考中…') {
      btn.disabled = false;
      btn.textContent = '分析';
    }
  }

  if (r.error) {
    box.innerHTML = `<div class="agent-final" style="background:var(--danger-soft);border-color:var(--danger)"><span class="trace-label" style="color:var(--danger)">错误</span><div class="final-body">${escapeHtml(r.error)}</div></div>`;
    return;
  }

  const steps = r.steps || [];
  let html = '';
  for (const s of steps) {
    html += `<div class="trace-step"><div class="rnd">R${s.round}</div>`;
    if (s.thought) {
      html += `<div class="trace-thought"><span class="trace-label">思考</span>${renderMarkdown(s.thought)}</div>`;
    }
    if (s.tool) {
      const cn = TOOL_CN[s.tool] || s.tool;
      html += `<div class="trace-tool">▸ ${cn}<span class="raw">${escapeHtml(s.tool)}</span></div>`;
      const argsStr = s.args && Object.keys(s.args).length ? JSON.stringify(s.args) : '';
      if (argsStr) html += `<div class="trace-args">参数 ${escapeHtml(argsStr)}</div>`;
      if (s.observation) {
        html += `<div class="trace-obs"><span class="trace-label">观察</span>${escapeHtml(s.observation.slice(0, 600))}${s.observation.length > 600 ? '\n…(已省略)' : ''}</div>`;
      }
    }
    html += `</div>`;
  }
  if (r.plan) {
    html += renderPlanBlock(r.plan);
  }
  if (r.final_answer) {
    html += `<div class="agent-final"><span class="trace-label">结论</span><div class="final-body">${renderMarkdown(r.final_answer)}</div></div>`;
  }
  if (r.usage) {
    const u = r.usage;
    const total = (u.prompt_tokens || 0) + (u.completion_tokens || 0);
    const cost = (u.cost_cny !== null && u.cost_cny !== undefined)
      ? `，约 ¥${Number(u.cost_cny).toFixed(3)}` : '';
    html += `<div class="trace-usage">本次消耗 ${total} tokens（输入 ${u.prompt_tokens || 0} / 输出 ${u.completion_tokens || 0}，${u.requests || 0} 次调用）${cost}</div>`;
  }
  box.innerHTML = html || '<p class="muted">智能体没有返回内容</p>';
  loadAgentHistory();
}

function setGoal(text) {
  document.getElementById('agent-goal').value = text;
  runAgent();
}

// ==================== Agent 历史 ====================
async function loadAgentHistory() {
  const box = document.getElementById('agent-history');
  if (!box) return;
  let runs = [];
  try {
    const q = currentTargetId ? `?target_id=${encodeURIComponent(currentTargetId)}` : '';
    runs = await api(`/api/agent/history${q}`);
  } catch (e) { runs = []; }
  if (!runs || !runs.length) {
    box.innerHTML = '';
    return;
  }
  box.innerHTML = runs.map(r => {
    const total = (r.prompt_tokens || 0) + (r.completion_tokens || 0);
    const when = (r.created_at || '').slice(5, 16).replace('T', ' ');
    return `<details class="hist-item">
      <summary class="hist-summary">
        <span class="hist-goal">${escapeHtml((r.goal || '').slice(0, 40))}</span>
        <span class="hist-meta">${when} · ${total} tokens</span>
      </summary>
      <div class="hist-body">${escapeHtml(r.final_answer || '（无结论）')}</div>
    </details>`;
  }).join('');
}

function clearAgentHistory() {
  if (!confirm('确定清空' + (currentTargetId ? '该目标款' : '全部') + '的智能体对话历史？')) return;
  const q = currentTargetId ? `?target_id=${encodeURIComponent(currentTargetId)}` : '';
  api(`/api/agent/history${q}`, { method: 'DELETE' });
  loadAgentHistory();
}

function renderPlanBlock(plan) {
  if (!plan || !plan.steps || !plan.steps.length) return '';
  const done = plan.steps.filter(s => s.status === 'done').length;
  let html = `<div class="trace-step"><div class="rnd">PLAN</div>
    <div class="trace-thought"><span class="trace-label">采购计划</span>${escapeHtml(plan.goal)}
    <span style="opacity:.7">（${done}/${plan.steps.length}）${plan.status === 'done' ? ' · 已全部完成' : ''}</span></div>`;
  for (const s of plan.steps) {
    const mark = PLAN_MARK[s.status] || '⬜';
    const note = s.note ? ` <span style="opacity:.6">${escapeHtml(s.note)}</span>` : '';
    html += `<div class="trace-args">${mark} 步骤${s.index}：${escapeHtml(s.action)}${note}</div>`;
  }
  return html + `</div>`;
}
// ==================== 控制台收起 / 展开 ====================
// 右栏空着的时候仍占 400px，而询价表 6 列在 880px 里会折行。
// 收起后主区直接多出 350px；展开状态记在 localStorage，不用每次点。
const CONSOLE_KEY = 'wb-console-collapsed';

function toggleConsole(force) {
  const con = document.getElementById('agent-console');
  const grid = con && con.closest('.workbench-grid');
  if (!con || !grid) return;
  const collapsed = typeof force === 'boolean' ? force : !con.classList.contains('collapsed');
  con.classList.toggle('collapsed', collapsed);
  grid.classList.toggle('console-collapsed', collapsed);
  try { localStorage.setItem(CONSOLE_KEY, collapsed ? '1' : '0'); } catch (e) {}
}

// 默认收起：第一次进来看到的是满宽主区，竖脊上写着「智能体」，点一下就开
(function initConsoleState() {
  let want = null;
  try { want = localStorage.getItem(CONSOLE_KEY); } catch (e) {}
  toggleConsole(want === null ? true : want === '1');
})();
