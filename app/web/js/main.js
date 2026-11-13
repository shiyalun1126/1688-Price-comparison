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
    import: ['书签导入', '兜底通道 · 在 1688 页面上点一下'],
    tutorial: ['使用说明', '快速上手与面试演示'],
  };
  const t = titles[name] || titles.home;
  document.getElementById('page-title').textContent = t[0];
  document.getElementById('page-sub').textContent = t[1];
  // 流程子项只在工作台下有意义；回到工作台时恢复上次的展开状态
  const pipe = document.getElementById('pipeline');
  if (pipe) {
    pipe.classList.toggle('is-hidden', name !== 'home');
    if (name === 'home') setPipeOpen(localStorage.getItem(PIPE_OPEN_KEY) !== '0');
  }
  if (name === 'import') refreshImportTargets(currentTargetId);
  window.scrollTo({ top: 0 });
}

// ==================== 工作台下拉（流程子项） ====================
// 原先流程条横在主区顶部，占掉一行；现在收进侧栏「工作台」下面，点开才展开。
const PIPE_OPEN_KEY = 'wb-pipe-open';

function setPipeOpen(open) {
  const pipe = document.getElementById('pipeline');
  const btn = document.getElementById('nav-home');
  if (!pipe) return;
  pipe.classList.toggle('is-collapsed', !open);
  if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  try { localStorage.setItem(PIPE_OPEN_KEY, open ? '1' : '0'); } catch (e) {}
}

function onNavHome() {
  const onHome = document.getElementById('tab-home').classList.contains('active');
  if (!onHome) { showTab('home'); return; }   // 从使用说明切回来，顺手展开
  const pipe = document.getElementById('pipeline');
  setPipeOpen(pipe.classList.contains('is-collapsed'));
}

// 首载：默认展开，之后记住上次是开是合
setPipeOpen(localStorage.getItem(PIPE_OPEN_KEY) !== '0');

// ==================== 状态变量 ====================
let currentTargetId = null;
let currentTarget = null;
let shops = [];
let inquiries = [];
let targetsCount = 0;
let lastMatchResult = null;
let importCurrentTargetId = null;
let inqFilter = 'all';
// 候选池的分层筛选。这里只回答「哪些值得去问」，跟「谈到哪一步」无关——
// 谈没谈由 04 区的状态分组负责，两者不要再混在一排按钮里。
const INQ_FILTERS = [
  { key: 'all', label: '全部', match: q => true },
  { key: 'exact', label: '完全同款', match: q => (q.tier || 'unverified') === 'exact' },
  { key: 'equivalent', label: '等效替代', match: q => (q.tier || 'unverified') === 'equivalent' },
  { key: 'unverified', label: '未核验', match: q => (q.tier || 'unverified') === 'unverified' },
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
      document.getElementById('result').innerHTML = '<div class="empty compact">先「执行视觉匹配」分出完全同款与等效替代，再「生成比价结论」看价格对比、成交条件与采购建议。</div>';
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
  // 四步与页面四个区块一一对应（03 已合并「匹配与比价」）
  const counts = [currentTargetId ? 1 : 0, products.length, matched, inquiryCount];

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

  const inqStep = steps[3];
  if (inqStep) {
    inqStep.title = inquiryCount
      ? `已发起 ${inquiryCount} 家，已报价 ${quoted} 家`
      : '还没开始询价';
  }

  const sp = document.getElementById('stat-products');
  if (sp) sp.textContent = products.length;

  const ss = document.getElementById('stat-shops');
  if (ss) ss.textContent = new Set(products.map(p => p.shop_id).filter(Boolean)).size;
}
document.querySelectorAll('.pipe-step').forEach(el => {
  el.addEventListener('click', () => focusZone(el.dataset.zone));
});

// ==================== 区块折叠 ====================
// 抓一批候选进来之后四个区块一起铺开有两屏多，但多数时候你只在一处干活。
// 折叠不删任何功能，只是把「现在不看的」收起来；状态存本地，下次进来还是你摆的样子。
const ZONE_FOLD_KEY = 'wb-folded-zones';
let foldedZones = null;

function loadFoldedZones() {
  if (foldedZones) return foldedZones;
  try { foldedZones = new Set(JSON.parse(localStorage.getItem(ZONE_FOLD_KEY) || '[]')); }
  catch (e) { foldedZones = new Set(); }
  return foldedZones;
}

function toggleZone(zone, force) {
  if (typeof zone === 'string') zone = document.getElementById(zone);
  if (!zone) return;
  const folded = typeof force === 'boolean' ? force : !zone.classList.contains('folded');
  zone.classList.toggle('folded', folded);
  const set = loadFoldedZones();
  if (folded) set.add(zone.id); else set.delete(zone.id);
  try { localStorage.setItem(ZONE_FOLD_KEY, JSON.stringify([...set])); } catch (e) {}
}

function unfoldZone(zone) { toggleZone(zone, false); }

/** 滚到某个区块：先摊开 → 让出 sticky 顶栏的高度 → 到了闪一下。
 *  直接 scrollIntoView 会把区块顶到视口 0 位，标题正好被 58px 的顶栏盖住。 */
function prefersReducedMotion() {
  return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

function focusZone(zone) {
  if (typeof zone === 'string') zone = document.getElementById(zone);
  if (!zone) return;
  unfoldZone(zone);
  const bar = document.querySelector('.topbar');
  const offset = (bar ? bar.offsetHeight : 58) + 16;
  const top = zone.getBoundingClientRect().top + window.scrollY - offset;
  window.scrollTo({ top: Math.max(0, top), behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  // 落点提示：重播一次边框闪动
  zone.classList.remove('is-flash');
  void zone.offsetWidth;
  zone.classList.add('is-flash');
  clearTimeout(zone._flashTimer);
  zone._flashTimer = setTimeout(() => zone.classList.remove('is-flash'), 1200);
}

(function initZoneFold() {
  const set = loadFoldedZones();
  document.querySelectorAll('.zone').forEach(z => {
    if (set.has(z.id)) z.classList.add('folded');
    const title = z.querySelector('.zone-title');
    if (!title) return;
    title.classList.add('zone-title-btn');
    title.title = '点一下折叠 / 展开这个区块';
    title.addEventListener('click', () => toggleZone(z));
  });
})();

// ==================== 门店 ====================
async function loadShops() {
  const list = await api('/api/shops');
  shops = Array.isArray(list) ? list : [];
  const sel = document.getElementById('shop-select');
  // 手工添加表单已下线（候选统一走关键词抓取），select 不存在时不要报错
  if (sel) sel.innerHTML = shops.map(s => `<option value="${s.id}">${escapeHtml(s.name)}${s.location ? escapeHtml(' · ' + s.location) : ''}</option>`).join('');
  updatePipeline();
}
async function createShop() {
  // 手工添加表单已从页面下线（候选改走关键词抓取），这些 DOM 不再存在，
  // 函数保留是为了老备份页面/临时脚本还能调，调用时不要炸。
  const nameEl = document.getElementById('shop-name');
  if (!nameEl) return;
  const name = nameEl.value.trim();
  if (!name) return alert('填写门店名');
  await api('/api/shops', { method: 'POST', body: JSON.stringify({ name }) });
  nameEl.value = '';
  loadShops();
}

// ==================== 抓取浏览器状态 ====================
// 以前只有点了「从 1688 抓候选」才知道浏览器没开，白等一趟。把状态摆到按钮旁边，
// 15 秒跟一次（走 /api/browser/status，纯本地端口探测，不打开任何页面）。
// 三态：null = 问不到（后端没起 / 正在问），true/false = 后端的结论。
// 后端不通时绝不能显示「未启动」——那是把「我不知道」说成了「没有」。
let browserReady = null;
let browserStartCmd = '';

async function refreshBrowserStatus() {
  const chip = document.getElementById('crawl-status');
  if (!chip) return;
  let r = null;
  try { r = await api('/api/browser/status'); } catch (e) { r = null; }
  const known = !!(r && r.ok !== false && typeof r.ready === 'boolean');
  const ready = known && r.ready === true;
  browserReady = known ? ready : null;
  if (known && r.start_cmd) browserStartCmd = r.start_cmd;

  chip.classList.toggle('ready', ready);
  chip.classList.toggle('down', known && !ready);
  chip.classList.toggle('unknown', !known);
  const label = !known ? '状态未知' : (ready ? '浏览器就绪' : '浏览器未启动');
  const text = chip.querySelector('.cs-text');
  if (text) text.textContent = label;
  chip.setAttribute('aria-label', `抓取浏览器状态：${label}`);
  chip.title = !known ? '问不到后端（服务没起？），点一下看怎么办'
    : ready ? '已连上抓取浏览器（CDP 9222），可以直接抓'
    : '抓取浏览器没开，点一下看启动命令';
}

function onBrowserChip() {
  const hint = document.getElementById('crawl-hint');
  if (!hint) return;
  if (browserReady === true) { hint.textContent = '浏览器在线，填关键词直接抓（约 20 秒）'; return; }
  if (browserReady === null) {
    hint.textContent = '问不到后端状态：先确认服务在 127.0.0.1:8000 起着，再刷新本页';
    return;
  }
  const cmd = browserStartCmd || 'python scripts/start_browser.py --hold';
  hint.textContent = `先在项目目录执行：${cmd}（首次要扫码登录 1688）`;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(cmd)
      .then(() => { hint.textContent = `启动命令已复制，粘到终端执行：${cmd}`; })
      .catch(() => {});
  }
}

document.addEventListener('DOMContentLoaded', () => refreshBrowserStatus());

// ==================== 找货源（关键词抓取） ====================
async function crawlCandidates() {
  if (!currentTargetId) { alert('请先选择目标款'); return; }
  const input = document.getElementById('crawl-keywords');
  const btn = document.getElementById('btn-crawl');
  const hint = document.getElementById('crawl-hint');
  const kw = (input.value || '').trim();
  if (!kw) { input.focus(); if (hint) hint.textContent = '先填关键词'; return; }

  // 已知没开就别等那 20 秒：先跟一次状态（可能你刚在终端起了），还是没开就直说
  if (browserReady !== true) {
    await refreshBrowserStatus();
    if (browserReady === null) {
      if (hint) hint.textContent = '问不到后端状态：先确认服务在 127.0.0.1:8000 起着';
      return;
    }
    if (browserReady === false) {
      if (hint) hint.textContent = '抓取浏览器没开，点右边那个灰点看启动命令';
      return;
    }
  }

  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '抓取中…';
  if (hint) hint.textContent = '正在用已登录的浏览器搜 1688，约 20 秒';
  let newCount = 0;
  try {
    const r = await api(`/api/targets/${currentTargetId}/crawl`,
      { method: 'POST', body: JSON.stringify({ keywords: kw, max_items: 20 }) });
    if (r.error || r.ok === false) {
      if (hint) hint.textContent = r.error || '抓取失败';
    } else if (!r.imported && r.skipped) {
      if (hint) hint.textContent = `这 ${r.skipped} 件都已在候选里了，换个关键词`;
    } else {
      newCount = r.imported || 0;
      if (hint) hint.textContent = `新增 ${r.imported} 件 / ${r.shops} 家店，跳过 ${r.skipped} 件已存在`;
      input.value = '';
    }
  } catch (e) {
    if (hint) hint.textContent = '抓取失败：' + (e && e.message ? e.message : e);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
    refreshBrowserStatus();          // 抓到一半浏览器被关掉也能立刻反映
    updatePipeline();
    await refreshInquiries();
    // 刚抓进来的都在「待询价」组，而那组默认收起；抓完主动摊开并滚过去，
    // 否则你看到的是「新增 20 件」，却在页面上找不到这 20 件在哪。
    if (newCount) {
      const open = loadInqOpen();
      open.add('todo');
      try { localStorage.setItem(INQ_OPEN_KEY, JSON.stringify([...open])); } catch (e) {}
      renderWorkbench();
      const zone = document.getElementById('zone-inquiry');
      if (zone) focusZone(zone);
    }
  }
}

// ==================== 候选商品 ====================
async function createProduct() {
  // 同 createShop：表单位置已换成「找货源」抓取行，DOM 不存在时安全退出
  const sel = document.getElementById('shop-select');
  if (!sel) return;
  if (!currentTargetId) return alert('先选择目标款');
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