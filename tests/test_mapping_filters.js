'use strict';
const assert=require('node:assert/strict');
const filters=require('../app/static/mapping-filters.js');
const group={id:'p',colors:[{id:'red'},{id:'blue'}],confirmed:[],images:[{id:'one',assigned_to:null},{id:'two',assigned_to:null}]};
assert.deepEqual(filters.status(group),{complete:false,unassigned:2});
assert.equal(filters.matches(filters.status(group),'incomplete'),true);
assert.equal(filters.matches(filters.status(group),'unselected'),true);
assert.equal(filters.matches(filters.status(group),'complete'),false);
const saved={...group,confirmed:['red','blue'],images:[{id:'one',assigned_to:'red'},{id:'two',assigned_to:null}]};
assert.deepEqual(filters.status(saved),{complete:true,unassigned:1}); // Intentionally excluded photos can remain after completion.
assert.equal(filters.matches(filters.status(saved),'complete'),true);
assert.equal(filters.matches(filters.status(saved),'unselected'),true);
const drafts={'p:red':new Set(['one','two'])};
assert.deepEqual(filters.status(saved,drafts,new Set(['p:red'])),{complete:false,unassigned:0});
assert.equal(filters.matches(filters.status(saved,drafts,new Set(['p:red'])),'incomplete'),true);
assert.equal(filters.matches(filters.status(saved,drafts,new Set(['p:red'])),'unselected'),false);
assert.deepEqual(filters.status({...group,colors:[]}),{complete:false,unassigned:2});
assert.equal(filters.status({...group,confirmed:['red','blue']}).complete,true); // All colors explicitly saved with zero photos.
assert.equal(filters.status(saved,{'p:red':new Set()}).unassigned,2); // An unsaved deselection is visible immediately.
assert.equal(filters.matches(filters.status(group),'all'),true);
assert.equal(filters.status(group,{'other:red':new Set(['one'])}).unassigned,2); // Other product drafts do not leak.
console.log('Mapping filters: saved/unsaved selections, exclusions, empty options and independent IDs passed.');
