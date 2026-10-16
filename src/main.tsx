import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import { saveGatewayConfig } from './services/api';
import './index.css';

// One-time bootstrap for phones/macOS shells:
//   https://<gateway>/mobile/?baseUrl=https://<gateway>/mobile&token=<DEVAGENT_TOKEN>
// The values are persisted on the device and then removed from the address bar.
if (typeof window !== 'undefined') {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token');
  const baseUrl = params.get('baseUrl');
  if (token || baseUrl) {
    saveGatewayConfig(baseUrl || '', token || '');
    params.delete('token');
    params.delete('baseUrl');
    const query = params.toString();
    window.history.replaceState(
      {},
      '',
      `${window.location.pathname}${query ? `?${query}` : ''}`
    );
  }
}

// In development or iframe preview, unregister any stale dev service worker
// to avoid intercepting Google Auth Bridge or proxy requests.
if (typeof window !== 'undefined' && 'serviceWorker' in navigator) {
  navigator.serviceWorker.getRegistrations().then((registrations) => {
    for (const reg of registrations) {
      if (reg.active?.scriptURL.includes('dev-sw.js')) {
        reg.unregister().catch(() => {});
      }
    }
  }).catch(() => {});
}

createRoot(document.getElementById('root')!).render(<App />);
