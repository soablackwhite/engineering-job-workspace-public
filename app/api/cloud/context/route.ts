import {authorizeRequest} from '@/db';
import {getDocuments} from '@/db';
import {contextKey,readContext} from '@/lib/cloud-tools';
const headers={'Cache-Control':'private, no-store'};
export async function GET(request:Request){const denied=authorizeRequest(request);if(denied)return denied;try{const c=await readContext();return Response.json(c,{headers});}catch{return Response.json({error:'Cloud context unavailable'},{status:404,headers});}}
export async function PUT(request:Request){const denied=authorizeRequest(request);if(denied)return denied;
 if(request.headers.get('sec-fetch-site')==='cross-site')return new Response(null,{status:403});
 try{const raw=await request.text();if(new TextEncoder().encode(raw).length>350000)return new Response(null,{status:413});const c=JSON.parse(raw);if(c.schema_version!==1||!c.manifest?.approved_cv_sha256?.match(/^[a-f0-9]{64}$/)||typeof c.instructions!=='string'||!c.notes||Object.keys(c.notes).length>30||Object.values(c.notes).some(v=>typeof v!=='string'))return Response.json({error:'Invalid cloud snapshot'},{status:400});
 if(!await getDocuments().head(c.manifest.approved_cv_sha256+'.pdf'))return Response.json({error:'Upload the exact approved CV first'},{status:400});
 await getDocuments().put(contextKey,raw,{httpMetadata:{contentType:'application/json'}});return Response.json({snapshot_sha256:c.manifest.snapshot_sha256,approved_cv_sha256:c.manifest.approved_cv_sha256},{headers});
 }catch{return Response.json({error:'Cloud snapshot not saved'},{status:503,headers});}
}
