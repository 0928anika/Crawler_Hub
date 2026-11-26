const AM={job:null,file:null,fileName:'',columns:null,payload:null,preview:null,busy:false,request:0};
function resetAmMappingForJob(force=false){if(!force&&AM.job===S.jobId)return;AM.job=S.jobId;AM.file=null;AM.fileName='';AM.columns=null;AM.payload=null;AM.preview=null;AM.request++;}
function renderAmSummary(){const node=$('mappingAmSummary');if(node)node.textContent=S.mapping?.am_import?'AM 요청 · '+S.mapping.am_import.source_name:'';}
function amField(label,node){const wrap=el('label',undefined,'am-field');wrap.append(el('span',label),node);return wrap;}
function amSelect(id,values){const select=el('select');if(id)select.id=id;for(const [value,label] of values){const option=el('option',label);option.value=String(value);select.append(option);}return select;}
function amInvalidate(){AM.request++;AM.payload=null;AM.columns=null;AM.preview=null;$('amApply').disabled=true;$('amPreview').replaceChildren();$('amColumns').replaceChildren();}
function openAmImport(){if(S.mappingBusy)return;resetAmMappingForJob();amInvalidate();$('amText').value='';AM.file=null;AM.fileName='';$('amFile').value='';$('amFileName').textContent='선택한 파일 없음';$('amImportDialog').showModal();}
function bindAmMapping(){
 if($('amImportDialog'))return;
 const dialog=el('dialog');dialog.id='amImportDialog';const head=el('div',undefined,'dialog-header');head.append(el('b','AM 요청 목록 불러오기'),button('닫기 ×',()=>dialog.close()));
 const body=el('div',undefined,'am-import-body');
 const file=el('input');file.type='file';file.id='amFile';file.accept='.xlsx,.xlsm,.csv,.tsv';const fileName=el('span','선택한 파일 없음','description');fileName.id='amFileName';const upload=el('div',undefined,'am-upload');upload.append(amField('엑셀 / CSV / TSV 파일 선택',file),fileName);body.append(upload);
 const text=el('textarea');text.id='amText';text.rows=4;text.placeholder='AB12345 화이트, 블랙\nAB67890 WH\nAB67890 BK';body.append(amField('또는 직접 붙여넣기',text));
 const tools=el('div',undefined,'am-import-tools');tools.append(button('인식 미리보기',loadAmPreview,'primary'));body.append(tools);
 const columns=el('div',undefined,'am-column-grid');columns.id='amColumns';const preview=el('div');preview.id='amPreview';body.append(columns,preview);
 const foot=el('div',undefined,'dialog-footer');const apply=button('AM 요청 목록 적용',applyAmImport,'primary');apply.id='amApply';apply.disabled=true;foot.append(button('취소',()=>dialog.close()),apply);dialog.append(head,body,foot);document.body.append(dialog);
 file.onchange=run(async()=>{const f=file.files[0];if(!f)return;if(f.size>10*1024*1024)throw Error('AM 파일은 10MB 이하로 선택하세요.');amInvalidate();text.value='';AM.file=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=()=>reject(Error('파일을 읽지 못했습니다.'));r.readAsDataURL(f);});AM.fileName=f.name;fileName.textContent=f.name;await loadAmPreview();});
 text.oninput=()=>{AM.file=null;AM.fileName='';file.value='';fileName.textContent='직접 입력 사용';amInvalidate();};
 $('mappingAmImport').onclick=openAmImport;dialog.addEventListener('click',closeLightboxOnBackdrop);dialog.addEventListener('contextmenu',e=>closeLightboxOnBackdrop(e,true));
 const summary=el('p',undefined,'description');summary.id='mappingAmSummary';$('mappingProgress').parentElement.append(summary);renderAmSummary();
}
async function loadAmPreview(){
 const request=++AM.request;$('amApply').disabled=true;const jid=S.jobId;
 const payload={text:$('amText').value};if(AM.file){payload.file=AM.file;payload.file_name=AM.fileName;}if(AM.columns)payload.columns=AM.columns;
 const data=await api('/api/jobs/'+jid+'/mapping/am-preview',payload);if(jid!==S.jobId||request!==AM.request)return;AM.preview=data;AM.payload=payload;AM.columns=data.columns;renderAmPreview();
}
function renderAmPreview(){
 const area=$('amPreview'),columns=$('amColumns');area.replaceChildren();columns.replaceChildren();const data=AM.preview;if(!data)return;
 for(const [key,label] of [['product','품번 열'],['color','색상 열'],['code','색상코드 열']]){const select=amSelect('amColumn-'+key,[[-1,'선택 안 함'],...data.column_options.map(o=>[o.index,o.label])]);select.value=String(data.columns[key]??-1);select.onchange=run(async()=>{AM.columns={...AM.columns,[key]:Number(select.value)};await loadAmPreview();});columns.append(amField(label,select));}
 if(data.needs_columns){area.append(el('p','품번과 색상 열을 선택하세요.','description'));return;}
 const crawled=new Map(S.mapping.groups.map(g=>[g.code.toUpperCase(),g.original_colors]));
 const table=el('table',undefined,'am-preview-table'),thead=el('thead'),tr=el('tr');for(const title of ['품번','AM 요청 옵션','크롤링된 옵션'])tr.append(el('th',title));thead.append(tr);table.append(thead);const tbody=el('tbody');const codes=Object.keys(data.products);for(const code of codes.slice(0,80)){const row=el('tr');row.append(el('td',code),el('td',data.products[code].map(c=>c.label).join(', ')),el('td',(crawled.get(code)||[]).map(c=>c.label).join(', ')||'—'));tbody.append(row);}table.append(tbody);const scroll=el('div',undefined,'am-preview-scroll');scroll.append(table);area.append(scroll);
 if(codes.length>80)area.append(el('p','처음 80개 품번 미리보기 · 전체 목록 적용','description'));
 if(data.warning_count){const details=el('details',undefined,'am-import-warnings');details.append(el('summary','확인 필요 '+data.warning_count+'행'));for(const warning of data.warnings)details.append(el('p',warning));area.append(details);}
 $('amApply').disabled=!AM.payload||!data.option_count;
}
function amCleanupDrafts(){const valid=new Set(S.mapping.groups.flatMap(g=>mappingRequiredOptions(g).map(c=>mappingKey(g.id,c.id))));for(const key of Object.keys(S.mappingDrafts))if(!S.mappingDirty.has(key)||!valid.has(key))delete S.mappingDrafts[key];for(const key of [...S.mappingDirty])if(!valid.has(key))S.mappingDirty.delete(key);}
async function applyAmImport(){
 if(!AM.payload||AM.busy||S.mappingBusy)return;await flushMappingSelections();AM.busy=true;S.mappingBusy=true;renderMappingProgress();$('amApply').disabled=true;try{
 S.mapping=await api('/api/jobs/'+S.jobId+'/mapping/am-import',{...AM.payload,columns:AM.preview.columns,expected_revision:S.mapping.revision});amCleanupDrafts();S.mappingActive={};$('amImportDialog').close();renderMapping();toast('AM 요청 옵션을 적용했습니다.');
 }finally{AM.busy=false;S.mappingBusy=false;renderMappingProgress();}
}
