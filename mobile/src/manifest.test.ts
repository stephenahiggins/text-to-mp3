import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseManifest, readMp3Tags, transferBase } from './manifest.ts';

const valid = () => ({ schemaVersion: 1, id: 'abc123', stackId: 'stack1', revision: 2, title: 'Weekend', generatedAt: 100,
  audio: { file: 'audio.mp3', bytes: 200, sha256: 'a'.repeat(64) }, documents: [], chapters: [{ document: 0, title: 'Introduction', start: 0, end: 2 }] });
test('validates format versions, file paths, sizes, and chapter bounds', () => {
  assert.equal(parseManifest(valid()).revision, 2);
  assert.throws(() => parseManifest({ ...valid(), schemaVersion: 99 }));
  assert.throws(() => parseManifest({ ...valid(), id: '../../oops' }));
  assert.throws(() => parseManifest({ ...valid(), audio: { ...valid().audio, bytes: 1024 ** 3 } }));
  assert.throws(() => parseManifest({ ...valid(), chapters: [{ document: 1, title: 'Missing document', start: 0, end: 1 }] }));
  assert.throws(() => parseManifest({ ...valid(), chapters: [...valid().chapters, { document: 0, title: 'Overlap', start: 1, end: 3 }] }));
});
test('transfer URLs permit only the capability endpoint', () => {
  const url = `http://192.168.1.4:8766/t/${'a'.repeat(43)}/manifest`;
  assert.equal(transferBase(url), url.replace('manifest', ''));
  assert.equal(transferBase(`articlestacks://receive?url=${encodeURIComponent(url)}`), url.replace('manifest', ''));
  for (const bad of ['file:///etc/passwd', 'https://example.com/audio', url + '?oops=1', url.replace('192.168', 'user:pass@192.168')]) assert.throws(() => transferBase(bad));
});
test('reads the ID3 tags emitted by FFmpeg, tolerates truncated frames', () => {
  const text = new TextEncoder().encode('River walk');
  const bytes = new Uint8Array(21 + text.length);
  bytes.set([73, 68, 51, 3, 0, 0, 0, 0, 0, 11 + text.length]);
  bytes.set([84, 73, 84, 50, 0, 0, 0, text.length + 1, 0, 0, 3], 10);
  bytes.set(text, 21);
  assert.equal(readMp3Tags(bytes).title, 'River walk');
  assert.deepEqual(readMp3Tags(bytes.slice(0, 15)), {});
  bytes[20] = 1;
  bytes.set([255, 254, 82, 0, 105, 0, 118, 0, 101, 0], 21);
  assert.equal(readMp3Tags(bytes).title, 'Rive');
});
