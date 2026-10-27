import test from 'node:test';
import assert from 'node:assert/strict';
import {copyFile, mkdir, mkdtemp, readFile, realpath, rm, stat, symlink, writeFile} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {resolveConfig, type UserConfig} from 'vite';

const repositoryRoot = fileURLToPath(new URL('../../', import.meta.url));

test('checkouts sharing installed dependencies cannot overwrite each other’s Vite cache', async t => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), 'workspace-vite-cache-'));
  t.after(() => rm(temporary, {recursive: true, force: true}));
  const dependencies = await realpath(path.join(repositoryRoot, 'node_modules'));
  const environmentKeys = ['JOB_WORKSPACE_LOCAL', 'WRANGLER_WRITE_LOGS', 'WRANGLER_LOG_PATH', 'MINIFLARE_REGISTRY_PATH'];
  const previousEnvironment = Object.fromEntries(environmentKeys.map(key => [key, process.env[key]]));
  // Evaluate configuration only: never bootstrap an operator's local runtime.
  process.env.JOB_WORKSPACE_LOCAL = '0';
  t.after(() => {
    for (const [key, value] of Object.entries(previousEnvironment)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  });

  const caches: string[] = [];
  const configuredCaches: string[] = [];
  for (const name of ['first', 'second']) {
    const root = path.join(temporary, name);
    await mkdir(root);
    await writeFile(path.join(root, 'package.json'), '{"private":true,"type":"module"}\n');
    await symlink(dependencies, path.join(root, 'node_modules'), 'dir');
    await symlink(path.join(repositoryRoot, 'local'), path.join(root, 'local'), 'dir');
    const configFile = path.join(root, 'vite.config.ts');
    await copyFile(path.join(repositoryRoot, 'vite.config.ts'), configFile);

    // Import the actual project configuration, retaining its import.meta.url
    // semantics in each checkout. Resolve its cache policy through Vite itself
    // without running application/Cloudflare plugin hooks or opening a server.
    const {default: exported} = await import(pathToFileURL(configFile).href);
    const projectConfig: UserConfig = await (typeof exported === 'function'
      ? exported({command: 'serve', mode: 'test'}) : exported);
    const resolved = await resolveConfig({
      root, configFile: false, envFile: false, logLevel: 'silent',
      cacheDir: projectConfig.cacheDir, server: {host: '127.0.0.1'},
    }, 'serve', 'test');
    await mkdir(resolved.cacheDir, {recursive: true});
    configuredCaches.push(resolved.cacheDir);
    caches.push(await realpath(resolved.cacheDir));
    await assert.rejects(stat(path.join(root, '.private')), {code: 'ENOENT'});
  }

  // Vite's default cache directories resolve to the same physical directory
  // through these symlinks, which caused independently running previews to mix
  // their optimized React modules. Compare physical paths, not lexical paths.
  assert.notEqual(caches[0], caches[1], 'separate checkouts must own separate dependency caches');
  for (const cache of configuredCaches) {
    // Vinext's CommonJS transform recognizes optimized dependencies by this
    // namespace; moving them elsewhere transforms already-optimized modules.
    assert.match(cache.split(path.sep).join('/'), /\/node_modules\/\.vite\//);
  }
  for (const [index, cache] of caches.entries()) {
    await mkdir(path.join(cache, 'deps'), {recursive: true});
    await writeFile(path.join(cache, 'deps', 'react.js'), `export const checkout = ${index};\n`);
  }
  assert.equal(await readFile(path.join(caches[0], 'deps', 'react.js'), 'utf8'), 'export const checkout = 0;\n');
  assert.equal(await readFile(path.join(caches[1], 'deps', 'react.js'), 'utf8'), 'export const checkout = 1;\n');
  // The cache may be namespaced inside shared node_modules for Vinext's
  // optimizer compatibility, so clean its generated files separately.
  t.after(async () => {
    for (const cache of new Set(caches)) await rm(cache, {recursive: true, force: true});
  });
});
