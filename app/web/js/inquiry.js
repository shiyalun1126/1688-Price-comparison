/**
 * 询价工作台：话术生成、状态管理、报价解析
 */

// ==================== 询价列表渲染 ====================
// 后台轮询用「内容指纹」判断有没有真变化：没变就完全不动 DOM，
// 否则每 5 秒重绘一次表格，你正在看的话术会被收起、滚动位置会跳。
let lastInqSig = '';
let lastInqMap = {};

function inqSignature(list) {
  return list.map(q => [
    q.product_id, q.status, q.round || 0, (q.gap_labels || []).join(','),
    q.paused ? 1 : 0, q.last_msg_at || '', q.price == null ? '' : q.price,
    q.session_id || '',
  ].join('|')).join(';');
}

function indexInq(list) {
  const m = {};
  for (const q of list) m[q.product_id] = q;
  return m;
}

/** 静默模式下：拿新旧两版比出「谁变了」，用来闪一下行、弹一条提示 */
function diffInq(prevMap, list) {
  const out = [];
  for (const q of list) {
    const p = prevMap[q.product_id];
    if (!p || !q.session_id) continue;      // 第一次见到这条不提示
    const statusChanged = p.status !== q.status;
    const newMsg = (p.last_msg_at || '') !== (q.last_msg_at || '');
    const roundUp = (q.round || 0) > (p.round || 0);
    if (!statusChanged && !newMsg && !roundUp) continue;
    // 卖家说话才会让引擎进入这些状态；sent/following_up 是我们自己刚发出去
    const remind = ['replied', 'negotiating', 'quoted', 'escalated']
      .includes(q.status) && (statusChanged || newMsg);
    out.push({
      product_id: q.product_id, shop_name: q.shop_name, status: q.status,
      session_id: q.session_id, remind,
    });
  }
  return out;
}

/**
 * @param {object} [opts]
 * @param {boolean} [opts.silent] 后台轮询调用：不显示「加载中」，无变化不重绘
 */
async function refreshInquiries(opts = {}) {
  const silent = !!opts.silent;
  const el = document.getElementById('workbench');
  if (!el) return;
  if (!currentTargetId) {
    el.innerHTML = '<p class="muted">请先选择或创建一个目标款</p>';
    inquiries = []; lastInqSig = ''; lastInqMap = {};
    renderInqChips(); updatePipeline();
    return;
  }
  if (!silent) {
    inquiries = null;
    renderWorkbench();
    renderInqChips();
  }
  const data = await api(`/api/targets/${currentTargetId}/inquiries`);
  if (!Array.isArray(data)) {
    // 服务重启/网络抖动时不要把表清空，保留上一次的结果
    if (inquiries === null) { inquiries = []; renderWorkbench(); }
    return;
  }
  const sig = inqSignature(data);
  if (silent && sig === lastInqSig) return;

  const changed = silent ? diffInq(lastInqMap, data) : [];
  inquiries = data;
  lastInqSig = sig;
  lastInqMap = indexInq(data);
  renderWorkbench({ flash: changed.map(c => c.product_id) });
  renderInqChips();
  // 步骤条要额外拉一次商品列表，静默刷新只在询价数/已报价数变了时才算
  if (!silent || changed.length) updatePipeline();
  if (silent) notifyInqChanges(changed);
}

/** 列表里有了新动静：右下角轻提示，点一下直接进对话 */
function notifyInqChanges(changed) {
  // 已经在看这段对话就别再弹提示了
  const hit = changed.filter(c => c.remind && c.session_id !== currentSessionId);
  if (!hit.length) return;
  // 人不在这个标签页时就别弹窗了，改成标题上的未读角标，回来看得见
  if (document.hidden) { markUnread(hit.length); return; }
  const first = hit[0];
  const label = STATUS_CN[first.status] || first.status;
  const more = hit.length > 1 ? ` 等 ${hit.length} 家` : '';
  showInqToast(`${first.shop_name}${more} · ${label}`, first.session_id);
}

function showInqToast(text, sessionId) {
  let box = document.getElementById('inq-toast');
  if (!box) {
    box = document.createElement('div');
    box.id = 'inq-toast';
    box.className = 'inq-toast';
    document.body.appendChild(box);
  }
  const item = document.createElement('div');
  item.className = 'inq-toast-item';
  item.innerHTML = `<span class="dot"></span><span>${escapeHtml(text)}</span>`;
  if (sessionId) {
    item.title = '点开这段对话';
    item.onclick = () => { openSession(sessionId); item.remove(); };
  }
  box.appendChild(item);
  setTimeout(() => item.classList.add('out'), 6000);
  setTimeout(() => item.remove(), 7000);
}

// 标签页在后台时用标题做未读角标：切回来一眼就知道有几家回了话
let unreadCount = 0;
const BASE_TITLE = document.title;

function markUnread(n) {
  unreadCount += n;
  document.title = `(${unreadCount}) 新回复 · ${BASE_TITLE}`;
}

function clearUnread() {
  if (!unreadCount) return;
  unreadCount = 0;
  document.title = BASE_TITLE;
}

function renderInqChips() {
  const el = document.getElementById('inq-chips');
  if (!el) return;
  if (!inquiries || !inquiries.length) {
    el.innerHTML = '<span class="muted" style="font-size:12px">还没有候选商品，先用上面的「找货源」抓一批</span>';
    return;
  }
  el.innerHTML = INQ_FILTERS.map(f => {
    const n = inquiries.filter(f.match).length;
    if (f.key !== 'all' && n === 0) return '';
    const on = inqFilter === f.key;
    return `<button type="button" class="${on ? 'on' : ''}" onclick="pickInqFilter('${f.key}')">${f.label} ${n}</button>`;
  }).join('');
}

/** 在「候选池」点一档 → 下面那张表跟着筛，并滚过去。否则点了像没反应 */
function pickInqFilter(key) {
  inqFilter = key;
  renderInqChips();
  renderWorkbench();
  if (key === 'all') return;
  const zone = document.getElementById('zone-inquiry');
  if (!zone) return;
  if (typeof focusZone === 'function') focusZone(zone);
  else {
    if (typeof unfoldZone === 'function') unfoldZone(zone);
    zone.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// ==================== 按「要不要管它」分组 ====================
// 一张表里混着在谈的、还没开口的、核验不匹配的，候选一多就变成一面墙：
// 抓 20 个款进来，页面直接长出 20 行跟你当下无关的东西。
// 按状态分组、默认只展开「在谈 / 已报价」，其余折成一行小标题。
const INQ_GROUPS = [
  { key: 'active', label: '在谈', hint: '智能体正在跟，需要盯', open: true },
  { key: 'quoted', label: '已报价', hint: '拿到卖家真实报价', open: true },
  { key: 'todo', label: '待询价', hint: '还没开口 · 点开挑要问的', open: false },
  { key: 'done', label: '已收工 / 需人工', hint: '', open: false },
  { key: 'rejected', label: '已排除', hint: '视觉核验不匹配 · 通常不用管', open: false },
];

/** 一条候选只属于一个组：先看核验结论，再看谈判到哪一步 */
function inqGroupOf(q) {
  if ((q.tier || 'unverified') === 'rejected') return 'rejected';
  if (['sending', 'sent', 'following_up', 'negotiating', 'replied'].includes(q.status)) return 'active';
  if (q.status === 'quoted') return 'quoted';
  if (['closed', 'escalated', 'expired'].includes(q.status)) return 'done';
  return 'todo';
}

const INQ_OPEN_KEY = 'wb-inq-groups';
let inqOpen = null;                    // null = 还没读过存档，用默认值

function loadInqOpen() {
  if (inqOpen) return inqOpen;
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(INQ_OPEN_KEY) || 'null'); } catch (e) {}
  if (Array.isArray(saved)) inqOpen = new Set(saved);
  else { inqOpen = new Set(); INQ_GROUPS.forEach(g => { if (g.open) inqOpen.add(g.key); }); }
  return inqOpen;
}

function toggleInqGroup(key) {
  const open = loadInqOpen();
  if (open.has(key)) open.delete(key); else open.add(key);
  try { localStorage.setItem(INQ_OPEN_KEY, JSON.stringify([...open])); } catch (e) {}
  renderWorkbench();
}

const INQ_TIER_CN = { exact: '完全同款', equivalent: '等效替代', unverified: '未核验', rejected: '已排除' };
const INQ_TIER_STYLE = {
  exact: { bg: 'var(--accent, #b8860b)', fg: '#fff' },
  equivalent: { bg: 'transparent', fg: 'var(--accent-deep, #8a6508)', bd: '1px solid var(--accent, #b8860b)' },
  unverified: { bg: 'transparent', fg: 'var(--muted, #888)', bd: '1px solid var(--border, #ccc)' },
  rejected: { bg: 'transparent', fg: 'var(--muted, #888)', bd: '1px dashed var(--border, #ccc)' }
};

function tierBadge(q) {
  const out = [];
  const mk = (bg, fg, bd, label, title) =>
    `<span title="${title}" style="display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;line-height:1.7;white-space:nowrap;background:${bg};color:${fg};border:${bd || 'none'};vertical-align:middle">${label}</span>`;
  const t = q.tier || 'unverified';
  if (t === 'exact' || t === 'equivalent') {
    const s = INQ_TIER_STYLE[t];
    out.push(mk(s.bg, s.fg, s.bd, INQ_TIER_CN[t], '视觉核验分层，与比价结论一致'));
  }
  // 只有真正收工（quoted）才打「已报价」；追问/议价中途虽然已有价格，
  // 但那个价还不是最终成交价，标成「已报价」会跟状态列打架
  if (q.price_source === 'quoted' && q.status === 'quoted') {
    out.push(mk('var(--leaf-soft, #dcefe2)', 'var(--leaf-deep, #2e7d52)', '1px solid var(--leaf-deep, #2e7d52)',
      '已报价', '卖家已回复真实报价，价格经询价核验'));
  } else if (t !== 'exact' && t !== 'equivalent') {
    const s = INQ_TIER_STYLE[t] || INQ_TIER_STYLE.unverified;
    out.push(mk(s.bg, s.fg, s.bd, INQ_TIER_CN[t] || '未核验', '视觉核验分层，与比价结论一致'));
  }
  return out.join(' ');
}

function tierPrice(q) {
  if (q.price == null || q.price <= 0) return '<span class="muted" title="未抓到有效标价，可重新从 1688 导入或手动补价">未抓到价</span>';
  const src = q.price_source === 'quoted' ? '<span class="muted" style="font-size:11px">报价</span>' : '<span class="muted" style="font-size:11px">标价</span>';
  return `<span style="font-variant-numeric:tabular-nums">¥${Number(q.price).toFixed(2)}</span> ${src}`;
}

/** 一行候选。抽出来是因为现在按组渲染，同一段 HTML 要被多处拼接 */
function inqRowHtml(q) {
  const st = STATUS_CN[q.status] || q.status;
  const thumb = q.image
    ? `<img class="p-thumb" src="${escapeAttr(q.image)}" alt="" loading="lazy">`
    : '<span class="p-thumb" style="display:inline-block"></span>';
  const titleCell = `<div class="p-cell">${thumb}<span>${escapeHtml(q.title || '')}</span></div>`;
  let cell = '';
  let actions = '';
  if (q.status === 'pending') {
    cell = '<span class="muted">—</span>';
    actions = `<button class="btn default sm" onclick="makeInquiry('${q.product_id}')">生成话术</button>`;
  } else {
    cell = `<details class="script-fold"><summary>${q.status === 'quoted' ? '报价已到 · 点开查看话术 / 回复' : '点开查看话术'}</summary><pre>${escapeHtml(q.script || '')}</pre></details>`;
    // 自动谈判进度：第几轮、还缺什么、是否被暂停
    if (q.session_id) {
      const gaps = q.gap_labels || [];
      const gapTxt = gaps.length ? ` · 缺 <em>${gaps.length}</em> 项` : '';
      const pauseTxt = q.paused ? ' · 已暂停' : '';
      const tip = gaps.length ? `还缺：${gaps.join('、')}` : '';
      cell += `<span class="inq-progress" title="${escapeAttr(tip)}">第 ${q.round || 0} 轮${gapTxt}${pauseTxt}</span>`;
    }

    const chatBtn = q.session_id
      ? `<button class="btn default sm" onclick="openSessionForInquiry('${q.id}')">对话</button>` : '';
    const pauseBtn = q.session_id
      ? `<button class="btn ghost sm" onclick="quickPause('${q.session_id}', ${q.paused ? 'false' : 'true'})">${q.paused ? '继续' : '暂停'}</button>` : '';

    if (q.status === 'draft') {
      actions = `<button class="btn default sm" onclick="copyScript('${q.id}')">复制</button>
                 <button class="btn primary sm" onclick="autoInquiry('${q.id}')">自动询价</button>
                 <button class="btn ghost sm" onclick="setStatus('${q.id}', 'sent')">标记已发送</button>`;
    } else if (q.status === 'sent') {
      actions = `${chatBtn}
                 <button class="btn default sm" onclick="replyBox('${q.id}')">贴入回复</button>
                 ${pauseBtn}`;
    } else if (q.status === 'sending' || q.status === 'following_up' || q.status === 'negotiating') {
      actions = `${chatBtn}${pauseBtn}`;
    } else if (q.status === 'replied') {
      actions = `${chatBtn}<button class="btn default sm" onclick="replyBox('${q.id}')">贴入回复</button>`;
    } else {
      actions = chatBtn || '<span class="muted">已完成</span>';
    }
  }
  return `<tr data-inq="${escapeAttr(q.product_id)}"><td>${tierBadge(q)} ${escapeHtml(q.shop_name || '')}</td><td>${titleCell}</td>`
    + `<td style="white-space:nowrap">${tierPrice(q)}</td>`
    + `<td><span class="status ${q.status}">${st}</span></td>`
    + `<td>${cell}</td><td style="white-space:nowrap">${actions}</td></tr>`;
}

function renderWorkbench(opts = {}) {
  const el = document.getElementById('workbench');
  if (!el) return;
  if (inquiries === null) { el.innerHTML = '<div class="empty" style="opacity:.6">加载中…</div>'; return; }
  if (!inquiries.length) { el.innerHTML = '<div class="empty">暂无候选商品。先在「02 候选商品」添加，或从 1688 书签导入。</div>'; return; }
  const kw = (document.getElementById('inquiry-search')?.value || '').trim().toLowerCase();
  let rows = inquiries;
  const f = INQ_FILTERS.find(x => x.key === inqFilter);
  if (f && f.key !== 'all') rows = rows.filter(f.match);
  if (kw) rows = rows.filter(q => (q.shop_name || '').toLowerCase().includes(kw) || (q.title || '').toLowerCase().includes(kw));
  if (!rows.length) {
    el.innerHTML = `<div class="empty">没有匹配「${escapeHtml(kw) || INQ_FILTERS.find(x => x.key === inqFilter)?.label || ''}」的条目，<a href="#" onclick="inqFilter='all';document.getElementById('inquiry-search').value='';renderInqChips();renderWorkbench();return false;" style="color:var(--accent-deep,var(--accent))">清除筛选</a></div>`;
    return;
  }
  // 有筛选/搜索时把命中的组全部摊开：否则点「已排除 54」只会看到一行折叠标题，像坏了
  const filtering = !!(kw || (f && f.key !== 'all'));
  const openSet = loadInqOpen();
  // 一组都没展开时（比如刚抓完货，全是「待询价」）自动摊开第一组，
  // 否则页面只剩两行小标题，看着像没数据。但只在总量小的时候这么做——
  // 一次抓 40 个款进来再全摊开，等于把刚收起来的那面墙又立回去。
  const autoExpand = rows.length <= 10;
  const hasVisible = INQ_GROUPS.some(g => (filtering || openSet.has(g.key)) && rows.some(q => inqGroupOf(q) === g.key));
  const autoKey = (autoExpand && !hasVisible)
    ? (INQ_GROUPS.find(g => rows.some(q => inqGroupOf(q) === g.key)) || {}).key : null;

  let html = '';
  if (filtering) {
    const label = kw ? `搜索「${escapeHtml(kw)}」` : (f ? f.label : '');
    html += `<div class="inq-filter-bar"><span>当前只看 <b>${label}</b> · ${rows.length} 条</span>`
      + `<button type="button" onclick="inqFilter='all';document.getElementById('inquiry-search').value='';renderInqChips();renderWorkbench()">显示全部</button></div>`;
  }
  html += '<div class="wb-scroll"><table><thead><tr><th>店铺</th><th>商品</th><th>核验 · 价格</th><th>状态</th><th>话术 / 回复</th><th>操作</th></tr></thead><tbody>';
  for (const g of INQ_GROUPS) {
    const groupRows = rows.filter(q => inqGroupOf(q) === g.key);
    if (!groupRows.length) continue;
    const open = filtering || openSet.has(g.key) || g.key === autoKey;
    html += `<tr class="grp-row" data-grp="${g.key}"><td colspan="6">`
      + `<button type="button" class="grp-btn" onclick="toggleInqGroup('${g.key}')" title="${open ? '收起' : '展开'}这一组">`
      + `<span class="grp-caret">${open ? '▾' : '▸'}</span>`
      + `<span class="grp-label">${g.label}</span>`
      + `<span class="grp-count">${groupRows.length}</span>`
      + (g.hint ? `<span class="grp-hint">${g.hint}</span>` : '')
      + '</button></td></tr>';
    if (!open) continue;
    for (const q of groupRows) html += inqRowHtml(q);
  }
  html += '</tbody></table></div>';

  // 重绘会把滚动位置和展开的话术清掉，后台轮询时尤其明显；先记下来再还原
  const prevScroll = el.querySelector('.wb-scroll')?.scrollTop || 0;
  const openRows = new Set([...el.querySelectorAll('tr[data-inq]')]
    .filter(tr => tr.querySelector('details.script-fold[open]'))
    .map(tr => tr.dataset.inq));

  el.innerHTML = html;

  const sc = el.querySelector('.wb-scroll');
  if (sc && prevScroll) sc.scrollTop = prevScroll;
  if (openRows.size) {
    el.querySelectorAll('tr[data-inq]').forEach(tr => {
      if (openRows.has(tr.dataset.inq)) {
        const d = tr.querySelector('details.script-fold');
        if (d) d.open = true;
      }
    });
  }
  // 有新消息的那行闪一下，让你知道变化发生在哪，而不是整张表静悄悄地换了
  if (opts.flash && opts.flash.length) {
    const ids = new Set(opts.flash);
    el.querySelectorAll('tr[data-inq]').forEach(tr => {
      if (ids.has(tr.dataset.inq)) tr.classList.add('row-flash');
    });
  }
}

// ==================== 询价操作 ====================
async function makeInquiry(productId) {
  const qty = prompt('拟采购数量（件，可留空）：', '');
  const body = { target_style_id: currentTargetId, product_id: productId };
  if (qty && parseInt(qty)) body.buyer_quantity = parseInt(qty);
  await api('/api/inquiries', { method: 'POST', body: JSON.stringify(body) });
  refreshInquiries();
}

async function copyScript(id) {
  const q = inquiries.find(x => x.id === id);
  if (!q) return;
  try { await navigator.clipboard.writeText(q.script); alert('话术已复制'); }
  catch (e) { prompt('复制以下话术：', q.script); }
}

async function setStatus(id, status) {
  await api(`/api/inquiries/${id}/status`, { method: 'POST', body: JSON.stringify({ status }) });
  refreshInquiries();
}

async function replyBox(id) {
  const reply = prompt('粘贴卖家回复原文：', '');
  if (!reply) return;
  const r = await api(`/api/inquiries/${id}/parse-reply`, { method: 'POST', body: JSON.stringify({ reply }) });
  if (r.quote) {
    const tax = r.quote.tax === 'included' ? '含税' : (r.quote.tax === 'excluded' ? '不含税' : '含税未知');
    const stock = (STOCK_CN[r.quote.stock_status] || '现货未知');
    const lead = fmtLeadTime(r.quote.lead_time_hours);
    alert(`已解析报价：${r.quote.price} 元 / MOQ ${r.quote.moq ?? '-'} / ${tax} / ${stock}${lead ? ' / ' + lead : ''}`);
  }
  refreshInquiries();
}