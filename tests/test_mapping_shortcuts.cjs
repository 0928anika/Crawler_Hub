const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('app/static/app.js','utf8');
const ctx={S:{page:'mapping',mapping:{},mappingDrafts:{},mappingDirty:new Set(),mappingActive:{g:'a'},mappingFocused:null,mappingBusy:false},document:{querySelector:()=>null},toast:()=>{},refreshMappingProduct:()=>{}};
const group={id:'g',colors:[{id:'a',label:'red'},{id:'b',label:'blue'}],confirmed:[],images:[{id:'x',assigned_colors:[]},{id:'y',assigned_colors:['b']},{id:'z',assigned_colors:[]}]};
ctx.mappingGroup=()=>group;ctx.mappingFilterView=()=>[group];
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('const mappingKey='),source.indexOf('async function openMapping(')),ctx);
vm.runInContext(source.slice(source.indexOf('async function saveMappingOption('),source.indexOf('function mappingPointerDown(')),ctx);
let saves=0,complete;
ctx.saveMappingColor=async(gid,cid)=>{saves++;ctx.S.mappingBusy=true;await new Promise(r=>complete=r);group.confirmed.push(cid);ctx.S.mappingDirty.delete('g:'+cid);ctx.S.mappingBusy=false;};
function event(values={}){return {key:'Enter',target:{closest:()=>null},preventDefault(){this.prevented=true},...values};}
(async()=>{
 const first=event();await ctx.mappingEnter(first);assert(first.prevented);assert.deepEqual([...ctx.mappingDraft(group,'a')],['x','z']);assert.equal(saves,0);
 const second=ctx.mappingEnter(event());assert.equal(saves,1);await ctx.mappingEnter(event());assert.equal(saves,1);complete();await second;assert.equal(ctx.S.mappingActive.g,'b');
 console.log('Enter: empty selection selects remaining; next Enter saves once and advances option');
 for(const e of [event({repeat:true}),event({isComposing:true}),event({keyCode:229}),event({ctrlKey:true}),event({target:{closest:selector=>selector.includes('input')?{}:null}})]){await ctx.mappingEnter(e);assert.equal(saves,1);}
 ctx.document.querySelector=()=>({open:true});await ctx.mappingEnter(event());assert.equal(saves,1);ctx.document.querySelector=()=>null;
 ctx.S.mappingDrag={dragged:true};await ctx.mappingEnter(event());assert.equal(saves,1);ctx.S.mappingDrag=null;
 ctx.S.page='review';await ctx.mappingEnter(event());assert.equal(saves,1);ctx.S.page='mapping';
 console.log('Typing, IME, held Enter, dialogs, active drag and other pages do not save');
 const rect={left:100,right:200,top:100,bottom:200};assert(ctx.segmentHitsPhoto(0,150,300,150,rect));assert(!ctx.segmentHitsPhoto(0,0,50,50,rect));assert(ctx.segmentHitsPhoto(150,0,150,300,rect));
 console.log('Fast drag segments include crossed middle photos and ignore untouched photos');
 ctx.S.mappingActive.g='a';ctx.S.mappingDrafts={'g:a':new Set(['x']),'g:b':new Set(['y'])};await ctx.mappingEnter(event({key:'Escape'}));assert.equal(ctx.mappingDraft(group,'a').size,0);assert.deepEqual([...ctx.mappingDraft(group,'b')],['y']);assert(ctx.S.mappingDirty.has('g:a'));assert.equal(saves,1);console.log('Escape clears only the active option without saving');
})().catch(e=>{console.error(e);process.exitCode=1});
