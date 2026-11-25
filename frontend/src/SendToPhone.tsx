import { useEffect, useState } from 'react';
import QRCode from 'qrcode';

type Transfer = { token: string; url: string; expiresAt: number };
export function SendToPhone({ jobId }: { jobId: string }) {
  const [transfer, setTransfer] = useState<Transfer | null>(null);
  const [qr, setQr] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [received, setReceived] = useState(false), [expired, setExpired] = useState(false), [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!transfer) return;
    let alive = true;
    async function refresh() {
      if (!transfer) return;
      if (Date.now() >= transfer.expiresAt * 1000) { if (alive) setExpired(true); return; }
      try {
        const response = await fetch(`/api/transfers/${transfer.token}`);
        if (response.status === 404) { if (alive) setExpired(true); return; }
        if (!response.ok) return;
        const status = await response.json();
        if (alive) setReceived(!!status.downloadedAt);
      } catch { /* Keep the code visible during transient editor disconnects. */ }
    }
    void refresh();
    const interval = setInterval(refresh, 2000);
    return () => { alive = false; clearInterval(interval); };
  }, [transfer]);
  async function send() {
    setBusy(true); setError('');
    try {
      if (transfer) await fetch(`/api/transfers/${transfer.token}`, { method: 'DELETE' });
      const response = await fetch(`/api/jobs/${jobId}/send`, { method: 'POST' });
      const result = await response.json();
      if (!response.ok) throw Error(result.detail || 'Unable to start phone transfer');
      const image = await QRCode.toDataURL(result.url, { width: 240, margin: 2 });
      setTransfer(result); setQr(image); setReceived(false); setExpired(false); setCopied(false);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="phone-transfer">
    <button className="send-to-phone-button" disabled={busy} onClick={() => void send()}><svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 5h4M11 18h2"/></svg>{busy ? 'Preparing…' : transfer ? 'New Transfer Code' : 'Send To Phone'}</button>
    {error && <p role="alert" className="error">{error}</p>}
    {transfer && <div className="phone-transfer-panel">
      <strong>{received ? 'Downloaded On Phone' : expired ? 'Transfer Expired' : 'Ready To Download'}</strong>
      {!expired && <><img src={qr} width={200} height={200} alt="Scan in the Reading Stacks mobile app to download this recording" />
        <p>Open Reading Stacks on your phone and tap Scan QR Code. Keep your Mac awake and both devices on the same Wi-Fi.</p>
        <input aria-label="Phone transfer link" value={transfer.url} readOnly onFocus={e => e.currentTarget.select()} />
        <button onClick={() => { void navigator.clipboard.writeText(transfer.url).then(() => setCopied(true)).catch(() => setError('Select and copy the transfer link above.')); }}>{copied ? 'Copied' : 'Copy Link'}</button>
        <p className="hint">Expires in one hour. This link grants access to this recording and its text over your local network.</p></>}
      <button onClick={() => { void fetch(`/api/transfers/${transfer.token}`, { method: 'DELETE' }).then(response => {
        if (!response.ok) throw Error('Unable to revoke transfer'); setTransfer(null);
      }).catch(e => setError(e.message)); }}>Revoke Transfer</button>
    </div>}
  </div>;
}
