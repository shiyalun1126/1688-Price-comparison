/**
 * 导入书签：目标款选择、书签生成
 */

// ==================== 导入书签页 ====================
async function refreshImportTargets(selectId) {
  const ts = await api('/api/targets');
  if (!Array.isArray(ts)) return;
  const sel = document.getElementById('target-select-import');
  sel.innerHTML = ts.map(t => `<option value="${t.id}">${escapeHtml(t.name)}</option>`).join('');
  let defId = selectId || null;
  if (!defId) {
    const cur = await api('/api/current-target').catch(() => ({}));
    defId = (cur && cur.id) || (ts.length ? ts[0].id : null);
  }
  if (defId) {
    sel.value = defId;
    importCurrentTargetId = defId;
    syncCurrentTarget(defId);
  }
  sel.onchange = () => {
    importCurrentTargetId = sel.value;
    syncCurrentTarget(sel.value);
    currentTargetId = sel.value;
    refreshTargets();
    refreshInquiries();
  };
  renderBM();
}

// 兼容旧调用名
async function loadImportTargets() {
  await refreshImportTargets();
}

function generateBookmarklet(createNew) {
  return `(async function(){
var CREATE_NEW=${createNew};
var BASE='${location.origin}';
var API=BASE+'/api/import-product';
var CREATE_API=BASE+'/api/targets/from-url';
var CURRENT_API=BASE+'/api/current-target';
var HOST=location.hostname;
var PAGE_TYPE=HOST==='detail.1688.com'?'detail':(HOST.indexOf('s.1688.com')>=0?'search':'other');
function $(s){return document.querySelector(s);}
function $$(s){return Array.from(document.querySelectorAll(s));}
function txt(el){return el?el.innerText.trim():'';}

// 价格解析：1688价格是分段渲染的，先去掉所有空白再匹配
function parsePrice(raw){
  if(!raw) return null;
  var flat=String(raw).replace(/\\s+/g,'');
  var m=flat.match(/[\\u00A5¥](\\d+(?:\\.\\d+)?)/);
  if(m) return parseFloat(m[1]);
  var m2=flat.match(/^(\\d+(?:\\.\\d+)?)$/);
  if(m2) return parseFloat(m2[1]);
  return null;
}

function extractPrice(card){
  var sels=['.price-item','.offer-price-row .price-item','.price-wrapper','.hover-price-item','.col-desc-price'];
  for(var i=0;i<sels.length;i++){
    var el=card.querySelector(sels[i]);
    if(el){
      var p=parsePrice(txt(el));
      if(p!==null && p>0) return p;
    }
  }
  return parsePrice(txt(card));
}

function extractAttrs(card){
  var el=card.querySelector('[class*="desc"],[class*="attr"],[class*="prop"]');
  var raw=(el?txt(el):txt(card)).replace(/\\s+/g,'');
  var seg=raw.split(/[\\u00A5¥]/)[0];
  if(seg.indexOf('｜')<0&&seg.indexOf('|')<0) return {};
  var parts=seg.split(/｜|\\|/).map(function(s){return s.trim();}).filter(function(s){return s && s.length<=12;});
  if(parts.length<2) return {};
  return {属性: parts.slice(0,4).join(' / ')};
}

function extractTitle(text){var lines=text.split(/\\n|\\r/).map(l=>l.trim()).filter(l=>l.length>2&&l.length<100);return lines.length>0?lines[0]:'';}

async function getCurrentTarget(){
  try{
    var r=await fetch(CURRENT_API,{cache:'no-store'});
    var d=await r.json();
    if(d && d.id){return d.id;}
  }catch(e){}
  return null;
}

async function createTargetFromPage(){
  var title=txt($('h1'))||document.title.replace(/[-_].*$/,'').trim();
  var img='';
  $$('img').some(function(im){if((im.naturalWidth||0)>200&&im.src&&!im.src.startsWith('data:')){img=im.src;return true;}});
  if(!img){alert('❌ 没找到图片，无法创建目标款');return null;}
  var pm=document.body.innerText.match(/\\u00A5(\\d+(?:\\.\\d+)?)/);
  var price=pm?parseFloat(pm[1]):null;
  var payload={title:title,image_url:img,price:price,category:'衬衫',description:''};
  var r=await fetch(CREATE_API,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  var d=await r.json();
  if(d.ok && d.id){return d.id;}
  alert('❌ 创建目标款失败：'+(d.error||'未知错误'));
  return null;
}

var TARGET_ID=await getCurrentTarget();
if(!TARGET_ID && !CREATE_NEW){
  alert('⚠️ 系统里还没有目标款。\\n请先到比价系统创建一个目标款再导入。');
  return;
}

if(PAGE_TYPE==='search'){
  var cards=$$('.space-common-offerlist, .page-offerlist, .search-offer-wrapper.major-offer');
  if(cards.length===0) cards=$$('div[class*="card"],div[class*="item"],div[class*="offer"]').filter(function(c){return c.querySelector('img')&&c.querySelector('a[href*="offer"]');});
  if(cards.length===0){alert('❌ 没找到商品卡片，请确认在 1688 搜索列表页');return;}
  var imported=0,failed=0,withPrice=0;
  async function importOne(card,i){
    try{
      var imgs=card.querySelectorAll('img'),links=card.querySelectorAll('a[href*="offer"]');
      if(imgs.length===0||links.length===0){failed++;return;}
      var imgSrc=imgs[0].src,linkUrl=links[0].href;
      if(!imgSrc||!linkUrl){failed++;return;}
      var shopRow=$('.offer-shop-row',card)||$('.offer-shop-row');
      var shopName=txt(shopRow)||'1688店铺';
      var title=extractTitle(txt(card))||'1688商品';
      var price=extractPrice(card);
      var attrs=extractAttrs(card);
      if(price!==null) withPrice++;
      var payload={target_style_id:TARGET_ID,title:title,image_url:imgSrc,price:price,url:linkUrl,shop_name:shopName,
                   attributes:attrs, attributes_json:JSON.stringify(attrs)};
      var r=await fetch(API,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      var d=await r.json();
      if(d.ok){imported++;}else{console.error('导入失败:',d.error);failed++;}
    }catch(e){console.error('处理卡片出错:',e);failed++;}
  }
  for(var i=0;i<cards.length && i<20;i++){await importOne(cards[i],i);}
  alert('✅ 批量导入完成\\n成功: '+imported+' 个（其中带价格 '+withPrice+' 个）\\n失败: '+failed+' 个');
}else if(PAGE_TYPE==='detail'){
  if(CREATE_NEW){
    var newId=await createTargetFromPage();
    if(!newId){return;}
    TARGET_ID=newId;
  }
  var h1txt=txt($('h1'));
  var h1IsCompany=/(有限公司|实业|工贸|贸易|商行|日用品|服饰|科技)/.test(h1txt);
  var ogEl=$('meta[property="og:title"]');
  var dt=document.title.replace(/[-_|–—].*$/,'').replace(/(阿里巴巴|1688).*/,'').trim();
  var cm=document.documentElement.innerHTML.match(/"companyName"\\s*:\\s*"([^"]{2,40})"/);
  var company=(h1IsCompany&&h1txt)||(cm&&cm[1])||txt($('.company-name'))||'';
  var title=(!h1IsCompany&&h1txt)?h1txt:(ogEl&&ogEl.content&&ogEl.content.trim())||dt||'1688商品';
  var img='';
  $$('img').some(function(im){if((im.naturalWidth||0)>200&&im.src&&!im.src.startsWith('data:')){img=im.src;return true;}});
  var price=null;
  var pm=document.body.innerText.match(/\\u00A5(\\d+(?:\\.\\d+)?)/);
  if(pm)price=parseFloat(pm[1]);
  var shopRow=$('.offer-shop-row')||$('.company-name')||$('.shop-name');
  var um=location.href.match(/[?&]uid=([^&]+)/);
  var shopName=um?decodeURIComponent(um[1]):(company||txt(shopRow)||'1688店铺');
  var offerId=(location.href.match(/offer\\/(\\d+)/)||[])[1]||'';
  var url='http://detail.1688.com/offer/'+offerId+'.html';
  fetch(API,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    target_style_id:TARGET_ID,title:title,image_url:img,price:price,url:url,shop_name:shopName.trim()
  })}).then(function(r){return r.json();}).then(function(d){
    if(d.ok){alert(CREATE_NEW?('✅ 已创建目标款《'+title+'》并导入'):('✅ 导入成功：'+title));}
    else{alert('❌ 导入失败：'+(d.error||'未知错误'));}
  }).catch(function(e){alert('❌ 请求失败：'+e.message);});
}else{
  alert('⚠️ 请在 1688 搜索列表页(s.1688.com)或商品详情页(detail.1688.com)使用此书签');
}
})();`;
}

function renderBM() {
  var createNew = document.getElementById('create-new-target').checked;
  var code = generateBookmarklet(createNew);
  var oneLine = 'javascript:' + encodeURIComponent(code);
  document.getElementById('bm-btn').href = oneLine;
  document.getElementById('bm-code').value = oneLine;
}