/**
 * 匹配与比价：视觉匹配、比价结论生成、导出
 */

// ==================== 比价行排序 ====================
// 匹配度高的排上面：完全同款 > 等效替代 > 未核验 > 已排除；
// 同层级内可硬比价的在前，再按价格升序（最低同款高亮行因此落在同款区顶部）
const TIER_RANK = { exact: 0, equivalent: 1, unverified: 2, rejected: 3 };
function sortCmpRows(rows) {
  return (rows || []).slice().sort((a, b) => {
    const ta = TIER_RANK[a.tier] ?? 9, tb = TIER_RANK[b.tier] ?? 9;
    if (ta !== tb) return ta - tb;
    const ca = a.comparable ? 0 : 1, cb = b.comparable ? 0 : 1;
    if (ca !== cb) return ca - cb;
    const pa = (a.price == null ? Infinity : Number(a.price));
    const pb = (b.price == null ? Infinity : Number(b.price));
    return pa - pb;
  });
}

// ==================== 匹配 ====================
async function runMatch() {
  if (!currentTargetId) return alert('先选择目标款');
  const btn = document.getElementById('btn-match');
  const box = document.getElementById('progress');
  const bar = document.getElementById('progress-bar');
  const text = document.getElementById('progress-text');

  btn.disabled = true;
  btn.textContent = '匹配中…';
  box.style.display = 'block';
  bar.style.width = '5%';
  text.textContent = '正在准备…';

  let pct = 5;
  const timer = setInterval(() => {
    if (pct < 90) {
      pct += Math.max(0.6, (90 - pct) * 0.04);
      if (pct > 90) pct = 90;
      bar.style.width = pct.toFixed(1) + '%';
      text.textContent = `视觉比对中… ${pct.toFixed(0)}%（并发匹配，请稍候）`;
    }
  }, 400);

  let r;
  try {
    r = await api(`/api/targets/${currentTargetId}/match`, { method: 'POST' });
  } finally {
    clearInterval(timer);
    btn.disabled = false;
    btn.textContent = '执行视觉匹配';
  }

  if (r && r.error) {
    box.style.display = 'none';
    alert('匹配失败：' + r.error);
    return;
  }

  bar.style.width = '100%';
  const st = r.stats || {};
  text.textContent = `完成：本次匹配 ${st.matched_now ?? '?'} 个，复用 ${st.reused ?? 0} 个，耗时 ${st.elapsed_sec ?? '?'} 秒`;
  setTimeout(() => { box.style.display = 'none'; bar.style.width = '0%'; }, 6000);

  lastMatchResult = r;
  document.getElementById('btn-export').disabled = false;
  renderMatchResult(r);
  updatePipeline();
}

// 把匹配结果画成四组。抽成独立函数：既供 runMatch 调用，也能拿库里已有的
// 结果直接重绘（复核版面、截图回归时不必再烧一次视觉模型）。
function renderMatchResult(r) {
  let html = '';
  const card = (m, cls) => {
    const img = m.product && m.product.image_paths && m.product.image_paths.length
      ? `<img src="${m.product.image_paths[0]}" alt="">` : '';
    const price = m.product && m.product.listed_price != null && m.product.listed_price > 0
      ? `<span class="price">¥${Number(m.product.listed_price).toFixed(2)}</span>`
      : '<span class="muted">未抓到价</span>';
    return `<div class="match-card">
      ${img}
      <div class="info">
        <div class="shop">${escapeHtml(m.shop_name || m.product_id || '')}${price}</div>
        <div class="tag ${cls}">${m.status}</div>
        <div class="reason">${escapeHtml(m.reason || '')}</div>
      </div>
    </div>`;
  };
  // 已排除/未核验往往是几十上百条，平铺会把页面拉成几屏，真正要看的同款被顶掉。
  // 这两组默认收成一行摘要，点开才展开（同款与替代保持全量可见，它们才是决策依据）。
  const render = (title, list, cls, opt = {}) => {
    if (!list.length) return '';
    const head = `<h3>${title} <span class="n">${list.length}</span>`
      + (opt.fold ? `<span class="fold-hint">${opt.hint || ''}</span>` : '') + '</h3>';
    const cards = list.map(m => card(m, cls)).join('');
    if (opt.fold) {
      return `<details class="match-col fold"><summary>${head}</summary>`
        + `<div class="cards">${cards}</div></details>`;
    }
    return `<div class="match-col">${head}${cards}</div>`;
  };
  html += render('完全同款', r.exact, 'exact');
  html += render('等效替代', r.equivalent, 'equiv');
  html += render('已排除', r.rejected, 'other',
                 { fold: true, hint: '· 都已判为不是这款，点开可复核' });
  html += render('未核验候选', r.candidate, 'other',
                 { fold: true, hint: '· 还没判定，点开查看' });
  document.getElementById('result').innerHTML = html || '<div class="empty">无匹配结果</div>';
}

// ==================== 比价 ====================
async function runComparison(force = false) {
  if (!currentTargetId) return alert('先选择目标款');
  let btn = null;
  if (force) {
    btn = document.querySelector('#result .rebtn');
    if (btn) { btn.disabled = true; btn.textContent = '重算中…'; }
  }
  try {
    const r = await api(`/api/targets/${currentTargetId}/comparison${force ? '?refresh=true' : ''}`, { method: 'POST' });
    if (r.error) { alert('生成失败：' + r.error); return; }
    renderComparison(r);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '重算结论'; }
  }
}

function renderComparison(r) {
  let html = `<div class="cmp-summary"><div class="trace-label" style="color:var(--accent-deep)">综述</div><p style="margin:4px 0 0;font-size:13px;line-height:1.8">${r.summary || ''}</p></div>`;
  const gen = fmtDateTime(r.generated_at);
  const genAge = priceAge(r.generated_at);
  if (gen) {
    const genTip = genAge && genAge.cls ? '，输入数据未变化时结论沿用缓存' : '';
    html += `<div class="cmp-meta"><span>结论生成于 ${gen}`
      + (genAge && genAge.cls === 'age-warn' ? '<span class="age age-warn" title="结论较早，若数据有更新请重算">7天前</span>' : '')
      + (genAge && genAge.cls === 'age-stale' ? '<span class="age age-stale" title="结论较早，建议重算">30天前</span>' : '')
      + `</span><button type="button" class="rebtn" onclick="runComparison(true)" title="跳过缓存强制重算（含议价话术）">重算结论</button></div>`;
  }

  if (r.rows && r.rows.length) {
    html += `<div class="cmp-scroll"><table><thead><tr><th>店铺</th><th>商品</th><th>价格</th><th>来源</th><th>匹配</th>`
      + `<th>可比性</th><th>成交条件</th><th>备注</th></tr></thead><tbody>`;
    let bestMarked = false;
    const sortedRows = sortCmpRows(r.rows);
    html += sortedRows.map(x => {
      const price = (x.price === null || x.price === undefined) ? '-' : Number(x.price).toFixed(2);
      const listed = (x.listed_price !== null && x.listed_price !== undefined && x.price_source === 'quoted')
        ? `<br><span class="muted" style="font-size:11px">标价 ${Number(x.listed_price).toFixed(2)}</span>` : '';
      const norm = normHtml(x);
      const age = ageBadge(x.price_at || x.quoted_at);
      const title = (x.title || '').length > 22 ? x.title.slice(0, 22) + '…' : (x.title || '');
      let rowCls = '';
      if (x.comparable && !bestMarked) { rowCls = ' class="cmp-best"'; bestMarked = true; }
      const compBadge = x.comparable
        ? '<span class="cmp-badge ok">可硬比价</span>'
        : '<span class="cmp-badge warn">仅参考</span>';
      return `<tr${rowCls}>
        <td>${escapeHtml(x.shop_name) || ''}</td>
        <td title="${escapeAttr(x.title)}">${escapeHtml(title)}</td>
        <td><b>¥${price}</b>${listed}${norm}${age}</td>
        <td>${SRC_CN[x.price_source] || x.price_source || '-'}</td>
        <td>${TIER_CN[x.tier] || x.tier || '-'}</td>
        <td>${compBadge}</td>
        <td class="cmp-cond">${fmtConditions(x)}</td>
        <td class="muted">${escapeHtml(x.condition_note)}</td>
      </tr>`;
    }).join('');
    html += '</tbody></table></div>';
    html += `<p class="muted" style="font-size:12px;margin-top:10px">可比性八维：<b>价格 · MOQ · 含税 · 运费 · 现货 · 发货时效 · 阶梯价 · 匹配层级</b>。`
      + `仅「完全同款」行可硬比价（橙色底 = 当前最低同款）；等效替代 / 未核验 / 已排除仅供参考，不进入比价结论。`
      + `阶梯价与 MOQ 门槛已按 100 件基准量折算归一价；所有价格为抓取时点快照，下单前请重新确认。</p>`;
  }
  html += `<div class="cmp-summary" style="margin-top:16px"><div class="trace-label" style="color:var(--accent-deep)">建议</div><p style="margin:4px 0 0;font-size:13px;line-height:1.8">${r.recommendation || ''}</p></div>`;
  if (r.negotiation_script) html += `<div style="margin-top:12px"><div class="trace-label" style="color:var(--accent-deep)">议价话术</div><pre style="margin-top:6px">${r.negotiation_script}</pre></div>`;
  document.getElementById('result').innerHTML = html;
  updatePipeline();
}

// ==================== 缓存加载 ====================
async function loadCachedComparison() {
  const el = document.getElementById('result');
  const capturedId = currentTargetId;
  if (!capturedId || !el) return;
  el.innerHTML = '<div class="empty" style="opacity:.6">正在加载比价结论…</div>';
  const r = await api(`/api/targets/${capturedId}/comparison`).catch(() => null);
  if (currentTargetId !== capturedId) return;
  if (!r || r.error || r.cached === false) {
    if (r && r.cached === false) {
      el.innerHTML = '<div class="empty compact">尚无结论。先「执行视觉匹配」分出同款与替代，再「生成比价结论」看价格对比与采购建议。</div>';
    }
    return;
  }
  renderComparison(r);
}

// ==================== 导出 ====================
async function exportComparison() {
  if (!currentTargetId) return alert('先选择目标款');
  const r = await api(`/api/targets/${currentTargetId}/comparison`, { method: 'POST' });
  const rows = sortCmpRows(r.rows || []);
  if (!rows.length) return alert('暂无可导出的比价数据');

  const headers = ['店铺', '商品', '价格', '来源', '页面标价', 'MOQ', '含税', '运费', '现货', '发货时效', '阶梯价',
                   '匹配状态', '可比', '条件备注', '链接'];
  const tierMap = { exact: '完全同款', equivalent: '等效替代', unverified: '未核验', rejected: '已排除' };
  const srcMap = { listed: '标价', quoted: '报价' };
  const taxMap = { included: '含税', excluded: '不含税', unknown: '未知' };
  const stockMap = { in_stock: '有现货', pre_sale: '预售', custom: '定制', unknown: '未知' };
  const fmtTiers = t => (t || []).map(x => `${x.qty}件及以上${x.price}元`).join('；');

  const esc = v => {
    const s = (v === null || v === undefined) ? '' : String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };

  const lines = [headers.join(',')];
  for (const x of rows) {
    lines.push([
      esc(x.shop_name), esc(x.title), esc(x.price), esc(srcMap[x.price_source] || x.price_source),
      esc(x.listed_price ?? ''), esc(x.moq ?? ''), esc(taxMap[x.tax] || x.tax || '未知'),
      esc(x.includes_shipping === true ? '包邮' : x.includes_shipping === false ? '不包邮' : '未知'),
      esc(stockMap[x.stock_status] || '未知'),
      esc(x.lead_time_hours !== null && x.lead_time_hours !== undefined
        ? (x.lead_time_hours < 24 ? `${x.lead_time_hours}小时内` : `${Math.round(x.lead_time_hours / 24)}天内`) : ''),
      esc(fmtTiers(x.tiers)), esc(tierMap[x.tier] || x.tier), esc(x.comparable ? '是' : '否'),
      esc(x.condition_note), esc(x.url),
    ].join(','));
  }

  lines.push('');
  lines.push(esc('综述') + ',' + esc(r.summary));
  lines.push(esc('建议') + ',' + esc(r.recommendation));
  if (r.negotiation_script) {
    lines.push(esc('议价话术') + ',' + esc(r.negotiation_script.replace(/\n/g, ' ')));
  }

  const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
  a.href = URL.createObjectURL(blob);
  a.download = `比价表_${ts}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

async function downloadReport() {
  if (!currentTargetId) return alert('先选择目标款');
  const r = await api(`/api/targets/${currentTargetId}/report`);
  if (!r || r.error) return alert('生成失败：' + (r && r.error ? r.error : '未知错误'));
  const blob = new Blob([r.markdown], { type: 'text/markdown;charset=utf-8' });
  const a = document.createElement('a');
  const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
  a.href = URL.createObjectURL(blob);
  a.download = `比价报告_${(r.target || '目标款').replace(/[\\/:*?"<>|]/g, '_')}_${ts}.md`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

// ==================== 演示模式 ====================
async function showDemo() {
  const targets = await api('/api/targets');
  if (!Array.isArray(targets)) { alert('读取目标款失败，请确认服务正在运行'); return; }
  let demo = null;
  for (const t of targets) {
    if (t.image_paths && t.image_paths.length > 0) {
      const prods = await api(`/api/targets/${t.id}/products`);
      if (prods && prods.length > 0) { demo = t; break; }
    }
  }
  if (!demo) {
    alert('未找到带图片和商品的目标款。\n\n请先创建一个带图片的目标款并导入一些候选商品。');
    return;
  }
  showTab('home');
  currentTargetId = demo.id;
  selectTarget(demo.id);
  runMatch();
}