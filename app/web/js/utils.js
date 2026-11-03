/**
 * 工具函数库
 * - HTTP 请求封装
 * - HTML 转义与 Markdown 渲染
 * - 时间/价格格式化
 */

// ==================== HTTP 请求 ====================
async function api(path, opts = {}) {
  let r;
  try {
    r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  } catch (e) {
    return { error: '网络请求失败：' + (e && e.message ? e.message : e) };
  }
  let d = null;
  try { d = await r.json(); } catch (e) { d = null; }
  if (!r.ok || d === null) {
    const detail = (d && d.error) ? '：' + d.error : '';
    return { error: `服务端错误（HTTP ${r.status}）${detail}` };
  }
  return d;
}

async function apiForm(path, formData) {
  let r;
  try {
    r = await fetch(path, { method: 'POST', body: formData });
  } catch (e) {
    return { error: '网络请求失败：' + (e && e.message ? e.message : e) };
  }
  let d = null;
  try { d = await r.json(); } catch (e) { d = null; }
  if (!r.ok || d === null) {
    const detail = (d && d.error) ? '：' + d.error : '';
    return { error: `服务端错误（HTTP ${r.status}）${detail}` };
  }
  return d;
}

// ==================== HTML 安全 ====================
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escapeAttr(s) {
  return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

// ==================== Markdown 渲染 ====================
function renderMarkdown(text) {
  if (!text) return '';
  let s = escapeHtml(text);

  // 代码块 ```...```
  s = s.replace(/```[\w]*\n?([\s\S]*?)```/g, (m, code) =>
    `<pre class="md-pre">${code}</pre>`);

  // 行内代码 `xxx`
  s = s.replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>');

  // 链接 [文字](url) —— 只允许 http(s)，且新窗口打开
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (m, label, url) =>
      `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`);

  // 裸链接
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
    (m, pre, url) =>
      `${pre}<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);

  // 粗体 **xxx**
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');

  // 表格：| a | b | 形式
  s = s.replace(/(?:^\|.*\|\s*$\n?)+/gm, block => {
    const lines = block.trim().split('\n').map(l => l.trim());
    if (lines.length < 2) return block;
    const cells = l => l.replace(/^\||\|$/g, '').split('|').map(c => c.trim());
    const head = cells(lines[0]);
    const isSep = /^\|?[\s:-]+\|/.test(lines[1]);
    const body = lines.slice(isSep ? 2 : 1);
    let t = '<table class="md-tbl">';
    t += '<tr>' + head.map(h => `<th>${h}</th>`).join('') + '</tr>';
    for (const row of body) {
      t += '<tr>' + cells(row).map(c => `<td>${c}</td>`).join('') + '</tr>';
    }
    t += '</table>';
    return t;
  });

  // 标题 ###
  s = s.replace(/^#{1,6}\s+(.+)$/gm, '<div class="md-h">$1</div>');

  // 换行
  s = s.replace(/\n/g, '<br>');
  return s;
}

// ==================== 时间 / 价格格式化 ====================
function fmtDateTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtLeadTime(h) {
  if (h === null || h === undefined) return null;
  return h < 24 ? `${h}小时内发货` : `${Math.round(h / 24)}天内发货`;
}

function priceAge(iso) {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (isNaN(t)) return null;
  const days = Math.max(0, Math.floor((Date.now() - t) / 86400000));
  if (days < 7) return { days, cls: '', label: days === 0 ? '今日数据' : `${days}天前` };
  if (days < 30) return { days, cls: 'age-warn', label: `${days}天前` };
  return { days, cls: 'age-stale', label: `${days}天前` };
}

function ageBadge(iso) {
  const a = priceAge(iso);
  if (!a) return '';
  const tip = a.cls === 'age-warn' ? '价格数据已超 7 天，可能已变动，建议重新核验'
    : a.cls === 'age-stale' ? '价格数据已超 30 天，强烈建议重新询价核验'
    : '价格数据时间';
  return `<br><span class="age ${a.cls}" title="${tip}">${a.label}</span>`;
}

function normHtml(x) {
  if (!x.norm_note || x.normalized_price === null || x.normalized_price === undefined) return '';
  return `<br><span class="muted" style="font-size:11px">折算 ¥${Number(x.normalized_price).toFixed(2)}（${escapeHtml(x.norm_note)}）</span>`;
}

function fmtConditions(x) {
  const unknown = v => v === null || v === undefined || v === '' || v === 'unknown';
  const parts = [];
  if (x.moq) parts.push(`MOQ ${x.moq}`);
  if (!unknown(x.tax)) parts.push(TAX_CN[x.tax] || x.tax);
  if (x.includes_shipping === true) parts.push('包邮');
  else if (x.includes_shipping === false) parts.push('不包邮');
  if (!unknown(x.stock_status)) parts.push(STOCK_CN[x.stock_status] || x.stock_status);
  const lead = fmtLeadTime(x.lead_time_hours);
  if (lead) parts.push(`<b style="color:var(--accent-deep)">${lead}</b>`);
  if (x.tiers && x.tiers.length) {
    parts.push('阶梯价 ' + x.tiers.map(t => `${t.qty}件↑${Number(t.price).toFixed(2)}元`).join('，'));
  }
  return parts.length ? parts.join('<br>') : '<span class="muted">—</span>';
}

// ==================== 常量映射 ====================
const TIER_CN = { exact: '✅ 完全同款', equivalent: '⚠️ 等效替代', unverified: '· 未核验', rejected: '· 已排除' };
const SRC_CN = { listed: '标价', quoted: '报价' };
const TAX_CN = { included: '含税', excluded: '不含税', unknown: '含税未知' };
const STOCK_CN = { in_stock: '✅ 有现货', pre_sale: '🕐 预售', custom: '🛠 定制', unknown: '现货未知' };
const STATUS_CN = { pending: '未询价', draft: '话术已备', sent: '已发送', replied: '已回复', quoted: '已报价', closed: '已关闭' };
// TOOL_CN 与 PLAN_MARK 归 agent.js（此处曾重复声明，const 撞名会让 agent.js 整体不执行）