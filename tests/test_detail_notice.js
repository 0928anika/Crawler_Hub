'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app/static/app.js','utf8');
const render=source.slice(source.indexOf('async function renderResult()'),source.indexOf('async function poll()'));
const nodes={};
function node(tag,text,cls){return {tag,text,cls,children:[],attrs:{},classList:{toggle(){}},replaceChildren(){this.children=[]},append(...children){this.children.push(...children)},setAttribute(k,v){this.attrs[k]=v}}}
const state={item:{html:'<img src="chart.jpg">',detail_notice:'상세페이지는 색상 매핑에서 선택한 사진으로 완성됩니다.'},reviewTab:'detail',job:{selected:['detail_page']}};
const context={S:state,$:id=>nodes[id]??=node('div'),el:node};
vm.createContext(context);vm.runInContext(render+'\nthis.renderResult=renderResult;',context);
(async()=>{await context.renderResult();assert.equal(nodes.reviewContent.children[0].text,state.item.detail_notice);assert.equal(nodes.reviewContent.children[0].attrs.role,'status');assert.equal(nodes.reviewContent.children[1].tag,'iframe');delete state.item.detail_notice;await context.renderResult();assert.equal(nodes.reviewContent.children.length,1);assert.equal(nodes.reviewContent.children[0].tag,'iframe');console.log('Pending detail notice and completed detail preview passed.');})().catch(e=>{console.error(e);process.exitCode=1});

