/** Add local CSRF proof only to same-origin workspace API writes. */
export function createWorkspaceFetch(fetcher: typeof fetch = globalThis.fetch) {
  let setup: Promise<string | null> | undefined;
  const csrf = () => {
    setup ??= fetcher('/api/setup', {cache:'no-store', credentials:'same-origin'})
      .then(async response => {
        if (!response.ok) return null;
        const state = await response.json() as {mode?:unknown;csrfToken?:unknown};
        return state.mode === 'local' && typeof state.csrfToken === 'string'
          ? state.csrfToken : null;
      }).catch(() => null);
    return setup;
  };
  return async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const method = (init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    // Deliberately restrict proof to the relative paths used by this app. Never
    // attach it to a supplied absolute URL, protocol-relative URL or MCP server.
    const internal = typeof input === 'string' && /^\/api\/(tracking|workflow|workspace)(?:[/?]|$)/.test(input);
    let options = init;
    if (internal && !['GET','HEAD','OPTIONS'].includes(method)) {
      const token = await csrf();
      if (token) {
        const headers = new Headers(init.headers);
        headers.set('X-Workspace-CSRF', token);
        options = {...init, headers};
      }
    }
    const response = await fetcher(input, options);
    if (internal && [401,403].includes(response.status)) setup = undefined;
    // A failed write is never replayed automatically: its outcome may be uncertain.
    return response;
  };
}

export const workspaceFetch = createWorkspaceFetch();
