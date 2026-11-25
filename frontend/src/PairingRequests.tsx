import React, {useEffect, useState} from 'react';

type Request = {id: string; name: string; code: string};
export function PairingRequests() {
  const [requests, setRequests] = useState<Request[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const response = await fetch('/api/share/requests', {cache: 'no-store', signal: AbortSignal.timeout(4000)});
        if (response.ok) {const data = await response.json(); if (alive) setRequests(data);}
      } catch { /* The regular app already reports backend connection errors. */ }
    };
    void refresh(); const timer = setInterval(refresh, 3000);
    return () => {alive = false; clearInterval(timer);};
  }, []);
  async function decide(id: string, action: 'approve' | 'reject') {
    setBusy(true); setError('');
    try {
      const response = await fetch(`/api/share/requests/${id}/${action}`, {method: 'POST', signal: AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error('This request may have expired. Retry the connection on your phone.');
      setRequests(current => current.filter(item => item.id !== id));
    } catch (e) {setError((e as Error).message);}
    finally {setBusy(false);}
  }
  if (!requests.length && !error) return null;
  return <section className="card" aria-label="iPhone connection requests" aria-live="polite">
    {requests.map(request => <div key={request.id}>
      <h2>Connect your iPhone</h2>
      <p>{request.name} found this Mac. Check that your phone shows <strong>{request.code}</strong>, then approve. Future connections are automatic.</p>
      <button className="primary" disabled={busy} onClick={() => decide(request.id, 'approve')}>Approve connection</button>{' '}
      <button disabled={busy} onClick={() => decide(request.id, 'reject')}>Decline</button>
    </div>)}
    {error && <p role="alert" className="error">{error}</p>}
  </section>;
}
