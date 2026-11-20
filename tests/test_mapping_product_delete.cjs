const fs=require('fs'),vm=require('vm'),assert=require('assert');
const s=fs.readFileSync('app/static/app.js','utf8');
(async()=>{
 const before={id:'a',code:'DELETE',colors:[{id:'c'}],confirmed:[]},after={id:'b',code:'KEEP',colors:[{id:'d'}],confirmed:[]};let scroll=0,calls=0;
 const target={style:{},focus(){},scrollIntoView(){scroll++}};
 const ctx={S:{jobId:'job',mapping:{revision:1,groups:[before,after]},mappingDrafts:{'a:c':new Set(['delete']),'b:d':new Set(['keep'])},mappingDirty:new Set(['a:c','b:d']),mappingActive:{a:'c'},mappingAddOpen:{},mappingFocused:'a'},mappingKey:(g,c)=>g+':'+c,confirm:()=>false,renderMappingProgress(){},renderMapping(){},renderHeader(){},refreshMappingProduct(){},mappingFilterView:()=>[after],$:()=>target};
 ctx.mappingGroup=id=>ctx.S.mapping.groups.find(g=>g.id===id);
 ctx.api=async(path,body)=>{if(body){calls++;assert.equal(body.action,'delete-product');return {revision:2,groups:[after],deleted_groups:[before]}}return {}};
 vm.createContext(ctx);
 vm.runInContext(s.slice(s.indexOf('function mappingRequiredOptions('),s.indexOf('function mappingDraft(')),ctx);
 vm.runInContext(s.slice(s.indexOf('function nextMappingProductAfterDelete('),s.indexOf('async function changeMappingOptions(')),ctx);
 const start=s.indexOf('async function changeMappingOptions(');vm.runInContext(s.slice(start,start+s.slice(start).indexOf('\n')),ctx);
 await ctx.deleteMappingProduct('a');assert.equal(calls,0);
 ctx.confirm=()=>true;await ctx.deleteMappingProduct('a');
 assert.equal(calls,1);assert(!ctx.S.mappingDirty.has('a:c'));assert(!('a:c' in ctx.S.mappingDrafts));assert.deepEqual([...ctx.S.mappingDrafts['b:d']],['keep']);
 assert.equal(ctx.S.mappingFocused,'b');assert.equal(scroll,1);assert.equal(ctx.S.mappingBusy,false);
 assert(s.includes("button('품번 삭제'"));console.log('Product deletion removes only its drafts, preserves other selections, and scrolls to next product.');
})().catch(e=>{console.error(e);process.exitCode=1});
