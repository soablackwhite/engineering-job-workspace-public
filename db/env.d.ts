declare namespace Cloudflare {
  interface Env {
    DB: D1Database;
    DOCUMENTS: R2Bucket;
    JOB_PIPELINE_LIVE_ENABLED?: string;
    WORKSPACE_AUTH_MODE?: string;
    WORKSPACE_OWNER_ID?: string;
  }
}
