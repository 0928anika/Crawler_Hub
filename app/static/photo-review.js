/* One gallery for the whole job. Changes remain in memory until one bulk save. */
const PhotoReview=(()=>{
 let reviewJobId=null,data=null,excluded=new Set(),selected={keep:new Set(),trash:new Set()},dirty=false,busy=false,request=0;
 const paneIds={keep:'photoKeepGallery',trash:'photoTrashGallery'},limits={min:128,gap:12,row:222};
 const filters={keep:[],trash:[]};
 let drag=null,suppressClick=false,preparing=false,preloadAbort=null;
 const previews=new Map(),previewFailures=new Set();
 function canLeave(){return !busy&&(!dirty||confirm('저장하지 않은 사진 제외 변경이 있습니다. 저장하지 않고 나갈까요?'))}
 function isDirty(){return dirty}
 function status(){
  const total=data?.photos.length||0,removed=excluded.size;
  $('photoKeepCount').textContent=String(total-removed);$('photoTrashCount').textContent=String(removed);
  $('photoReviewStatus').textContent=busy?'결과를 저장하고 있습니다…':dirty?'변경사항 있음 · 마지막에 한 번 저장하세요':data?.draft?'중간 저장됨 · 최종 저장 시 결과에 적용':data?.finalized?'저장 완료 · 결과에 반영됨':'전체 사진을 검수한 뒤 저장하세요';
  $('photoReviewStatus').classList.toggle('is-dirty',dirty);
  $('photoReviewSave').disabled=busy||preparing||!total;$('photoReviewSave').textContent=busy?'저장 중…':'저장 · 결과에 적용';
  $('photoReviewExit').disabled=busy;$('photoReviewDraft').disabled=busy||preparing||!total;
  $('photoTrashMove').textContent='선택한 사진 버리기'+(selected.keep.size?' ('+selected.keep.size+')':'')+' →';
  $('photoRestore').textContent='← 선택한 사진 복원'+(selected.trash.size?' ('+selected.trash.size+')':'');
  for(const id of ['photoKeepSelect','photoTrashSelect','photoReviewSearch'])$(id).disabled=busy||preparing;
  $('photoTrashMove').disabled=busy||preparing||!selected.keep.size;$('photoRestore').disabled=busy||preparing||!selected.trash.size;
 }
 function filter(){
  const q=$('photoReviewSearch').value.trim().toLocaleLowerCase();filters.keep=[];filters.trash=[];
  for(const p of data?.photos||[])if(!q||(p.code+' '+p.name+' '+p.colors).toLocaleLowerCase().includes(q))filters[excluded.has(p.id)?'trash':'keep'].push(p);
 }
 function renderPane(side){
  const host=$(paneIds[side]),list=filters[side],width=host.clientWidth-28,cols=Math.max(1,Math.floor((width+limits.gap)/(limits.min+limits.gap)));
  const rows=Math.ceil(list.length/cols),height=rows*limits.row;
  if(host.scrollTop>height)host.scrollTop=Math.max(0,height-host.clientHeight);
  const start=Math.max(0,Math.floor(host.scrollTop/limits.row)-2),end=Math.min(rows,Math.ceil((host.scrollTop+host.clientHeight)/limits.row)+2);
  const canvas=el('div',undefined,'photo-gallery-canvas');canvas.style.height=Math.max(height,host.clientHeight-28)+'px';
  if(!list.length){const empty=el('div',undefined,'photo-gallery-empty');empty.append(el('span',side==='keep'?'▧':'♲'),el('b',side==='keep'?'표시할 사진이 없습니다':'쓰레기통이 비어 있습니다'),el('p',side==='keep'?'검색어를 확인하거나 사진을 복원하세요':'왼쪽에서 제외할 사진을 선택해 옮겨주세요'));canvas.append(empty)}
  const grid=el('div',undefined,'photo-gallery-grid');grid.style.gridTemplateColumns='repeat('+cols+', minmax(0, 1fr))';grid.style.top=start*limits.row+'px';
  for(const p of list.slice(start*cols,end*cols)){
   const card=button('',()=>toggle(side,p.id),'photo-review-card'+(selected[side].has(p.id)?' selected':''));card.dataset.photoId=p.id;card.setAttribute('aria-pressed',String(selected[side].has(p.id)));card.setAttribute('aria-label',p.code+' '+p.colors+' 사진 선택');
   const shot=el('div',undefined,'photo-review-shot'),img=el('img');img.decoding='async';img.alt=p.code+' '+p.colors;const preview=previews.get(p.thumbnail_url);if(preview)img.src=preview;else{shot.classList.add('photo-thumbnail-error');shot.append(el('span','미리보기 준비 실패 · 우클릭으로 원본 확인'));}
   const check=el('span',selected[side].has(p.id)?'✓':'','photo-review-check');shot.append(img,check);card.append(shot,el('b',p.code),el('small',p.colors||'색상 미연결'));card.title=p.name+' · '+p.colors;card.disabled=busy;
   card.oncontextmenu=e=>{e.preventDefault();if(!safeUrl(p.url))return;$('largeImage').src=p.url;$('lightboxTitle').textContent=p.code+' · '+p.colors;$('largeOriginal').href=p.url;$('lightbox').showModal()};grid.append(card);
  }
  canvas.append(grid);host.replaceChildren(canvas);
 }
 function render(){filter();renderPane('keep');renderPane('trash');status()}
 function toggle(side,id){if(busy)return;const set=selected[side];set.has(id)?set.delete(id):set.add(id);const card=$(paneIds[side]).querySelector('[data-photo-id="'+id+'"]');if(card){card.classList.toggle('selected',set.has(id));card.setAttribute('aria-pressed',String(set.has(id)));card.querySelector('.photo-review-check').textContent=set.has(id)?'✓':''}status()}
 function selectAll(side){if(busy)return;const all=filters[side].every(p=>selected[side].has(p.id));for(const p of filters[side])all?selected[side].delete(p.id):selected[side].add(p.id);renderPane(side);status()}
 function move(side){if(busy||!selected[side].size)return;for(const id of selected[side])side==='keep'?excluded.add(id):excluded.delete(id);selected[side].clear();dirty=true;render()}
 function progress(done,total,failed=0){
  const box=$('photoPreviewLoading');box.classList.toggle('hidden',!preparing&&!failed);$('photoPreviewBar').max=Math.max(1,total);$('photoPreviewBar').value=done;
  $('photoPreviewLabel').textContent=preparing?'전체 미리보기 준비 중 '+done+' / '+total+'장 · 처음 한 번만 원본을 읽습니다':failed+'개 미리보기 준비 실패 · 사진은 보존되어 있습니다';
  $('photoPreviewRetry').classList.toggle('hidden',preparing||!failed);
 }
 async function preparePreviews(seq,onlyFailed=false){
  preparing=true;status();preloadAbort?.abort();const controller=new AbortController();preloadAbort=controller;
  const urls=[...new Set(data.photos.map(p=>p.thumbnail_url))],queue=onlyFailed?urls.filter(url=>!previews.has(url)):urls;let index=0,done=onlyFailed?urls.length-queue.length:0;
  const weights=new Map();for(const p of data.photos)weights.set(p.thumbnail_url,(weights.get(p.thumbnail_url)||0)+1);
  let loaded=data.photos.filter(p=>previews.has(p.thumbnail_url)).length;progress(loaded,data.photos.length);
  async function worker(){while(index<queue.length&&seq===request&&S.page==='photos'&&!controller.signal.aborted){const url=queue[index++];try{if(!/^\/api\/jobs\/[a-f0-9]{32}\/photo-thumbnail\?image=/.test(url))throw Error('미리보기 주소 오류');const response=await fetch(url,{signal:controller.signal,cache:onlyFailed?'reload':'force-cache'});if(!response.ok||!response.headers.get('Content-Type')?.startsWith('image/'))throw Error('미리보기 조회 실패');const blob=await response.blob();if(seq!==request||S.page!=='photos'||controller.signal.aborted)return;previews.set(url,URL.createObjectURL(blob));previewFailures.delete(url)}catch(error){if(controller.signal.aborted)return;previewFailures.add(url)}done++;loaded+=weights.get(url)||0;progress(loaded,data.photos.length)}}
  await Promise.all(Array.from({length:6},worker));
  if(seq!==request||S.page!=='photos')return;preparing=false;progress(data.photos.length,data.photos.length,previewFailures.size);render();
 }
 async function load(){
  const seq=++request;preloadAbort?.abort();for(const url of previews.values())URL.revokeObjectURL(url);previews.clear();previewFailures.clear();preparing=false;reviewJobId=S.jobId;data=null;dirty=false;selected={keep:new Set(),trash:new Set()};excluded=new Set();$('photoReviewName').textContent=S.job?.name||'';$('photoReviewSearch').value='';
  for(const id of Object.values(paneIds))skeleton($(id));$('photoReviewStatus').textContent='수집된 사진을 불러오고 있습니다…';$('photoReviewSave').disabled=true;
  try{const result=await api('/api/jobs/'+reviewJobId+'/photo-review');if(seq!==request||S.page!=='photos')return;data=result;excluded=new Set(data.photos.filter(p=>p.excluded).map(p=>p.id));$('photoReviewName').textContent=(S.job?.name||'')+' · '+data.products+'개 품번 · '+data.photos.length+'장'+(data.excluded_tasks?' · 실패·미완료 '+data.excluded_tasks+'건 제외':'');for(const id of Object.values(paneIds))$(id).scrollTop=0;await preparePreviews(seq)}catch(e){for(const id of Object.values(paneIds)){empty($(id),'사진을 불러오지 못했습니다',e.message);$(id).append(button('다시 불러오기',load))}throw e}
 }
 async function save(draft=false){
  if(busy||preparing||!data)return;busy=true;status();
  try{const result=await api('/api/jobs/'+reviewJobId+'/photo-review',{expected_revision:data.revision,excluded_ids:[...excluded],draft});data.revision=result.revision;data.finalized=!draft;data.draft=draft;dirty=false;S.reviewKey='';S.job=await api('/api/jobs/'+S.jobId);renderHeader();toast(draft?'중간 저장했습니다. 사진 제외하기에서 이어서 작업하세요.':'저장 완료 · '+result.kept+'장 사용 / '+result.excluded+'장 제외. 원본은 보존했습니다.')}finally{busy=false;status()}
  if(draft)await navigate('review');
 }
 function cancelSelection(){selected.keep.clear();selected.trash.clear();drag=null;for(const id of Object.values(paneIds))$(id).classList.remove('photo-dragging');render()}
 function pointerDown(side,e){if(busy||e.button!==0||e.isPrimary===false)return;const card=e.target.closest('.photo-review-card');if(!card)return;drag={side,id:card.dataset.photoId,pointer:e.pointerId,startX:e.clientX,startY:e.clientY,x:e.clientX,y:e.clientY,remove:selected[side].has(card.dataset.photoId),moved:false}}
 function pointerMove(e){if(!drag||e.pointerId!==drag.pointer||busy)return;if(!drag.moved&&Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY)<5)return;e.preventDefault();const host=$(paneIds[drag.side]);if(!drag.moved){drag.moved=true;host.setPointerCapture?.(e.pointerId);host.classList.add('photo-dragging')}
  const selection=selected[drag.side];for(const card of host.querySelectorAll('.photo-review-card')){if(!segmentHitsPhoto(drag.x,drag.y,e.clientX,e.clientY,card.getBoundingClientRect())&&card.dataset.photoId!==drag.id)continue;const id=card.dataset.photoId;drag.remove?selection.delete(id):selection.add(id);card.classList.toggle('selected',!drag.remove);card.setAttribute('aria-pressed',String(!drag.remove));card.querySelector('.photo-review-check').textContent=drag.remove?'':'✓'}drag.x=e.clientX;drag.y=e.clientY;status();
 }
 function pointerEnd(e){if(!drag||e&&e.pointerId!==undefined&&e.pointerId!==drag.pointer)return;const host=$(paneIds[drag.side]);if(drag.moved){suppressClick=true;setTimeout(()=>suppressClick=false,0)}if(host.hasPointerCapture?.(drag.pointer))host.releasePointerCapture(drag.pointer);host.classList.remove('photo-dragging');drag=null}
 for(const side of ['keep','trash']){const host=$(paneIds[side]);host.addEventListener('pointerdown',e=>pointerDown(side,e));host.addEventListener('pointermove',pointerMove);host.addEventListener('click',e=>{if(suppressClick){e.preventDefault();e.stopImmediatePropagation();suppressClick=false}},true);host.addEventListener('dragstart',e=>e.preventDefault())}
 document.addEventListener('pointerup',pointerEnd);document.addEventListener('pointercancel',pointerEnd);window.addEventListener('blur',()=>pointerEnd());
 for(const side of ['keep','trash']){let frame=null;$(paneIds[side]).addEventListener('scroll',()=>{if(!data||preparing||frame)return;frame=requestAnimationFrame(()=>{frame=null;renderPane(side)})},{passive:true})}
 let searchFrame=null;$('photoReviewSearch').oninput=()=>{cancelAnimationFrame(searchFrame);searchFrame=requestAnimationFrame(()=>{for(const id of Object.values(paneIds))$(id).scrollTop=0;render()})};
 $('photoPreviewRetry').onclick=run(()=>preparePreviews(request,true));
 $('photoKeepSelect').onclick=()=>selectAll('keep');$('photoTrashSelect').onclick=()=>selectAll('trash');$('photoTrashMove').onclick=()=>move('keep');$('photoRestore').onclick=()=>move('trash');$('photoReviewSave').onclick=run(()=>save(false));$('photoReviewDraft').onclick=run(()=>save(true));$('photoReviewExit').onclick=run(()=>navigate('review'));
 new ResizeObserver(()=>{if(S.page==='photos'&&data&&!preparing){renderPane('keep');renderPane('trash')}}).observe($('photoKeepGallery'));
 window.addEventListener('beforeunload',e=>{if(dirty||busy){e.preventDefault();e.returnValue=''}});
 document.addEventListener('keydown',e=>{if(S.page!=='photos'||busy||preparing||document.querySelector('dialog[open]'))return;if(e.key==='Escape'){e.preventDefault();pointerEnd();cancelSelection();return}if(e.target.closest('input,textarea,select,[contenteditable=true]'))return;if(e.key==='Enter'&&!e.repeat){e.preventDefault();move(selected.keep.size?'keep':'trash')}});
 return {load,canLeave,isDirty};
})();
