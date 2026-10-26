import {authorizeRequest} from '@/db';
import {getDocuments} from '@/db';
function key(request:Request){const hash=new URL(request.url).searchParams.get('hash');if(!hash||! /^[a-f0-9]{64}$/.test(hash))throw Error('Invalid document hash');return hash;}
export async function GET(request:Request){const denied=authorizeRequest(request);if(denied)return denied;try{const hash=key(request),doc=await getDocuments().get(hash+'.pdf');if(!doc)return Response.json({error:'Document not found'},{status:404});return new Response(doc.body,{headers:{'Content-Type':'application/pdf','Content-Disposition':'inline; filename="resume-review-draft.pdf"','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}});}catch{return Response.json({error:'Document unavailable'},{status:503});}}
export async function PUT(request:Request){const denied=authorizeRequest(request);if(denied)return denied;
 if(request.headers.get('sec-fetch-site')==='cross-site')return Response.json({error:'Invalid origin'},{status:403});
 try{const hash=key(request),bytes=await request.arrayBuffer();if(bytes.byteLength>2000000||new TextDecoder().decode(bytes.slice(0,5))!=='%PDF-')return Response.json({error:'PDF up to 2 MB required'},{status:400});
 const actual=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(b=>b.toString(16).padStart(2,'0')).join('');if(actual!==hash)return Response.json({error:'Document hash mismatch'},{status:400});
 await getDocuments().put(hash+'.pdf',bytes,{httpMetadata:{contentType:'application/pdf'}});return Response.json({hash});
 }catch{return Response.json({error:'Document save failed'},{status:503});}
}
