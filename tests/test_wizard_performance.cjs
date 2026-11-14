const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('app/static/app.js','utf8');
const part=source.slice(source.indexOf('async function nextStep()'),source.indexOf('function renderMonitor()'));
(async()=>{
 let validations=0,posts=0,release;
 const button={disabled:false,textContent:''};
 const context={S:{step:2,validation:null},$:()=>button,brand:()=>({available:true}),renderWizard:()=>{},active:()=>false,toast:()=>{},inputData:()=>({}),resetNewForm:()=>{},openJob:async()=>{},
 validate:async()=>{validations++;await new Promise(r=>release=r);return context.S.validation={valid:true}},
 api:async(path)=>{if(path==='/api/jobs'){posts++;throw Error('simulated server error')}return {}}};
 vm.createContext(context);vm.runInContext(part,context);
 const first=context.nextStep();const duplicate=context.nextStep();
 assert.equal(validations,1);assert.equal(button.disabled,true);release();await Promise.all([first,duplicate]);
 assert.equal(context.S.step,3);assert.equal(button.disabled,false);
 await assert.rejects(context.nextStep(),/simulated/);
 assert.equal(validations,1);assert.equal(posts,1);assert.equal(button.disabled,false);assert.equal(context.S.wizardBusy,false);
 context.S.step=2;context.S.validation=null;const changed=context.nextStep();assert.equal(validations,2);release();await changed;
 console.log('Wizard: duplicate click blocked, validation reused, errors release button, changed input revalidated');
})().catch(e=>{console.error(e);process.exitCode=1});
