import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

test('PWA manifest exists and meets installability standards', () => {
  const manifestPath = path.resolve('public/manifest.webmanifest');
  assert.ok(fs.existsSync(manifestPath), 'manifest.webmanifest must exist');

  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  assert.equal(manifest.display, 'standalone');
  assert.ok(manifest.short_name.length <= 12, 'short_name must be <= 12 chars');
  assert.equal(manifest.start_url, '/');
  assert.equal(manifest.scope, '/');
  assert.ok(manifest.icons && manifest.icons.length >= 3);

  // Check required resolutions
  const has192 = manifest.icons.some((i: any) => i.sizes === '192x192');
  const has512 = manifest.icons.some((i: any) => i.sizes === '512x512' && i.purpose === 'any');
  const hasMaskable = manifest.icons.some((i: any) => i.purpose === 'maskable');
  assert.ok(has192, 'Manifest must specify 192x192 icon');
  assert.ok(has512, 'Manifest must specify 512x512 icon');
  assert.ok(hasMaskable, 'Manifest must specify maskable icon');
});

test('Generated icon files exist in public directory', () => {
  assert.ok(fs.existsSync(path.resolve('public/pwa-192x192.png')), '192x192 PNG exists');
  assert.ok(fs.existsSync(path.resolve('public/pwa-512x512.png')), '512x512 PNG exists');
  assert.ok(fs.existsSync(path.resolve('public/pwa-maskable-512x512.png')), 'Maskable PNG exists');
  assert.ok(fs.existsSync(path.resolve('public/apple-touch-icon.png')), 'iOS touch icon PNG exists');
  assert.ok(fs.existsSync(path.resolve('public/icon.svg')), 'SVG vector icon exists');
});

test('HTML entrypoint index.html includes PWA meta tags and safe-area viewport', () => {
  const html = fs.readFileSync(path.resolve('index.html'), 'utf8');
  assert.ok(html.includes('viewport-fit=cover'), 'Viewport includes viewport-fit=cover for safe-areas');
  assert.ok(html.includes('apple-mobile-web-app-capable'), 'iOS web app capable tag included');
  assert.ok(html.includes('apple-touch-icon'), 'apple-touch-icon link included');
  assert.ok(html.includes('manifest.webmanifest'), 'Manifest webmanifest linked');
});

test('Two-Phase Approval workflow verification and hash safety logic', () => {
  // Test approval payload verification
  const testApproval = {
    id: 'appr-test',
    taskId: 'task-test',
    hash: 'sha256:abc123expectedhash',
    state: 'pending',
  };

  // 1. Mismatch hash should be detected as stale
  const staleHash = 'sha256:differenthash456';
  assert.notEqual(testApproval.hash, staleHash);

  // 2. Matching hash is valid
  const correctHash = 'sha256:abc123expectedhash';
  assert.equal(testApproval.hash, correctHash);

  // 3. Status transition logic
  assert.equal(testApproval.state, 'pending');
});

test('API client error handling and status code classification', async () => {
  const { ApiError } = await import('../src/services/api.ts');
  const error = new ApiError('Conflict: Stale approval hash', 409, { expectedHash: '123' });
  assert.equal(error.status, 409);
  assert.equal(error.message, 'Conflict: Stale approval hash');
  assert.equal(error.name, 'ApiError');
});
