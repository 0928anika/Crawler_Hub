const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('app/static/app.js','utf8');
const part=source.slice(source.indexOf('async function loadReview('),source.indexOf('function updateReviewNav('));
(async()=>{
 const nodes={},node=()=>({value:'',textContent:'',append(){},replaceChildren(){}});let calls=0;
 const ctx={URLSearchParams,S:{jobId:'a'.repeat(32),job:{revision:1,state:'completed',queue_revision:1,mapping_revision:1},reviewOffset:0,reviewKey:'',reviewId:null},$:id=>nodes[id]??(nodes[id]=node()),api:async()=>{calls++;if(calls===1)throw Error('temporary failure');return {rows:[],total:0}},empty(){},button(){return node()},pager(){},updateReviewNav(){}};
 vm.createContext(ctx);vm.runInContext(part,ctx);
 await assert.rejects(ctx.loadReview(),/temporary failure/);assert.equal(ctx.S.reviewKey,'');
 await ctx.loadReview();assert.equal(calls,2);await ctx.loadReview();assert.equal(calls,2);
 ctx.S.job.mapping_revision=2;await ctx.loadReview();assert.equal(calls,3);
 console.log('Review failure retries and saved mapping revision refreshes results');
})().catch(e=>{console.error(e);process.exitCode=1});
