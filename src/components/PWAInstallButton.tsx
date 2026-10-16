import React, { useState } from 'react';
import { usePWAInstall } from '../hooks/usePWAInstall';
import { Download, Share2, X, PlusSquare } from 'lucide-react';

interface PWAInstallButtonProps {
  variant?: 'header' | 'card' | 'settings';
}

export const PWAInstallButton: React.FC<PWAInstallButtonProps> = ({ variant = 'header' }) => {
  const { isInstallable, isInstalled, isIOS, install } = usePWAInstall();
  const [showIOSGuide, setShowIOSGuide] = useState(false);

  if (isInstalled) {
    if (variant === 'settings') {
      return (
        <div className="flex items-center justify-between p-3 rounded-xl bg-zinc-900 border border-zinc-800 text-xs">
          <span className="text-zinc-400">PWA Application Status</span>
          <span className="text-emerald-400 font-medium">Installed & Running Standalone</span>
        </div>
      );
    }
    return null;
  }

  // Desktop or Android / Chrome flow with beforeinstallprompt
  if (isInstallable) {
    return (
      <>
        {variant === 'card' ? (
          <div className="p-4 rounded-xl bg-gradient-to-r from-indigo-950/60 to-purple-950/60 border border-indigo-800/40 flex items-center justify-between gap-3">
            <div>
              <h4 className="text-sm font-semibold text-white">Install Mobile App</h4>
              <p className="text-xs text-zinc-400 mt-0.5">Control your coding agent directly from home screen</p>
            </div>
            <button
              onClick={install}
              className="px-3.5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-medium text-xs flex items-center gap-1.5 shadow-sm transition active:scale-95 shrink-0"
            >
              <Download className="w-4 h-4" />
              <span>Install</span>
            </button>
          </div>
        ) : (
          <button
            onClick={install}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-indigo-600/90 hover:bg-indigo-600 text-white text-xs font-medium transition active:scale-95"
            title="Install DevAgent as PWA"
          >
            <Download className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Install App</span>
          </button>
        )}
      </>
    );
  }

  // iOS Safari flow
  if (isIOS) {
    return (
      <>
        {variant === 'card' ? (
          <div className="p-4 rounded-xl bg-zinc-900/90 border border-zinc-800 flex items-center justify-between gap-3">
            <div>
              <h4 className="text-sm font-semibold text-white">Add to iPhone Home Screen</h4>
              <p className="text-xs text-zinc-400 mt-0.5">Run in full screen without Safari browser chrome</p>
            </div>
            <button
              onClick={() => setShowIOSGuide(true)}
              className="px-3 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700 font-medium text-xs flex items-center gap-1.5 transition shrink-0"
            >
              <Share2 className="w-3.5 h-3.5" />
              <span>How to Install</span>
            </button>
          </div>
        ) : (
          <button
            onClick={() => setShowIOSGuide(true)}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-800 text-xs font-medium transition"
          >
            <Share2 className="w-3.5 h-3.5" />
            <span>Install</span>
          </button>
        )}

        {showIOSGuide && (
          <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/75 backdrop-blur-xs p-4 animate-in fade-in">
            <div className="w-full max-w-sm rounded-2xl bg-zinc-900 border border-zinc-800 p-5 shadow-2xl text-left space-y-4">
              <div className="flex items-center justify-between pb-3 border-b border-zinc-800">
                <div className="flex items-center gap-2">
                  <div className="p-2 rounded-lg bg-indigo-500/10 text-indigo-400">
                    <Download className="w-5 h-5" />
                  </div>
                  <div>
                    <h3 className="text-sm font-semibold text-white">Install on iPhone Safari</h3>
                    <p className="text-xs text-zinc-400">DevAgent Mobile Web App</p>
                  </div>
                </div>
                <button
                  onClick={() => setShowIOSGuide(false)}
                  className="p-1 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              <div className="space-y-3 text-xs text-zinc-300">
                <div className="flex items-start gap-3 p-2.5 rounded-lg bg-zinc-800/50">
                  <span className="flex items-center justify-center w-5 h-5 rounded-full bg-indigo-600 text-[11px] font-bold text-white shrink-0">1</span>
                  <div>
                    Tap the <strong className="text-white">Share</strong> button in Safari's bottom toolbar (<Share2 className="inline w-3.5 h-3.5 text-sky-400" />).
                  </div>
                </div>

                <div className="flex items-start gap-3 p-2.5 rounded-lg bg-zinc-800/50">
                  <span className="flex items-center justify-center w-5 h-5 rounded-full bg-indigo-600 text-[11px] font-bold text-white shrink-0">2</span>
                  <div>
                    Scroll down and tap <strong className="text-white">Add to Home Screen</strong> (<PlusSquare className="inline w-3.5 h-3.5 text-zinc-300" />).
                  </div>
                </div>

                <div className="flex items-start gap-3 p-2.5 rounded-lg bg-zinc-800/50">
                  <span className="flex items-center justify-center w-5 h-5 rounded-full bg-indigo-600 text-[11px] font-bold text-white shrink-0">3</span>
                  <div>
                    Tap <strong className="text-white">Add</strong> in the top-right corner to finish.
                  </div>
                </div>
              </div>

              <button
                onClick={() => setShowIOSGuide(false)}
                className="w-full py-2.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-white text-xs font-semibold transition"
              >
                Got It
              </button>
            </div>
          </div>
        )}
      </>
    );
  }

  return null;
};
