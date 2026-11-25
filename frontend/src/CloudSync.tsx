import React, {useEffect, useState} from 'react';

type Status = {enabled: boolean; folder: string; autoAudio: boolean; lastSync: number | null; error: string};
export function CloudSync() {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function call(path = '', method = 'GET', body?: unknown) {
    const response = await fetch('/api/cloud' + path, {method, headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not configure iCloud sync.');
    return data;
  }
  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {const data = await call(); if (alive) setStatus(data);}
      catch (e) {if (alive) setError((e as Error).message);}
    };
    void refresh(); const timer = setInterval(refresh, 5000);
    return () => {alive = false; clearInterval(timer);};
  }, []);
  async function update(path: string, method: string, body?: unknown) {
    setBusy(true); setError('');
    try {const data = await call(path, method, body); if (!data.cancelled) setStatus(data);}
    catch (e) {setError((e as Error).message);}
    finally {setBusy(false);}
  }
  return <section className="card" aria-labelledby="cloud-heading">
    <h2 id="cloud-heading">Send from anywhere with iCloud Drive</h2>
    <p>Select a new, empty Reading Stacks folder in iCloud Drive on this Mac. On your iPhone, open Mac connection → iCloud Drive and select the same folder.</p>
    <p>Use the same Apple Account on both devices. Shares wait in iCloud until this Mac is awake and Reading Stacks is running. Open the phone app to sync completed audio into its library.</p>
    <button className="primary" disabled={busy || !status} onClick={() => update('/choose', 'POST')}>{busy ? 'Please wait…' : status?.enabled ? 'Choose another folder' : 'Choose iCloud folder'}</button>
    {status?.enabled && <>
      <p role="status">Connected to {status.folder}</p>
      <label><input type="checkbox" checked={status.autoAudio} disabled={busy} onChange={e => update('', 'POST', {path: status.folder, autoAudio: e.target.checked})}/> Generate audio automatically for incoming stacks</label>
      <p className="hint">Automatic audio uses George at normal speed, without optional AI briefings. You can also generate audio in the stack editor. Completed recordings sync to your personal iCloud folder.</p>
      {status.lastSync && <p className="hint">Last checked: {new Date(status.lastSync * 1000).toLocaleTimeString()}. iCloud delivery can take longer.</p>}
      <button disabled={busy} onClick={() => update('', 'DELETE')}>Disconnect iCloud sync</button>
      <p className="hint">Disconnecting leaves your files in iCloud. Choose the original folder to resume.</p>
    </>}
    {(error || status?.error) && <p role="alert" className="error">{error || status?.error}</p>}
  </section>;
}
