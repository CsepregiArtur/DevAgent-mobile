import React from 'react';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { WifiOff } from 'lucide-react';

export const OfflineBanner: React.FC = () => {
  const isOnline = useOnlineStatus();

  if (isOnline) return null;

  return (
    <div className="sticky top-0 z-50 flex items-center justify-center gap-2 bg-rose-600/95 text-white px-4 py-2 text-xs font-medium shadow-md backdrop-blur-xs">
      <WifiOff className="w-4 h-4 shrink-0" />
      <span>Offline Mode — State-changing requests paused. Cached shell in use.</span>
    </div>
  );
};
