/* Saved confirmation and current unsaved photo choices are intentionally distinct. */
(function(root){
 'use strict';
 function status(group,drafts={},dirty=new Set()){
  const assigned=new Set();
  for(const color of group.colors){
   const key=group.id+':'+color.id;
   const selected=drafts[key]||new Set(group.images.filter(i=>(i.assigned_colors||[i.assigned_to]).includes(color.id)).map(i=>i.id));
   for(const id of selected)assigned.add(id);
  }
  const complete=group.colors.length>0&&group.colors.every(c=>group.confirmed.includes(c.id)&&!dirty.has(group.id+':'+c.id));
  return {complete,unassigned:group.images.filter(i=>!assigned.has(i.id)).length};
 }
 function matches(state,filter){
  if(filter==='incomplete')return !state.complete;
  if(filter==='unselected')return state.unassigned>0;
  if(filter==='complete')return state.complete;
  return true;
 }
 const api={status,matches};
 if(typeof module!=='undefined'&&module.exports)module.exports=api;
 else root.MappingFilters=api;
})(globalThis);
