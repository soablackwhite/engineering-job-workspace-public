import {authorizeRequest} from '@/db';
import {readWorkspace} from '@/lib/workspace-store';
export async function GET(request:Request){const denied=authorizeRequest(request);if(denied)return denied;try{return Response.json(await readWorkspace(),{headers:{'Cache-Control':'no-store'}});}catch{return Response.json({error:'The application pipeline could not be loaded. Please retry.'},{status:503,headers:{'Cache-Control':'no-store'}});}}
