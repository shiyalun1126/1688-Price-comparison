function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function renderMarkdown(text) {
  if (!text) return '';
  let s = escapeHtml(text);

  // 代码块 ```...```
  s = s.replace(/```[\w]*\n?([\s\S]*?)```/g, (m, code) =>
    `<pre style="background:#f1f5f9;padding:8px;border-radius:6px;overflow:auto;font-size:12px">${code}</pre>`);

  // 行内代码 `xxx`
  s = s.replace(/`([^`\n]+)`/g, '<code style="background:#f1f5f9;padding:1px 5px;border-radius:4px">$1</code>');

  // 链接 [文字](url) —— 只允许 http(s)，且新窗口打开
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (m, label, url) =>
      `<a href="${url}" target="_blank" rel="noopener noreferrer" style="color:var(--accent);text-decoration:underline">${label}</a>`);

  // 裸链接
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
    (m, pre, url) =>
      `${pre}<a href="${url}" target="_blank" rel="noopener noreferrer" style="color:var(--accent);text-decoration:underline">${url}</a>`);

  // 粗体 **xxx**
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');

  // 表格：| a | b | 形式
  s = s.replace(/(?:^\|.*\|\s*$\n?)+/gm, block => {
    const lines = block.trim().split('\n').map(l => l.trim());
    if (lines.length < 2) return block;
    const cells = l => l.replace(/^\||\|$/g, '').split('|').map(c => c.trim());
    const head = cells(lines[0]);
    // 第二行是分隔线 |---|---|
    const isSep = /^\|?[\s:-]+\|/.test(lines[1]);
    const body = lines.slice(isSep ? 2 : 1);
    let t = '<table style="width:100%;border-collapse:collapse;font-size:13px;margin:8px 0">';
    t += '<tr>' + head.map(h => `<th style="text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);color:var(--muted)">${h}</th>`).join('') + '</tr>';
    for (const row of body) {
      t += '<tr>' + cells(row).map(c => `<td style="padding:6px 8px;border-bottom:1px solid #f1f5f9">${c}</td>`).join('') + '</tr>';
    }
    t += '</table>';
    return t;
  });

  // 标题 ###
  s = s.replace(/^#{1,6}\s+(.+)$/gm, '<div style="font-weight:700;margin:10px 0 4px">$1</div>');

  // 换行
  s = s.replace(/\n/g, '<br>');
  return s;
}
module.exports={renderMarkdown};