import { sites } from '@openai/sites-vite-plugin';
import { existsSync } from 'node:fs';
import tailwindcss from '@tailwindcss/postcss';
import vinext from 'vinext';
import { createLogger, defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import {createLocalRuntime,localWorkspacePlugin,bootstrapLocalStorage} from './local/runtime.mjs';

const SITE_CREATOR_PLACEHOLDER_DATABASE_ID =
  '00000000-0000-4000-8000-000000000000';
// A Sites deployment binding is private operator configuration. Clean clones
// can build without it; never commit a real project ID to this repository.
const hasPrivateSitesConfig = existsSync(new URL('./.openai/hosting.json', import.meta.url));

// macOS Seatbelt blocks FSEvents, so Codex previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === 'seatbelt';

const localBindingConfig = {
  main: 'vinext/server/fetch-handler',
  compatibility_flags: ['nodejs_compat'],
  d1_databases: [
        {
          binding: 'DB',
          database_name: 'job-workspace-local',
          database_id: SITE_CREATOR_PLACEHOLDER_DATABASE_ID,
        },
      ],
  r2_buckets: [
        {
          binding: 'DOCUMENTS',
          bucket_name: 'job-workspace-local-documents',
        },
      ],
};

export default defineConfig(async () => {
  // Keep Wrangler and Miniflare state project-local. These are non-secret tool
  // settings; application environment belongs in ignored `.env*` files.
  process.env.WRANGLER_WRITE_LOGS ??= 'false';
  process.env.WRANGLER_LOG_PATH ??= '.wrangler/logs';
  process.env.MINIFLARE_REGISTRY_PATH ??= '.wrangler/registry';

  // Wrangler snapshots its log path while the Cloudflare plugin is imported.
  const { cloudflare } = await import('@cloudflare/vite-plugin');
  const local = process.env.JOB_WORKSPACE_LOCAL === '1';
  const port = Number(process.env.JOB_WORKSPACE_PORT || 4310);
  const root = fileURLToPath(new URL('.', import.meta.url));
  const runtime = local ? await createLocalRuntime({root, port, bootstrapStorage: bootstrapLocalStorage}) : null;
  const logger = createLogger();
  const redactedLogger = runtime ? Object.assign({}, logger, Object.fromEntries(['info', 'warn', 'error', 'warnOnce'].map(level => [level, (message:string, ...args:unknown[]) => {
    let safe = message;
    for (const secret of [runtime.security.ownerIdentity, runtime.security.mcpToken, runtime.security.sessionToken, runtime.security.csrfToken]) safe = safe.replaceAll(secret, '[private]');
    (logger[level as keyof typeof logger] as (...args:any[])=>void)(safe, ...args);
  }]))) : undefined;

  return {
    css: { postcss: { plugins: [tailwindcss()] } },
    customLogger: redactedLogger,
    server: {
      ...(local ? {host: '127.0.0.1', port, strictPort: true, allowedHosts: ['127.0.0.1']} : {}),
      ...(isCodexSeatbeltSandbox ? {watch: {useFsEvents: false, usePolling: true}} : {}),
      fs: {deny: ['**/.private/**', '**/.git/**', '**/.env*', '**/.openai/**', '**/workflow/runtime/**', '**/*.{pem,key,p12,pfx}']},
    },
    plugins: [
      ...(runtime ? [localWorkspacePlugin(runtime)] : []),
      vinext(),
      ...(hasPrivateSitesConfig && !local ? [sites()] : []),
      cloudflare({
        viteEnvironment: { name: 'rsc', childEnvironments: ['ssr'] },
        config: {...localBindingConfig, ...(runtime ? {vars: {WORKSPACE_AUTH_MODE: 'trusted-sites', WORKSPACE_OWNER_ID: runtime.ownerIdentity}} : {})},
        ...(runtime ? {persistState: {path: path.join(root, '.private', 'storage')}, inspectorPort: false, remoteBindings: false, tunnel: false} : {}),
      }),
    ],
  };
});
