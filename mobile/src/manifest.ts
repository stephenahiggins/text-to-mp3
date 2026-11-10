export const MAX_AUDIO_BYTES = 150 * 1024 * 1024;
export type Chapter = { document: number; title: string; start: number; end: number };
export type Document = { id: string; title: string; date: string; author: string; url: string; prepared: string };
export type Manifest = {
  schemaVersion: 1; id: string; stackId: string; revision: number; title: string;
  generatedAt: number; chapters: Chapter[]; documents: Document[];
  audio: { file: 'audio.mp3'; bytes: number; sha256: string };
};
export function parseManifest(value: unknown): Manifest {
  if (!value || typeof value !== 'object') throw Error('Invalid stack manifest.');
  const m = value as Manifest;
  const text = (v: unknown, max = 2000000) => typeof v === 'string' && v.length <= max;
  if (m.schemaVersion !== 1) throw Error('This stack format is not supported. Update Reading Stacks.');
  if (typeof m.id !== 'string' || !/^[a-zA-Z0-9-]{1,80}$/.test(m.id) || !text(m.stackId, 80) || !text(m.title, 500)
    || !Number.isInteger(m.revision) || m.revision < 1 || !Number.isFinite(m.generatedAt)
    || m.audio?.file !== 'audio.mp3' || !Number.isInteger(m.audio.bytes) || m.audio.bytes < 1
    || m.audio.bytes > MAX_AUDIO_BYTES || !/^[a-f0-9]{64}$/.test(m.audio.sha256)
    || !Array.isArray(m.documents) || m.documents.length > 500
    || !Array.isArray(m.chapters) || m.chapters.length > 501) throw Error('Invalid stack or audio exceeds the 150 MB import limit.');
  for (const d of m.documents) {
    if (!d || !['id', 'title', 'date', 'author', 'url', 'prepared'].every(k => text(d[k as keyof Document]))) throw Error('Invalid document.');
  }
  let end = 0;
  for (const c of m.chapters) {
    if (!c || !text(c.title, 500) || !Number.isInteger(c.document) || c.document < 0 || c.document > m.documents.length
      || !Number.isFinite(c.start) || !Number.isFinite(c.end) || c.start < end || c.end <= c.start) throw Error('Invalid chapter timestamps.');
    end = c.end;
  }
  return m;
}

// Only URLs created by the transfer service are accepted. No credentials, query
// strings, redirects to arbitrary resources, or manifest-supplied download URLs.
export function transferBase(input: string): string {
  let raw = input.trim();
  if (raw.startsWith('articlestacks://receive?')) raw = new URL(raw).searchParams.get('url') || '';
  const url = new URL(raw);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash
    || !/^\/t\/[A-Za-z0-9_-]{40,80}\/manifest$/.test(url.pathname)) throw Error('Paste or scan a Send to Phone transfer link.');
  return url.href.slice(0, -'manifest'.length);
}

// ID3v2.3/v2.4 text tags, with safe bounds; the OS handles MP3 decoding.
export function readMp3Tags(bytes: Uint8Array): { title?: string; artist?: string; album?: string } {
  const result: { title?: string; artist?: string; album?: string } = {};
  if (String.fromCharCode(...bytes.slice(0, 3)) !== 'ID3' || ![3, 4].includes(bytes[3])) return result;
  const sync = (p: number) => ((bytes[p] & 127) * 2097152) + ((bytes[p + 1] & 127) * 16384) + ((bytes[p + 2] & 127) * 128) + (bytes[p + 3] & 127);
  const uint = (p: number) => bytes[p] * 16777216 + bytes[p + 1] * 65536 + bytes[p + 2] * 256 + bytes[p + 3];
  const limit = Math.min(bytes.length, 10 + sync(6));
  // Unsynchronisation and extended headers need a full ID3 parser; use filename fallback.
  if (bytes[5] & 0xc0) return result;
  for (let p = 10; p + 10 <= limit;) {
    const key = String.fromCharCode(...bytes.slice(p, p + 4));
    const length = bytes[3] === 4 ? sync(p + 4) : uint(p + 4);
    if (length < 1 || p + 10 + length > limit) break;
    const field = ({ TIT2: 'title', TPE1: 'artist', TALB: 'album' } as const)[key as 'TIT2'];
    if (field && bytes[p + 9] === 0) {
      const encoding = bytes[p + 10];
      const data = bytes.slice(p + 11, p + 10 + length);
      let value = '';
      if (encoding === 0) value = Array.from(data, c => String.fromCharCode(c)).join('');
      else if (encoding === 3) value = new TextDecoder().decode(data);
      else if (encoding === 1 || encoding === 2) {
        const little = encoding === 1 && data[0] === 255 && data[1] === 254;
        const start = encoding === 1 && ((data[0] === 255 && data[1] === 254) || (data[0] === 254 && data[1] === 255)) ? 2 : 0;
        for (let i = start; i + 1 < data.length; i += 2) value += String.fromCharCode(little ? data[i] + data[i + 1] * 256 : data[i] * 256 + data[i + 1]);
      }
      if (value) result[field] = value.replace(/\0/g, '').trim();
    }
    p += 10 + length;
  }
  return result;
}
