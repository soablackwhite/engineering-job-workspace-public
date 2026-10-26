import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {projectWorkspace} from '../../lib/workspace';
import {jobFields,emptyTracking,selectJobs} from '../../lib/crm';
const filters={query:'',category:'',evidence:'',company:'',location:'',work:'',age:'',status:'',sort:'Company A–Z',due:false};
test('canonical job identity merges campaign status while preserving research details and tracking',()=>{
 const original={...Object.fromEntries(jobFields.map(k=>[k,'Synthetic research field'])),id:'example-001',Company:'Example Engineering',Role:'Example Engineer','Job / apply link':'https://www.linkedin.com/jobs/view/123?trk=research'} as import('../../lib/crm').Job;
 const workflow={jobs:{'linkedin-123':{id:'linkedin-123',company:'Same employer',title:'Engineer',sources:['https://www.linkedin.com/jobs/view/123/'],state:'rejected',application:{status:'Rejected',lifecycle_event_at:'2026-10-01T10:00:00Z'}}},paused:true};
 const manual=[{id:original.id,...emptyTracking,status:'Applied',notes:'Keep notes',followUp:'2026-10-03',updatedAt:'2026-09-20T10:00:00Z'}];const before=JSON.stringify({workflow,manual});
 const p=projectWorkspace(workflow,manual,[original]);assert.equal(p.jobs.length,1);assert.deepEqual(p.jobs[0],original);assert.equal(p.tracking[original.id].status,'Rejected');assert.equal(p.tracking[original.id].notes,'Keep notes');assert.equal(p.tracking[original.id].followUp,'2026-10-03');assert.equal(p.tracking[original.id].updatedAt,manual[0].updatedAt);assert.equal(JSON.stringify({workflow,manual}),before);
});
test('new campaign jobs appear and separate requisitions at the same employer stay separate',()=>{
 const p=projectWorkspace({jobs:{a:{id:'a',company:'Acme',title:'Engineer',sources:['https://www.linkedin.com/jobs/view/1'],state:'applied',application:{status:'Applied'}},b:{id:'b',company:'Acme',title:'Engineer',sources:['https://www.linkedin.com/jobs/view/2'],state:'interviewing',application:{status:'Assessment requested'}}}},[],[]);
 assert.equal(p.jobs.length,2);assert.equal(selectJobs(p.tracking,{...filters,status:'Applied'},'2026-10-01',p.jobs).length,1);assert.equal(p.tracking.b.status,'Assessment requested');
});
test('later manual decisions are retained; a subsequent email decision appears automatically',()=>{
 const w={id:'a',company:'Acme',title:'Engineer',state:'applied',application:{status:'Applied',lifecycle_event_at:'2026-09-20T00:00:00Z'}};
 const manual=[{id:'a',...emptyTracking,status:'Archived',updatedAt:'2026-09-21T00:00:00Z'}];assert.equal(projectWorkspace({jobs:{a:w}},manual,[]).tracking.a.status,'Archived');
 w.application={status:'Rejected',lifecycle_event_at:'2026-10-01T00:00:00Z'};assert.equal(projectWorkspace({jobs:{a:w}},manual,[]).tracking.a.status,'Rejected');
});
