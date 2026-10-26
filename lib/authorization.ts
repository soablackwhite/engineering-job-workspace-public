/**
 * Boundary for the authenticated Sites gateway, not a standalone login system.
 * A deployer must block direct access and let only that trusted gateway inject
 * identity headers. Never enable this mode on a public, unprotected Worker.
 */
export type OwnerAuthConfig = {
  WORKSPACE_AUTH_MODE?: string;
  WORKSPACE_OWNER_ID?: string;
};

export function ownerAuthorizationFailure(request:Request,config:OwnerAuthConfig):Response|null {
  const headers = {'Cache-Control':'private, no-store'};
  if(config.WORKSPACE_AUTH_MODE!=='trusted-sites'||!config.WORKSPACE_OWNER_ID?.trim()) {
    return Response.json({error:'Private workspace authentication is not configured.'},{status:503,headers});
  }
  if(request.headers.get('sec-fetch-site')==='cross-site') {
    return Response.json({error:'Invalid origin'},{status:403,headers});
  }
  if(request.headers.get('oai-authenticated-user-id')!==config.WORKSPACE_OWNER_ID) {
    return Response.json({error:'Authenticated workspace owner required'},{status:401,headers});
  }
  return null;
}
