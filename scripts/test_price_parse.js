// 用真实 1688 文本验证价格解析逻辑
const samples = [
  "外贸Polo衫速干短袖定制欧美大码热升华冰丝翻领短袖工作服印LOGO\n聚酯纤维（涤纶）\n｜\n冰丝\n｜\n合体型\n¥\n17\n.5\n运费8元\n已售600+件",
  "男装夏季polo衫定制logo冰离子体恤企业服纯色短袖男t恤工作班服\n棉\n｜\n宽松型\n｜\n拼贴/拼接\n¥\n50\n运费4.6元\n全网1300+件",
  "外贸欧码美码纯棉T恤定制logo印字广告衫圆领定做短袖订做文化衫\n纯棉\n｜\n普通\n｜\n套头\n¥\n9\n.5\n红包价\n4.4万+件",
  "跨境男装纯色圆领长袖上衣亚马逊春秋外贸欧码休闲户外运动polo衫\n设计上新 直筒版型显瘦\n¥\n20\n.5\n运费5元",
  "男士撞色翻领长袖Polo衫黑领搭配细白线条三粒扣设计简约大气上衣\n聚酯纤维（涤纶）\n｜\n修身型\n｜\n普通\n¥\n13\n红包价\n包邮",
];

// 新逻辑：先去掉所有空白再匹配
function parsePrice(raw) {
  if (!raw) return null;
  const flat = String(raw).replace(/\s+/g, '');
  const m = flat.match(/[\u00A5¥](\d+(?:\.\d+)?)/);
  if (m) return parseFloat(m[1]);
  const m2 = flat.match(/^(\d+(?:\.\d+)?)$/);
  if (m2) return parseFloat(m2[1]);
  return null;
}

// 属性提取
function extractAttrs(text) {
  const raw = String(text).replace(/\s+/g, '');
  const seg = raw.split(/[\u00A5¥]/)[0];
  if (seg.indexOf('｜') < 0 && seg.indexOf('|') < 0) return {};
  const parts = seg.split(/｜|\|/).map(s => s.trim()).filter(s => s && s.length <= 12);
  if (parts.length < 2) return {};
  return { 属性: parts.slice(0, 4).join(' / ') };
}

console.log('=== 价格解析 ===');
const expected = [17.5, 50, 9.5, 20.5, 13];
samples.forEach((s, i) => {
  const p = parsePrice(s);
  const ok = p === expected[i] ? '✅' : '❌';
  console.log(`${ok} 期望 ${expected[i]} 实际 ${p}`);
});

console.log('\n=== 属性提取 ===');
samples.forEach((s, i) => {
  console.log(i + 1, JSON.stringify(extractAttrs(s)));
});