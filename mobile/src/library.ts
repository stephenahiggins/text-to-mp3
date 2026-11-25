import * as SQLite from 'expo-sqlite';
import * as FS from 'expo-file-system/legacy';
import { File } from 'expo-file-system';
import * as Crypto from 'expo-crypto';
import { unzipSync, strFromU8 } from 'fflate';
import { MAX_AUDIO_BYTES, parseManifest, readMp3Tags, transferBase, type Manifest } from './manifest';

export type Recording = { id: string; manifest: Manifest; uri: string; position: number; added: number };
const db = SQLite.openDatabaseSync('article-stacks.db');
db.execSync('CREATE TABLE IF NOT EXISTS recordings (id TEXT PRIMARY KEY, manifest TEXT NOT NULL, uri TEXT NOT NULL, position REAL NOT NULL DEFAULT 0, added REAL NOT NULL)');
db.execSync('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value REAL NOT NULL)');
export const PLAYBACK_SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2] as const;
export type PlaybackSpeed = typeof PLAYBACK_SPEEDS[number];
export function playbackSpeed(): PlaybackSpeed {
  const value = db.getFirstSync<{ value: number }>('SELECT value FROM settings WHERE key=?', 'playbackSpeed')?.value;
  return PLAYBACK_SPEEDS.find(speed => speed === value) ?? 1;
}
export function savePlaybackSpeed(speed: PlaybackSpeed) {
  db.runSync('INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)', 'playbackSpeed', speed);
}
export function library(): Recording[] {
  return db.getAllSync<{ id: string; manifest: string; uri: string; position: number; added: number }>('SELECT * FROM recordings ORDER BY added DESC').map(r => ({ ...r, manifest: JSON.parse(r.manifest) }));
}
export function savePosition(id: string, position: number) { db.runSync('UPDATE recordings SET position=? WHERE id=?', position, id); }
export async function removeRecording(r: Recording) {
  await FS.deleteAsync(r.uri, { idempotent: true });
  db.runSync('DELETE FROM recordings WHERE id=?', r.id);
}
async function digest(bytes: Uint8Array): Promise<string> {
  const hash = await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, new Uint8Array(bytes).buffer);
  return Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('');
}
async function commit(m: Manifest, temp: string) {
  const existing = library().find(r => r.id === m.id);
  if (existing) { await FS.deleteAsync(temp, { idempotent: true }); return existing; }
  const bytes = await new File(temp).bytes();
  if (bytes.length !== m.audio.bytes || await digest(bytes) !== m.audio.sha256) throw Error('Audio verification failed. Please retry the transfer.');
  const directory = FS.documentDirectory + 'recordings/';
  await FS.makeDirectoryAsync(directory, { intermediates: true });
  const uri = directory + m.id + '.mp3';
  await FS.moveAsync({ from: temp, to: uri });
  try {
    db.runSync('INSERT INTO recordings (id,manifest,uri,added) VALUES (?,?,?,?)', m.id, JSON.stringify(m), uri, Date.now());
  } catch (error) { await FS.deleteAsync(uri, { idempotent: true }); throw error; }
  return library().find(r => r.id === m.id)!;
}
export async function receive(input: string, progress: (label: string) => void) {
  const base = transferBase(input);
  progress('Connecting to your Mac…');
  const response = await fetch(base + 'manifest', { signal: AbortSignal.timeout(15000) });
  if (!response.ok) throw Error('Transfer expired or unavailable. Click Send to Phone again on your Mac.');
  const text = await response.text();
  if (text.length > 20 * 1024 * 1024) throw Error('Manifest too large.');
  const manifest = parseManifest(JSON.parse(text));
  const temp = FS.cacheDirectory + Crypto.randomUUID() + '.mp3';
  try {
    const existing = library().find(r => r.id === manifest.id);
    if (!existing) {
      const task = FS.createDownloadResumable(base + 'audio', temp, {}, state => {
        progress(`Downloading ${Math.min(100, Math.round(state.totalBytesWritten / manifest.audio.bytes * 100))}%`);
      });
      const result = await task.downloadAsync();
      if (!result || result.status !== 200) throw Error('Download interrupted. Keep your Mac awake and retry.');
    }
    progress('Verifying audio…');
    const recording = existing || await commit(manifest, temp);
    // Receipt is sent only after the file and library entry are durable. Retrying
    // an existing download also retries a lost receipt without duplicating audio.
    try { await fetch(base + 'ack', { method: 'POST', signal: AbortSignal.timeout(5000) }); } catch { /* locally saved */ }
    return recording;
  } finally { await FS.deleteAsync(temp, { idempotent: true }); }
}
export async function importFile(uri: string, name: string, expectedId?: string): Promise<Recording> {
  const file = new File(uri);
  if (file.size > MAX_AUDIO_BYTES + 20 * 1024 * 1024) throw Error('File exceeds the import limit (150 MB audio).');
  const bytes = await file.bytes();
  const temp = FS.cacheDirectory + Crypto.randomUUID() + '.mp3';
  try {
    let manifest: Manifest;
    if (name.toLowerCase().endsWith('.mp3') || String.fromCharCode(...bytes.slice(0, 3)) === 'ID3') {
      const tags = readMp3Tags(bytes.subarray(0, 256 * 1024));
      const id = Crypto.randomUUID();
      const sha256 = await digest(bytes);
      const duplicate = library().find(r => r.manifest.audio.sha256 === sha256);
      if (duplicate) return duplicate;
      manifest = parseManifest({ schemaVersion: 1, id, stackId: id, revision: 1, title: tags.title || name.replace(/\.mp3$/i, ''), generatedAt: Date.now() / 1000, chapters: [], documents: [], audio: { file: 'audio.mp3', bytes: bytes.length, sha256 } });
      new File(temp).write(bytes);
    } else {
      const archive = unzipSync(bytes, { filter: entry => {
        if (!['manifest.json', 'audio.mp3'].includes(entry.name)) return false;
        if (entry.originalSize > (entry.name === 'audio.mp3' ? MAX_AUDIO_BYTES : 20 * 1024 * 1024)) throw Error('Stack exceeds import limits.');
        return true;
      } });
      if (!archive['manifest.json'] || !archive['audio.mp3']) throw Error('Choose an Reading Stacks .stack export or an MP3.');
      manifest = parseManifest(JSON.parse(strFromU8(archive['manifest.json'])));
      new File(temp).write(archive['audio.mp3']);
    }
    if (expectedId && manifest.id !== expectedId) throw Error('The iCloud recording does not match its catalog entry.');
    return await commit(manifest, temp);
  } finally { await FS.deleteAsync(temp, { idempotent: true }); }
}
