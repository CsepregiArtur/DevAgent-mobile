import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import './index.css';

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
