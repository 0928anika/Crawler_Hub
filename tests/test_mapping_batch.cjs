const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('app/static/app.js','utf8'),store=new Map();
const group=()=>({id:'g',colors:[{id:'a'},{id:'b'}],original_colors:[{id:'a'},{id:'b'}],confirmed:[],images:[{id:'x',assigned_colors:[]},{id:'y',assigned_colors:[]}],orders:{}});
let calls=0,fail=false;
const S={jobId:'job',mapping:{revision:0,groups:[group()]},mappingDrafts:{},mappingPending:{},mappingDirty:new Set(),mappingActive:{},mappingBusy:false};
const ctx={S,localStorage:{setItem:(k,v)=>store.set(k,v),getItem:k=>store.get(k),removeItem:k=>store.delete(k)},toast:()=>{},renderMappingProgress:()=>{},refreshMappingProduct:()=>{},mappingFilterView:()=>S.mapping.groups,api:async(url,data)=>{calls++;assert(url.endsWith('/mapping/colors'));if(fail)throw Error('offline');return {revision:S.mapping.revision+1,groups:JSON.parse(JSON.stringify(S.mapping.groups)),saved_colors:2,total_colors:2,finalized:false,unassigned:0,fingerprint:'f'};}};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('const mappingKey='),source.indexOf('async function openMapping(')),ctx);
ctx.mappingGroup=id=>S.mapping.groups.find(g=>g.id===id);
vm.runInContext(source.slice(source.indexOf('const mappingPendingKey='),source.indexOf('async function mappingEnter(')),ctx);
(async()=>{
 S.mappingDrafts['g:a']=new Set(['y','x']);S.mappingDirty.add('g:a');await ctx.saveMappingOption('g','a');
 assert.equal(calls,0);assert.equal(S.mappingActive.g,'b');assert(store.size);assert.deepEqual(Array.from(S.mapping.groups[0].orders.a),['y','x']);
 S.mapping.groups=[group()];S.mappingDrafts={};S.mappingPending={};ctx.restoreMappingPending();assert(S.mapping.groups[0].confirmed.includes('a'));assert.deepEqual(Array.from(S.mappingDrafts['g:a']),['y','x']);
 S.mappingDrafts['g:b']=new Set(['x']);await ctx.saveMappingOption('g','b');assert.equal(calls,1);assert.equal(store.size,0);assert.equal(Object.keys(S.mappingPending).length,0);
 S.mappingDrafts['g:a']=new Set(['x']);await ctx.saveMappingColor('g','a');fail=true;await assert.rejects(ctx.flushMappingSelections(),/offline/);assert.equal(Object.keys(S.mappingPending).length,1);assert(store.size);assert(!S.mappingBusy);
 fail=false;await ctx.flushMappingSelections();assert.equal(Object.keys(S.mappingPending).length,0);
 console.log('Batch mapping: immediate next option, one request per product, recovery and failed-request retry passed');
})().catch(e=>{console.error(e);process.exitCode=1});
