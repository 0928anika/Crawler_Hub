const fs=require('fs'),vm=require('vm'),assert=require('assert');
const s=fs.readFileSync('app/static/app.js','utf8');
async function check({allDeleted=false,dirtySaved=false,visible=true,hasNext=true}={}){
 const before={id:'g',colors:[{id:'a'},{id:'b'}],confirmed:['a']},other={id:'other',colors:[{id:'c'}],confirmed:hasNext?[]:['c']};
 const after={id:'g',colors:allDeleted?[]:[{id:'a'}],confirmed:allDeleted?[]:['a']};
 let scrolled=0,focused=0,renders=0;
 const target={style:{},focus(){focused++},scrollIntoView(){scrolled++}},filter={value:'unselected'};
 const ctx={S:{jobId:'job',mapping:{revision:4,groups:[before,other]},mappingDrafts:{'g:a':new Set(['saved']),'g:b':new Set(['drop']),'other:c':new Set(['keep'])},mappingDirty:new Set(['g:b',...(hasNext?['other:c']:[]),...(dirtySaved?['g:a']:[])]),mappingActive:{g:'b'},mappingAddOpen:{}},mappingKey:(g,c)=>g+':'+c,renderMappingProgress(){},renderHeader(){},renderMapping(){renders++},refreshMappingProduct(){},mappingFilterView:()=>visible?[other]:[], $:id=>id==='mappingFilter'?filter:target};
 ctx.mappingGroup=id=>ctx.S.mapping.groups.find(g=>g.id===id);
 ctx.api=async(path,body)=>{if(body){assert.equal(body.action,'delete-unsaved');assert.equal(body.expected_revision,4);return {revision:5,groups:[after,other]}}return {}};
 ctx.flushMappingSelections=async()=>{};ctx.S.mappingPending={};vm.createContext(ctx);vm.runInContext(s.slice(s.indexOf('function mappingRequiredOptions('),s.indexOf('function mappingDraft(')),ctx);vm.runInContext(s.slice(s.indexOf('function nextMappingProductAfterDelete('),s.indexOf('async function changeMappingOptions(')),ctx);
 vm.runInContext(s.slice(s.indexOf('async function changeMappingOptions('),s.indexOf('async function changeMappingOptions(')+s.slice(s.indexOf('async function changeMappingOptions(')).indexOf('\n')),ctx);
 await ctx.changeMappingOptions('g','delete-unsaved');
 assert(!ctx.S.mappingDirty.has('g:b'));assert(!('g:b' in ctx.S.mappingDrafts));if(hasNext)assert.deepEqual([...ctx.S.mappingDrafts['other:c']],['keep']);
 assert.equal(scrolled,hasNext&&!dirtySaved?1:0);assert.equal(focused,scrolled);assert.equal(ctx.S.mappingBusy,false);
 if(hasNext&&!dirtySaved){assert.equal(ctx.S.mappingActive.other,'c');assert.equal(ctx.S.mappingFocused,'other');if(!visible)assert.equal(filter.value,'incomplete')}
 if(dirtySaved)assert.deepEqual([...ctx.S.mappingDrafts['g:a']],['saved']);
}
(async()=>{await check();await check({allDeleted:true});await check({dirtySaved:true});await check({visible:false});await check({hasNext:false});console.log('Bulk cleanup preserves saved/draft selections and scrolls to the next pending product, including after deleting every option');})().catch(e=>{console.error(e);process.exitCode=1});
