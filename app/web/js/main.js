/**
 * 主逻辑：Tab 切换、目标款管理、门店、候选商品、流程步骤条
 */

// ==================== Tab 切换 ====================
function showTab(name) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(el => el.classList.remove('active'));
  const tabEl = document.getElementById('tab-' + name);
  if (tabEl) tabEl.classList.add('active');
  const navEl = document.querySelector('.nav-tab[data-tab="' + name + '"]');
  if (navEl) navEl.classList.add('active');
  const titles = {
    home: ['工作台', '以款找源 · 全网比价'],
    import: ['导入书签', '从 1688 页面一键抓取候选'],
    tutorial: ['使用说明', '快速上手与面试演示'],
  };
  const t = titles[name] || titles.home;
  document.getElementById('page-title').textContent = t[0];
  document.getElementById('page-sub').textContent = t[1];
  document.getElementById('pipeline').classList.toggle('is-hidden', name !== 'home');
  if (name === 'import') refreshImportTargets(currentTargetId);
  window.scrollTo({ top: 0 });
}

// ==================== 状态变量 ====================
let currentTargetId = null;
let currentTarget = null;
let shops = [];
let inquiries = [];
let targetsCount = 0;
let lastMatchResult = null;
let importCurrentTargetId = null;
let inqFilter = 'all';
const INQ_FILTERS = [
  { key: 'all', label: '全部', match: q => true },
  { key: 'exact', label: '完全同款', match: q => (q.tier || 'unverified') === 'exact' },
  { key: 'equivalent', label: '等效替代', match: q => (q.tier || 'unverified') === 'equivalent' },
  { key: 'quoted', label: '已报价', match: q => q.price_source === 'quoted' || q.status === 'quoted' },
  { key: 'unverified', label: '未核验', match: q => (q.tier || 'unverified') === 'unverified' && q.price_source !== 'quoted' },
  { key: 'rejected', label: '已排除', match: q => (q.tier || 'unverified') === 'rejected' },
];

// ==================== 目标款管理 ====================
async function refreshTargets() {
  const ts = await api('/api/targets');
  if (!Array.isArray(ts)) return;
  targetsCount = ts.length || 0;
  const statEl = document.getElementById('stat-targets');
  if (statEl) statEl.textContent = targetsCount;

  // 顶栏切换器列表
  const pop = document.getElementById('switcher-options');
  if (pop) {
    pop.innerHTML = ts.length ? ts.map(t => `
      <button type="button" class="ts-option ${t.id === currentTargetId ? 'on' : ''}" onclick="selectTarget('${t.id}')">
        <span class="ts-thumb">${t.image_paths && t.image_paths.length ? `<img src="${t.image_paths[0]}" alt="">` : '<i></i>'}</span>
        <span class="ts-name">${escapeHtml(t.name)}</span>
        <span class="ts-meta">${escapeHtml(t.category || '')}</span>
      </button>`).join('') : '<div class="ts-empty">还没有目标款，先创建一个</div>';
  }

  // 当前目标款展示
  const mine = ts.find(t => t.id === currentTargetId);
  currentTarget = mine || null;
  const nameEl = document.getElementById('target-name');
  const metaEl = document.getElementById('target-meta');
  const prevEl = document.getElementById('target-preview');
  const delBtn = document.getElementById('btn-delete-target');
  const swName = document.getElementById('switcher-name');
  const swThumb = document.getElementById('switcher-thumb');
  if (mine) {
    if (nameEl) nameEl.textContent = mine.name;
    if (metaEl) {
      const chip = s => `<span class="attr-chip">${escapeHtml(s)}</span>`;
      const chips = [];
      if (mine.category) chips.push(chip(mine.category));
      if (mine.reference_price != null) chips.push(chip('参考价 ¥' + Number(mine.reference_price).toFixed(2)));
      for (const [k, v] of Object.entries(mine.attributes || {})) chips.push(chip(`${k}: ${v}`));
      metaEl.innerHTML = (chips.join('') || '<span class="muted">暂无属性</span>')
        + (mine.description ? `<div style="margin-top:4px">${escapeHtml(mine.description)}</div>` : '');
    }
    if (prevEl) prevEl.innerHTML = (mine.image_paths && mine.image_paths.length) ? `<img class="thumb" src="${mine.image_paths[0]}" alt="目标款图">` : '';
    if (delBtn) delBtn.disabled = false;
    if (swName) swName.textContent = mine.name;
    if (swThumb) swThumb.innerHTML = (mine.image_paths && mine.image_paths.length) ? `<img src="${mine.image_paths[0]}" alt="">` : '<i></i>';
  } else {
    if (nameEl) nameEl.textContent = '未选择目标款';
    if (metaEl) metaEl.textContent = '创建或切换一个目标款，开始比价工作流';
    if (prevEl) prevEl.innerHTML = '';
    if (delBtn) delBtn.disabled = true;
    if (swName) swName.textContent = '未选择目标款';
    if (swThumb) swThumb.innerHTML = '<i></i>';
  }
  updatePipeline();
}

function deleteCurrentTarget() {
  if (!currentTargetId || !currentTarget) return;
  deleteTarget(currentTargetId, currentTarget.name || '');
}

async function deleteTarget(id, name) {
  if (!confirm('确定删除目标款《' + name + '》？\n\n该目标款下的所有候选商品、询价和报价都会被一并删除，且无法恢复。')) return;
  const r = await api(`/api/targets/${id}`, { method: 'DELETE' });
  if (r.ok) {
    const d = r.deleted || {};
    if (currentTargetId === id) {
      currentTargetId = null;
      lastMatchResult = null;
      document.getElementById('result').innerHTML = '<div class="empty">选好目标款与候选商品后，先「执行视觉匹配」分出完全同款与等效替代，<br>再「生成比价结论」查看价格对比、成交条件与采购建议。</div>';
      document.getElementById('workbench').innerHTML = '<p class="muted">请先选择或创建一个目标款</p>';
      document.getElementById('btn-export').disabled = true;
    }
    await refreshTargets();
    await refreshInquiries();
    try { await refreshImportTargets(currentTargetId); } catch (e) {}
    alert('✅ 已删除\n商品 ' + (d.product || 0) + ' 个、询价 ' + (d.inquiry || 0) + ' 条、报价 ' + (d.quote || 0) + ' 条');
  } else {
    alert('❌ 删除失败：' + (r.error || '未知错误'));
  }
}

function selectTarget(id) {
  currentTargetId = id;
  lastMatchResult = null;
  toggleSwitcher(false);
  syncCurrentTarget(id);
  refreshTargets();
  refreshInquiries();
  loadCachedComparison();
  loadAgentHistory();
}

async function createTarget() {
  const name = document.getElementById('ts-name').value.trim();
  if (!name) { alert('先填写目标款名称'); return; }
  const form = new FormData();
  form.append('name', name);
  form.append('description', document.getElementById('ts-desc').value || '');
  form.append('attributes_json', document.getElementById('ts-attr').value || '{}');
  form.append('reference_price', document.getElementById('ts-ref').value || '');
  form.append('category', document.getElementById('ts-cat').value || '');
  const file = document.getElementById('ts-image').files[0];
  if (file) form.append('image', file);
  const t = await apiForm('/api/targets', form);
  if (!t || !t.id) {
    alert('❌ 创建失败：' + (t && t.error ? t.error : '未知错误'));
    return;
  }
  currentTargetId = t.id;
  toggleTargetForm(false);
  await syncCurrentTarget(t.id);
  await refreshTargets();
  await refreshImportTargets(t.id);
  await refreshInquiries();
}

function openNewTargetForm() {
  toggleSwitcher(false);
  const form = document.getElementById('new-target-form');
  form.hidden = false;
  form.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  document.getElementById('ts-name').focus();
}
function toggleTargetForm(show) {
  document.getElementById('new-target-form').hidden = !show;
}

// 顶栏切换器开合
function toggleSwitcher(force) {
  const pop = document.getElementById('switcher-pop');
  if (!pop) return;
  const open = force !== undefined ? force : !pop.classList.contains('open');
  pop.classList.toggle('open', open);
}
document.addEventListener('click', e => {
  const pop = document.getElementById('switcher-pop');
  if (!pop || !pop.classList.contains('open')) return;
  if (!e.target.closest('#switcher-pop') && !e.target.closest('#btn-switch')
    && !e.target.closest('#btn-switch-zone')) toggleSwitcher(false);
});

// file 控件文件名回显
document.addEventListener('change', e => {
  if (!e.target.matches || !e.target.matches('input[type="file"]')) return;
  const holder = (e.target.closest('label') && e.target.closest('label').parentElement)
    || e.target.closest('.field, .sub-row');
  const nameEl = holder && holder.querySelector('.file-name');
  if (nameEl) nameEl.textContent = e.target.files.length ? e.target.files[0].name : '未选择';
});

// ==================== 流程步骤条 ====================
async function updatePipeline() {
  const steps = document.querySelectorAll('.pipe-step');
  if (!steps.length) return;
  let products = [];
  if (currentTargetId) {
    try { products = await api(`/api/targets/${currentTargetId}/products`); } catch (e) {}
  }
  const matched = lastMatchResult
    ? ((lastMatchResult.exact || []).length + (lastMatchResult.equivalent || []).length)
    : products.filter(p => p.tier === 'exact' || p.tier === 'equivalent').length;
  const inquiryCount = (inquiries || []).length;
  const quoted = (inquiries || []).filter(q => q.status === 'quoted').length;
  const counts = [currentTargetId ? 1 : 0, products.length, matched, inquiryCount, quoted];

  let seenActive = false;
  counts.forEach((c, i) => {
    const el = steps[i];
    if (!el) return;
    el.classList.remove('done', 'active', 'todo');
    if (c > 0) el.classList.add('done');
    else if (!seenActive && (i === 0 || counts[i - 1] > 0)) { el.classList.add('active'); seenActive = true; }
    else el.classList.add('todo');
    const badge = el.querySelector('.pipe-count');
    if (badge) badge.textContent = c > 0 ? String(c) : '';
  });

  const sp = document.getElementById('stat-products');
  if (sp) sp.textContent = products.length;

  const ss = document.getElementById('stat-shops');
  if (ss) ss.textContent = new Set(products.map(p => p.shop_id).filter(Boolean)).size;
}
document.querySelectorAll('.pipe-step').forEach(el => {
  el.addEventListener('click', () => {
    const zone = document.getElementById(el.dataset.zone);
    if (zone) zone.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
});

// ==================== 门店 ====================
async function loadShops() {
  const list = await api('/api/shops');
  shops = Array.isArray(list) ? list : [];
  const sel = document.getElementById('shop-select');
  sel.innerHTML = shops.map(s => `<option value="${s.id}">${escapeHtml(s.name)}${s.location ? escapeHtml(' · ' + s.location) : ''}</option>`).join('');
  updatePipeline();
}
async function createShop() {
  const name = document.getElementById('shop-name').value.trim();
  if (!name) return alert('填写门店名');
  await api('/api/shops', { method: 'POST', body: JSON.stringify({ name }) });
  document.getElementById('shop-name').value = '';
  loadShops();
}

// ==================== 候选商品 ====================
async function createProduct() {
  if (!currentTargetId) return alert('先选择目标款');
  const sel = document.getElementById('shop-select');
  if (!sel.value) return alert('先添加并选择门店');
  const form = new FormData();
  form.append('target_style_id', currentTargetId);
  form.append('shop_id', sel.value);
  form.append('title', document.getElementById('prod-title').value.trim());
  form.append('attributes_json', document.getElementById('prod-attr').value || '{}');
  form.append('listed_price', document.getElementById('prod-price').value || '');
  const file = document.getElementById('prod-image').files[0];
  if (file) form.append('image', file);
  const r = await apiForm('/api/products', form);
  if (r && r.error) { alert('❌ 添加失败：' + r.error); return; }
  document.getElementById('prod-title').value = '';
  document.getElementById('prod-image').value = '';
  document.getElementById('prod-attr').value = '';
  document.getElementById('prod-price').value = '';
  updatePipeline();
}

// ==================== 智能体同步 ====================
async function syncCurrentTarget(id) {
  if (!id) return;
  try {
    await api('/api/current-target', { method: 'POST', body: JSON.stringify({ target_id: id }) });
  } catch (e) {}
}