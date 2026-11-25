import {CloudSync} from './CloudSync';
import React, {useEffect, useState} from 'react';

async function connection(method = 'GET') {
  const response = await fetch('/api/share/connect', {method, signal: AbortSignal.timeout(15000), cache: 'no-store'});
  if (!response.ok) throw new Error('Could not reach sharing on your Mac. Check that Reading Stacks is running and try again.');
  return response.json();
}

export function IPhoneSharing() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [link, setLink] = useState('');
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  useEffect(() => {
    let alive = true;
    connection().then(data => {if (alive) setEnabled(data.enabled);})
      .catch(e => {if (alive) setError(e.message);})
      .finally(() => {if (alive) setBusy(false);});
    return () => {alive = false;};
  }, []);
  async function update(method: 'POST' | 'DELETE') {
    setBusy(true); setError(''); setMessage('');
    try {
      const data = await connection(method);
      setEnabled(method === 'POST');
      setLink(data.connectionLink || '');
      setMessage(method === 'POST' ? 'Your connection link is ready. Copy it to your iPhone.' : 'iPhone sharing is disconnected.');
    } catch (e) {setError((e as Error).message);}
    finally {setBusy(false);}
  }
  async function copy() {
    try {await navigator.clipboard.writeText(link); setMessage('Connection link copied. Paste it in Add to Stack on your iPhone.');}
    catch {setMessage('Select the connection link below and copy it manually.');}
  }
  return <section className="sharing-page" aria-labelledby="sharing-title">
    <a className="sharing-back" href="#/">← Your Stacks</a>
    <div className="intro"><p className="eyebrow">IPHONE SHARING</p><h1 id="sharing-title">Save a good read, wherever you find it.</h1>
      <p>Save articles and PDFs from your iPhone’s share sheet, then sync them through your personal iCloud Drive or send directly over Wi-Fi.</p></div>
    <CloudSync/>
    <div className="sharing-grid">
      <section className="card sharing-connection" aria-labelledby="connection-title">
        <h2 id="connection-title">Your iPhone finds this Mac automatically</h2>
        <p>Open Reading Stacks on your iPhone on the same Wi-Fi. Approve the matching code here once; future connections happen automatically.</p>
        <p className="sharing-status" role="status">{enabled === null ? (busy ? 'Checking sharing…' : 'Sharing status unavailable') : enabled ? 'Sharing is enabled on this Mac' : 'Ready for your iPhone to discover this Mac'}</p>
        <p>Capture links anywhere. To send saved shares, keep this Mac awake and open Reading Stacks on your iPhone on the same trusted Wi-Fi.</p>
        <details><summary>Manual connection link</summary>
        <button className="primary" disabled={busy} onClick={() => update('POST')}>{busy ? 'Please wait…' : enabled ? 'Create a new connection link' : 'Create manual connection link'}</button>
        {enabled && <p className="hint">Creating a new link disconnects any iPhone using the previous link.</p>}
        {link && <div className="sharing-link"><label htmlFor="sharing-link">Connection link</label>
          <input id="sharing-link" readOnly value={link} spellCheck={false} onFocus={e => e.currentTarget.select()}/>
          <button onClick={copy} disabled={busy}>Copy connection link</button>
          <p className="hint">Use Universal Clipboard, or privately send this link to your iPhone. Paste it into Mac connection in Saved shares in the iPhone app.</p></div>}
        {enabled && !link && <p className="hint">Your current connection remains active. Create a new link only if you need to connect again.</p>}
        </details>
        {enabled && <button className="sharing-disconnect" disabled={busy} onClick={() => update('DELETE')}>Disconnect iPhone sharing</button>}
        {error && <p className="error" role="alert">{error}</p>}
        <p className="hint" role="status">{message}</p>
      </section>
      <section className="card sharing-instructions" aria-labelledby="share-steps-title"><h2 id="share-steps-title">From article to stack</h2>
        <ol><li><strong>Open an article on your iPhone</strong><p>In Safari, tap Share and choose Add to Stack. If it’s missing, check More in the share sheet.</p></li>
          <li><strong>Connect once</strong><p>After saving an article, open Reading Stacks. It finds this Mac automatically. Approve the matching code on your Mac once.</p></li>
          <li><strong>Pick a stack and save</strong><p>Tap Add to stack in the share sheet. Open Reading Stacks to see the saved article and choose its destination. Use Send now on home Wi-Fi to sync.</p></li></ol>
        <p className="hint">The Reading Stacks iPhone app with Add to Stack must be installed. You can save offline before choosing a stack. You can generate audio later.</p>
      </section>
    </div>
    <details className="sharing-help"><summary>Connection help and privacy</summary>
      <p>Pairing survives Mac restarts. After revoking or replacing a link, paste the new connection in Saved shares on your iPhone. Pending items stay on the phone.</p>
      <p>If your phone cannot connect, check that the Mac is awake, both devices are on the same Wi-Fi, and local-network access is allowed for Reading Stacks. A guest network or VPN may prevent a connection.</p>
      <p>The connection link allows stack listing and sending article URLs and PDFs. Keep it private. This local connection is unencrypted, so use trusted Wi-Fi. Disconnect sharing above to revoke the link.</p>
    </details>
  </section>;
}
