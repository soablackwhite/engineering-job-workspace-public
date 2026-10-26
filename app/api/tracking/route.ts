import {authorizeRequest} from '@/db';
import {getDb} from '@/db';
import {statuses} from '@/lib/crm';
import {readWorkspace} from '@/lib/workspace-store';
export async function GET(request:Request){const denied=authorizeRequest(request);if(denied)return denied;try {const {tracking}=await readWorkspace();return Response.json(Object.entries(tracking).map(([id,t])=>({...t,id})),{headers:{'Cache-Control':'no-store'}});}catch{return Response.json({error:'Your saved tracking could not be loaded. Please retry.'},{status:503});}}
export async function PATCH(request:Request){const denied=authorizeRequest(request);if(denied)return denied;
 if(request.headers.get('sec-fetch-site')==='cross-site')return Response.json({error:'Invalid request'},{status:403});
 try {const b=await request.json() as Record<string,unknown>;const {jobs}=await readWorkspace();if(typeof b.id!=='string'||!jobs.some(j=>j.id===b.id)||!statuses.includes(String(b.status))||typeof b.notes!=='string'||b.notes.length>20000||typeof b.followUp!=='string'||(b.followUp!==''&&(!/^\d{4}-\d{2}-\d{2}$/.test(b.followUp)||isNaN(Date.parse(b.followUp)))))return Response.json({error:'Please check the tracking fields.'},{status:400});
 const updatedAt=new Date().toISOString();await getDb().prepare('INSERT INTO tracking (id,status,notes,follow_up,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,notes=excluded.notes,follow_up=excluded.follow_up,updated_at=excluded.updated_at').bind(b.id,b.status,b.notes,b.followUp,updatedAt).run();return Response.json({...b,updatedAt});
 }catch{return Response.json({error:'Changes were not saved. Please retry.'},{status:503});}
}
