function generateBookmarklet(createNew) {
  return `(function(){
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
function getPrice(text){var m=text.match(/\\u00A5(\\d+(?:\\.\\d+)?)/);return m?parseFloat(m[1]):null;}
function extractTitle(text){var lines=text.split(/\\n|\\r/).map(l=>l.trim()).filter(l=>l.length>2&&l.length<100);return lines.length>0?lines[0]:'';}

// 每次点击时动态查询当前目标款（不再写死在书签里）
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

(async function(){
var TARGET_ID=await getCurrentTarget();
if(!TARGET_ID && !CREATE_NEW){
  alert('⚠️ 系统里还没有目标款。\\n请先到比价系统创建一个目标款再导入。');
  return;
}

if(PAGE_TYPE==='search'){
  var cards=$$('.space-common-offerlist, .page-offerlist, .search-offer-wrapper.major-offer');
  if(cards.length===0) cards=$$('div[class*="card"],div[class*="item"],div[class*="offer"]').filter(function(c){return c.querySelector('img')&&c.querySelector('a[href*="offer"]');});
  if(cards.length===0){alert('❌ 没找到商品卡片，请确认在 1688 搜索列表页');return;}
  var imported=0,failed=0;
  async function importOne(card,i){
    try{
      var imgs=card.querySelectorAll('img'),links=card.querySelectorAll('a[href*="offer"]');
      if(imgs.length===0||links.length===0){failed++;return;}
      var imgSrc=imgs[0].src,linkUrl=links[0].href;
      if(!imgSrc||!linkUrl){failed++;return;}
      var shopRow=$('.offer-shop-row',card)||$('.offer-shop-row');
      var shopName='';var um=(linkUrl||'').match(/[?&]uid=([^&]+)/);if(um)shopName=decodeURIComponent(um[1]);if(!shopName)shopName=txt(shopRow)||'1688店铺';
      var title=extractTitle(txt(card))||'1688商品';
      var price=getPrice(txt(card));
      var payload={target_style_id:TARGET_ID,title:title,image_url:imgSrc,price:price,url:linkUrl,shop_name:shopName};
      var r=await fetch(API,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      var d=await r.json();
      if(d.ok){imported++;}else{console.error('导入失败:',d.error);failed++;}
    }catch(e){console.error('处理卡片出错:',e);failed++;}
  }
  for(var i=0;i<cards.length && i<20;i++){await importOne(cards[i],i);}
  alert('✅ 批量导入完成\\n成功: '+imported+' 个\\n失败: '+failed+' 个');
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
  var shopName='';var um=location.href.match(/[?&]uid=([^&]+)/);if(um)shopName=decodeURIComponent(um[1]);if(!shopName)shopName=company||txt(shopRow)||'1688店铺';
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

// 把页面选中的目标款同步到后端，书签点击时会读取它
async function syncCurrentTarget(id) {
  if (!id) return;
  try {
    await api('/api/current-target', {method:'POST', body: JSON.stringify({target_id: id})});
  } catch (e) {}
}

// Demo mode: pre-load a demo target with results
async function showDemo() {
  const targets = await api('/api/targets');
  console.log('showDemo: found', targets.length, 'targets');
  
  // 优先找有图片且有商品的目标款
  let demo = null;
  for (const t of targets) {
    if (t.image_paths && t.image_paths.length > 0) {
      // 检查这个目标款下是否有商品
      const prods = await api(`/api/targets/${t.id}/products`);
      if (prods && prods.length > 0) {
        demo = t;
        break;
      }
    }
  }
  if (!demo) {
    alert('❌ 未找到带图片和商品的目标款。\n\n请先创建一个带图片的目标款并导入一些候选商品。');
    return;
  }
  currentTargetId = demo.id;
  selectTarget(demo.id);
  console.log('showDemo: selected target', demo.name, 'id=', demo.id, 'with', (await api(`/api/targets/${demo.id}/products`)).length, 'products');
  runMatch();
  alert('✅ 演示模式：已加载《' + demo.name + '》及匹配结果\n\n你可以在此基础上继续添加更多候选商品。');
}

// Init
refreshTargets();
loadShops();
refreshInquiries();
loadImportTargets();
</script>
</body>
</html
module.exports = generateBookmarklet;
