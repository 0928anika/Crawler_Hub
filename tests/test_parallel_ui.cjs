const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('app/static/app.js','utf8');
function part(start,end){return source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));}
function node(){return {children:[],value:'',disabled:false,textContent:'',replaceChildren(...children){this.children=children;},append(...children){this.children.push(...children);},classList:{toggle(){}}};}
(async()=>{
 const elements={},get=id=>elements[id]??(elements[id]=node());
 const brands=['a','b','c'].map(id=>({id,name:id,category:'의류',available:true}));
 const status={limit:4,active:[{id:'first',name:'작업1',brand:'a',state:'running'}],availability:{a:{allowed:false,reason:'같은 사이트 사용 중'},b:{allowed:false,reason:'크롬·브라우저 사용 중'},c:{allowed:true,reason:''}}};
 const ctx={S:{brands,brand:'c',page:'new',settings:{},concurrency:status},$:get,brand:()=>brands.find(b=>b.id===ctx.S.brand),brandLabel:b=>b.name,el:()=>node(),button:(text,fn)=>Object.assign(node(),{onclick:fn}),hide:()=>{},stateNames:{running:'실행 중'},renderGuide:()=>{},renderSummary:()=>{},renderFields:()=>{},toast:()=>{},openJob:()=>{},empty:()=>{}};
 vm.createContext(ctx);vm.runInContext(part('function brandBlockReason(','function renderGuide('),ctx);
 ctx.renderBrands();assert.deepEqual(get('brandCards').children.map(x=>x.disabled),[true,true,false]);
 ctx.selectBrand('a');assert.equal(ctx.S.brand,'c');
 ctx.api=async()=>({...status,active:[],availability:{a:{allowed:true,reason:''},b:{allowed:true,reason:''},c:{allowed:true,reason:''}}});
 await ctx.refreshConcurrency();assert.deepEqual(get('brandCards').children.map(x=>x.disabled),[false,false,false]);
 ctx.selectBrand('a');assert.equal(ctx.S.brand,'a');assert.equal(get('nextStep').disabled,false);
 ctx.api=async()=>({...status,availability:Object.fromEntries(brands.map(b=>[b.id,{allowed:false,reason:'최대 4개 작업 실행 중'}]))});
 await ctx.refreshConcurrency();assert(get('nextStep').disabled);assert(get('brandCards').children.every(x=>x.disabled));
 console.log('Parallel UI: conflicts disable cards, completion re-enables, capacity disables start');

 let finish;const q={S:{jobId:'old',job:{},queueOffset:0,queueKey:'',queue:['new-result']},$:get,URLSearchParams,api:()=>new Promise(r=>finish=r)};
 vm.createContext(q);vm.runInContext(part('async function loadQueue(','async function selectQueue('),q);
 const waiting=q.loadQueue();q.S.jobId='new';q.S.queueKey='new-key';finish({rows:['old-result'],total:1});await waiting;assert.deepEqual(q.S.queue,['new-result']);
 console.log('Late queue responses cannot replace another selected job');

 const resolvers={},navigations=[];const j={S:{},$:get,localStorage:{setItem(){}},api:path=>new Promise(r=>resolvers[path]=r),renderHeader:()=>{},navigate:async page=>navigations.push([j.S.jobId,page])};
 vm.createContext(j);vm.runInContext(part('async function openJob(','function renderHeader('),j);
 const old=j.openJob('old','monitor'),latest=j.openJob('new','monitor');resolvers['/api/jobs/new']({id:'new'});await latest;resolvers['/api/jobs/old']({id:'old'});await old;
 assert.equal(j.S.job.id,'new');assert.deepEqual(navigations,[['new','monitor']]);
 console.log('Quick job switching keeps the latest selection and monitor');
})().catch(e=>{console.error(e);process.exitCode=1});
