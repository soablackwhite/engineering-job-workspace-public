import {authorizeRequest} from '@/db';
import {handleMcp} from '@/lib/cloud-mcp';
import {callCloudTool} from '@/lib/cloud-tools';
export async function POST(request:Request){const denied=authorizeRequest(request);if(denied)return denied;return handleMcp(request,(name,args)=>callCloudTool(name,args,request.url));}
