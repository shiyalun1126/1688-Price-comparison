/**
 * 自动谈判：引擎状态条 + 会话抽屉（实时对话流、人工接管）
 *
 * 设计原则：把「智能体在跟卖家聊什么」完全暴露给用户看。
 * 隐藏过程的黑箱最让人不敢用，所以每一句、每个判定、每次失败都摆在明面上，
 * 并且随时能暂停接管。
 */

let currentSessionId = null;
let sessionTimer = null;
let lastRenderedCount = 0;
let autoListOpen = false;

// ==================== 引擎状态条 ====================
const ENGINE_STATE_CN = {
  sending: '发送中', sent: '已发出', following_up: '追问中',
  negotiating: '议价中', replied: '已回复', quoted: '已报价',
  escalated: '需人工', closed: '已结束', expired: '已超时',
};

async function refreshAutoBar() {
  const bar = document.getElementById('auto-bar');
  const warn = document.getElementById('auto-warn');
  if (!bar) return;
  const s = await api('/api/inquiry-engine');
  if (!s || s.error) {
    bar.innerHTML = '<span class="auto-dot idle"></span><span>自动谈判引擎未就绪</span>';
    return;
  }
  const active = s.active || 0;
  const pausedN = s.paused || 0;
  const blocked = (s.blocked_reason || '').trim();
  // 全被暂停时点灰灯：引擎在转，但一条也不会发出去
  const idle = !active || pausedN >= active;
  const dotCls = blocked ? 'blocked' : (idle ? 'idle' : '');

  let chips = '';
  for (const key of ['negotiating', 'following_up', 'sent', 'escalated']) {
    const n = (s.by_status || {})[key];
    if (n) chips += `<span>${ENGINE_STATE_CN[key]} <b>${n}</b></span>`;
  }

  bar.classList.toggle('open', autoListOpen);
  bar.innerHTML = `
    <button type="button" class="auto-toggle" id="auto-toggle"
            aria-expanded="${autoListOpen}" aria-controls="auto-pop"
            title="点开看是哪几家在谈、各自说到哪一步" onclick="toggleAutoList()">
      <span class="auto-dot ${dotCls}"></span>
      <span>自动谈判 <b>${active}</b> 家未收工</span>
      ${pausedN ? `<span class="auto-paused">其中 ${pausedN} 家已暂停</span>` : ''}
      <span class="auto-toggle-caret">›</span>
    </button>
    ${chips}
    <span class="spacer"></span>
    <span>通道 <b>${escapeHtml(s.channel || 'cdp')}</b></span>
    <span>今日剩余配额 <b>${s.quota_left == null ? '-' : s.quota_left}</b></span>`;

  if (warn) {
    if (blocked) {
      warn.style.display = '';
      warn.innerHTML = `自动谈判已暂停：${escapeHtml(blocked)}
        <button class="btn default sm" style="margin-left:8px"
          onclick="resumeEngine()">处理完了，恢复</button>`;
    } else {
      warn.style.display = 'none';
      warn.innerHTML = '';
    }
  }
}

async function resumeEngine() {
  const r = await api('/api/inquiry-engine/resume', { method: 'POST', body: '{}' });
  if (r && r.error) { alert(r.error); return; }
  await refreshAutoBar();
  await refreshInquiries();
}

// ==================== 在谈名单（点状态条展开） ====================
// 状态条只给聚合数，想问「到底是哪几家、说到哪一句」得点开。
// 每行点进去就是完整对话抽屉，不用再去候选池里翻。
async function toggleAutoList(force) {
  autoListOpen = typeof force === 'boolean' ? force : !autoListOpen;
  const pop = document.getElementById('auto-pop');
  const bar = document.getElementById('auto-bar');
  if (bar) bar.classList.toggle('open', autoListOpen);
  const btn = document.getElementById('auto-toggle');
  if (btn) btn.setAttribute('aria-expanded', String(autoListOpen));
  if (!pop) return;
  pop.hidden = !autoListOpen;
  if (autoListOpen) await loadAutoList();
}

function closeAutoList() { toggleAutoList(false); }

async function loadAutoList() {
  const pop = document.getElementById('auto-pop');
  if (!pop) return;
  pop.innerHTML = '<div class="auto-pop-empty">读取中…</div>';
  const d = await api('/api/sessions');
  if (!d || d.error || !Array.isArray(d.sessions)) {
    pop.innerHTML = '<div class="auto-pop-empty">读取失败，稍后再试</div>';
    return;
  }
  if (!d.sessions.length) {
    pop.innerHTML = '<div class="auto-pop-empty">现在没有在谈的会话。点上面「⚡ 自动询价」发起一批。</div>';
    return;
  }
  const rows = d.sessions.map(s => {
    const paused = !!s.paused;
    const stateCn = paused ? '已暂停' : (ENGINE_STATE_CN[s.status] || s.status);
    const cls = paused ? 'paused'
      : s.status === 'escalated' ? 'warn'
      : (s.status === 'negotiating' || s.status === 'following_up') ? 'live' : '';
    const who = s.last_role === 'seller' ? '卖家：'
      : (s.last_role === 'buyer' ? '我方：' : '系统：');
    let lastText = (s.last_text || '').trim();
    if (lastText.length > 46) lastText = lastText.slice(0, 44) + '…';
    const lastLine = lastText ? who + lastText : '还没有消息';
    return `<button type="button" class="apl-row" onclick="closeAutoList(); openSession('${escapeAttr(s.id)}')">
      <span class="apl-main">
        <span class="apl-line1">
          <b class="apl-shop">${escapeHtml(s.shop_name || '')}</b>
          <span class="apl-chip ${cls}">${escapeHtml(stateCn || '')}</span>
          <span class="apl-round">第 ${s.round || 0} 轮</span>
          <span class="apl-when">${timeAgo(s.last_msg_at || s.updated_at)}</span>
        </span>
        <span class="apl-title">${escapeHtml(s.title || '')}</span>
        <span class="apl-last${lastText ? '' : ' empty'}">${escapeHtml(lastLine)}</span>
      </span>
      <span class="apl-go">打开对话 ›</span>
    </button>`;
  }).join('');
  pop.innerHTML = `
    <div class="auto-pop-head">
      <b>在谈的 ${d.total} 家</b>
      <span class="auto-pop-note">不限当前目标款</span>
      ${d.paused ? `<span class="auto-pop-note">· ${d.paused} 家已暂停，点进去可恢复</span>` : ''}
      <span class="spacer"></span>
      <button type="button" class="btn ghost sm" onclick="closeAutoList()">收起</button>
    </div>
    <div class="auto-pop-list">${rows}</div>`;
}

// 点空白处收起名单
document.addEventListener('click', e => {
  if (!autoListOpen) return;
  if (e.target.closest && (e.target.closest('#auto-pop') || e.target.closest('#auto-toggle'))) return;
  closeAutoList();
});

// ==================== 发起自动谈判 ====================
// channel: 'cdp' = 真的发到你已登录的 1688；'mock' = 剧本卖家，离线演示。
// 分开两个入口是有意的：演示通道不能碰真实账号，真实通道不能假装有卖家秒回。
async function startAutoInquiry(channel) {
  if (!currentTargetId) { alert('请先选择目标款'); return; }
  const mock = channel === 'mock';
  const go = confirm(mock
    ? '用「剧本卖家」演示一遍完整谈判？\n\n'
      + '假卖家会按真实节奏回话：先只报价，再补税费运费，最后让一口价。\n'
      + '不打开真实 1688、不消耗配额、不会给任何老板发消息。\n\n'
      + '你会看到智能体每一步的决策理由（会话抽屉里的「决策」行）。'
    : '对当前目标款发起自动询价？\n\n'
      + '智能体会用你已登录的 1688 账号，逐个打开店铺会话：\n'
      + '① 发开场询价 ② 缺什么补问什么 ③ 拿市场价回头议价\n\n'
      + '只对已核验为「完全同款 / 等效替代」的候选发起；'
      + '被排除的、以及只有图相似还没核验的，都不去打扰。');
  if (!go) return;
  const r = await api(`/api/targets/${currentTargetId}/inquiries/auto`,
    { method: 'POST', body: JSON.stringify({ create_missing: true, channel: channel || '' }) });
  if (r && r.error) { alert(r.error); return; }
  const fail = (r['失败'] || []).length;
  alert(`已入队 ${r['已入队'] || 0} 个会话`
    + (r['新建询价任务'] ? `，顺带新建 ${r['新建询价任务']} 条询价任务` : '')
    + (r['跳过'] ? `，跳过 ${r['跳过']} 个（已在进行或已报价）` : '')
    + (r['候选池'] !== undefined ? `\n可询价候选：${r['候选池']} 家（已核验同款/等效）` : '')
    + (r['未核验跳过'] ? `\n另有 ${r['未核验跳过']} 个候选只是图相似、还没核验，没有去问——先在视觉匹配里判定它是不是这款。` : '')
    + (fail ? `\n${fail} 个失败：${(r['失败'][0] || {}).error || ''}` : '')
    + `\n今日剩余配额：${r['今日剩余配额'] ?? '-'}`);
  await refreshAutoBar();
  await refreshInquiries();
}

async function autoInquiry(inquiryId) {
  const r = await api(`/api/inquiries/${inquiryId}/auto`, { method: 'POST', body: '{}' });
  if (r && r.error) { alert(r.error); return; }
  if (r.already_running) { openSession(r.session_id); return; }
  await refreshAutoBar();
  await refreshInquiries();
  if (r.session_id) openSession(r.session_id);
}

// ==================== 会话抽屉 ====================
function openSessionForInquiry(inquiryId) {
  const q = (inquiries || []).find(x => x.id === inquiryId);
  if (q && q.session_id) openSession(q.session_id);
  else alert('这条询价还没有自动会话（先点「自动询价」）');
}

async function openSession(sessionId) {
  currentSessionId = sessionId;
  lastRenderedCount = 0;
  document.getElementById('drawer-mask')?.classList.add('on');
  document.getElementById('session-drawer')?.classList.add('on');
  await refreshSession();
  if (sessionTimer) clearInterval(sessionTimer);
  // 打开时轮询，页面不可见就跳过，避免无意义请求
  sessionTimer = setInterval(() => {
    if (currentSessionId && !document.hidden) refreshSession();
  }, 3000);
}

function closeSession() {
  currentSessionId = null;
  document.getElementById('drawer-mask')?.classList.remove('on');
  document.getElementById('session-drawer')?.classList.remove('on');
  if (sessionTimer) { clearInterval(sessionTimer); sessionTimer = null; }
}

async function refreshSession() {
  if (!currentSessionId) return;
  const d = await api(`/api/sessions/${currentSessionId}`);
  if (!d || d.error) return;
  renderSession(d);
}

function renderSession(d) {
  const s = d.session || {};
  const msgs = d.messages || [];

  const title = document.getElementById('sd-title');
  const sub = document.getElementById('sd-sub');
  const meta = document.getElementById('sd-meta');
  const log = document.getElementById('sd-log');
  const hint = document.getElementById('sd-hint');
  const pauseBtn = document.getElementById('sd-pause');
  if (!title || !log) return;

  title.textContent = s.shop_name || '卖家会话';
  sub.textContent = s.title || '';
  sub.style.display = s.title ? '' : 'none';

  const stageCn = s.stage === 'bargain' ? '议价' : '询价';
  const statusCn = ENGINE_STATE_CN[s.status] || s.status;
  const gapCn = (s.gap_labels || []).join('、');
  meta.innerHTML = `
    <span>${stageCn}阶段</span>
    <span>第 ${s.round || 0} 轮</span>
    <span>${escapeHtml(statusCn || '')}</span>
    ${gapCn ? `<span class="warn">还缺：${escapeHtml(gapCn)}</span>` : ''}
    ${s.market_min ? `<span>市场最低 ¥${Number(s.market_min).toFixed(2)}</span>` : ''}
    ${s.error ? `<span class="warn">${escapeHtml(s.error.slice(0, 40))}</span>` : ''}`;

  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  log.innerHTML = msgs.map(m => {
    const role = m.role === 'seller' ? 'seller' : (m.role === 'buyer' ? 'buyer' : 'system');
    const t = m.ts ? new Date(m.ts) : null;
    const hhmm = t ? `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}` : '';
    if (m.kind === 'decision') {
      return `<div class="bubble decision"><span class="decision-tag">决策</span>${escapeHtml(m.text)}</div>`;
    }
    if (role === 'system') {
      return `<div class="bubble system">${escapeHtml(m.text)}</div>`;
    }
    const who = role === 'seller' ? '卖家' : '我方（智能体）';
    return `<div class="bubble ${role}">${escapeHtml(m.text)}
      <span class="ts">${who} · ${hhmm}</span></div>`;
  }).join('') || '<p class="muted" style="font-size:12px">还没有消息</p>';

  if (msgs.length !== lastRenderedCount || atBottom) {
    log.scrollTop = log.scrollHeight;
    lastRenderedCount = msgs.length;
  }

  if (pauseBtn) {
    pauseBtn.textContent = s.paused ? '恢复自动' : '暂停自动';
    pauseBtn.dataset.paused = s.paused ? '1' : '0';
  }
  if (hint) {
    hint.textContent = s.channel === 'mock'
      ? '当前是模拟通道（假卖家），用来验证谈判逻辑，不会真的发消息到 1688。'
      : '你在这里发的消息会以你的身份发到旺旺，发送后自动谈判会暂停，交回你手上。';
  }
}

async function togglePause() {
  if (!currentSessionId) return;
  const btn = document.getElementById('sd-pause');
  const wantPaused = btn?.dataset.paused !== '1';
  const r = await api(`/api/sessions/${currentSessionId}/pause`,
    { method: 'POST', body: JSON.stringify({ paused: wantPaused }) });
  if (r && r.error) { alert(r.error); return; }
  await refreshSession();
  await refreshAutoBar();
}

/** 列表里直接暂停/继续，不必先打开抽屉 */
async function quickPause(sessionId, paused) {
  const r = await api(`/api/sessions/${sessionId}/pause`,
    { method: 'POST', body: JSON.stringify({ paused: !!paused }) });
  if (r && r.error) { alert(r.error); return; }
  await refreshInquiries();
  await refreshAutoBar();
}

async function sendInject() {
  if (!currentSessionId) return;
  const input = document.getElementById('sd-input');
  const text = (input?.value || '').trim();
  if (!text) return;
  const r = await api(`/api/sessions/${currentSessionId}/inject`,
    { method: 'POST', body: JSON.stringify({ text }) });
  if (r && r.error) { alert('发送失败：' + r.error); return; }
  input.value = '';
  await refreshSession();
  await refreshAutoBar();
  await refreshInquiries();
}

// ==================== 初始化 ====================
// 后台轮询：卖家回话、引擎推进都由服务端的线程在做，页面这边只负责把变化取回来。
// 5 秒一次、页面不可见就跳过；列表没变化时完全不重绘，所以不会闪、不会打断操作。
// 打开抽屉后另有 3 秒的会话轮询，这里只管「列表 + 引擎状态」。
let liveTimer = null;
let liveTick = 0;

function startLivePolling() {
  if (liveTimer) clearInterval(liveTimer);
  liveTimer = setInterval(async () => {
    liveTick++;
    // 标签页在后台也继续跟，只是降频到 10 秒：卖家回话不会因为你切走就丢
    if (document.hidden && liveTick % 2) return;
    await refreshAutoBar();
    // 抓取浏览器可能被关掉/刚起起来，15 秒跟一次就够（本地端口探测，很轻）
    if (liveTick % 3 === 0 && typeof refreshBrowserStatus === 'function') refreshBrowserStatus();
    if (currentTargetId) await refreshInquiries({ silent: true });
  }, 5000);
}

// 切回这个标签页时立刻补一次，顺手清掉标题上的未读角标
document.addEventListener('visibilitychange', () => {
  if (document.hidden) return;
  clearUnread();
  refreshAutoBar();
  if (currentTargetId) refreshInquiries({ silent: true });
});

document.addEventListener('DOMContentLoaded', () => {
  refreshAutoBar();
  startLivePolling();
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && currentSessionId) closeSession();
  });
});
