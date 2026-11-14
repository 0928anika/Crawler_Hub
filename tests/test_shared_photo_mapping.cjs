const fs=require('fs'),vm=require('vm'),assert=require('assert');
const s=fs.readFileSync('app/static/app.js','utf8');
const part=s.slice(s.indexOf('const mappingKey='),s.indexOf('async function openMapping('));
const context={S:{mappingDrafts:{},mappingDirty:new Set()}};vm.createContext(context);vm.runInContext(part,context);
const g={id:'g',colors:[{id:'a'},{id:'b'}],images:[{id:'x',assigned_to:'a',assigned_colors:['a']}]};
context.toggleMappingPhoto(g,'b','x');assert(context.mappingDraft(g,'a').has('x'));assert(context.mappingDraft(g,'b').has('x'));
context.toggleMappingPhoto(g,'a','x');assert(!context.mappingDraft(g,'a').has('x'));assert(context.mappingDraft(g,'b').has('x'));
console.log('Shared mapping UI: selecting and deselecting one color preserves other color choices');


const filters=require("../app/static/mapping-filters.js");
assert.deepEqual(filters.status({...g,confirmed:["a","b"],images:[{id:"x",assigned_to:"a",assigned_colors:["a","b"]}]},{"g:a":new Set()}),{complete:true,unassigned:0});

vm.runInContext(s.slice(s.indexOf('async function saveMappingColor('),s.indexOf('async function saveMappingDrafts(')),context);
let posts=0;context.mappingGroup=()=>g;context.renderMappingProgress=()=>{};context.api=async()=>{posts++};
(async()=>{await assert.rejects(context.saveMappingColor('g','a'),/최소 1장/);assert.equal(posts,0);assert.equal(context.S.mappingBusy,false);console.log('Empty option save blocked before API request');})().catch(e=>{console.error(e);process.exitCode=1});
