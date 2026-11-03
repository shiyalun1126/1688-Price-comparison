/**
 * 询价工作台：话术生成、状态管理、报价解析
 */

// ==================== 询价列表渲染 ====================
async function refreshInquiries() {
  const el = document.getElementById('workbench');
  if (!currentTargetId) { el.innerHTML = '<p class="muted">请先选择或创建一个目标款</p>'; inquiries = []; renderInqChips(); updatePipeline(); return; }
  inquiries = null;
  renderWorkbench();
  renderInqChips();
  inquiries = await api(`/api/targets/${currentTargetId}/inquiries`);
  if (!Array.isArray(inquiries)) inquiries = [];
  renderWorkbench();
  renderInqChips();
  updatePipeline();
}

function renderInqChips() {
  const el = document.getElementById('inq-chips');
  if (!el) return;
  if (!inquiries || !inquiries.length) { el.innerHTML = ''; return; }
  el.innerHTML = INQ_FILTERS.map(f => {
    const n = inquiries.filter(f.match).length;
    if (f.key !== 'all' && n === 0) return '';
    const on = inqFilter === f.key;
    return `<button type="button" class="${on ? 'on' : ''}" onclick="inqFilter='${f.key}';renderInqChips();renderWorkbench()">${f.label} ${n}</button>`;
  }).join('');
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
  if (q.price_source === 'quoted') {
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

function renderWorkbench() {
  const el = document.getElementById('workbench');
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
  let html = '<div class="wb-scroll"><table><thead><tr><th>店铺</th><th>商品</th><th>核验 · 价格</th><th>状态</th><th>话术 / 回复</th><th>操作</th></tr></thead><tbody>';
  for (const q of rows) {
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
      if (q.status === 'draft') {
        actions = `<button class="btn default sm" onclick="copyScript('${q.id}')">复制</button>
                   <button class="btn ghost sm" onclick="setStatus('${q.id}', 'sent')">标记已发送</button>`;
      } else if (q.status === 'sent') {
        actions = `<button class="btn default sm" onclick="replyBox('${q.id}')">贴入回复</button>
                   <button class="btn default sm" onclick="setStatus('${q.id}', 'closed')">关闭</button>`;
      } else if (q.status === 'replied' || q.status === 'quoted' || q.status === 'closed') {
        actions = '<span class="muted">已完成</span>';
      }
    }
    html += `<tr><td>${tierBadge(q)} ${escapeHtml(q.shop_name || '')}</td><td>${titleCell}</td>`
      + `<td style="white-space:nowrap">${tierPrice(q)}</td>`
      + `<td><span class="status ${q.status}">${st}</span></td>`
      + `<td>${cell}</td><td style="white-space:nowrap">${actions}</td></tr>`;
  }
  html += '</tbody></table></div>';
  if (kw || (f && f.key !== 'all')) html += `<p class="muted" style="margin:8px 2px 0;font-size:12px">命中 ${rows.length} / ${inquiries.length} 条（列表默认按视觉核验分层与价格排序）</p>`;
  el.innerHTML = html;
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