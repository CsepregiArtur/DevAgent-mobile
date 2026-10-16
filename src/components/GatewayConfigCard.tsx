import React, { useState } from 'react';
import {
  clearGatewayConfig,
  DEFAULT_GATEWAY_URL,
  fetchAgentStatus,
  getGatewayBase,
  getGatewayToken,
  saveGatewayConfig,
} from '../services/api';
import { Check, Eye, EyeOff, KeyRound, Link2, Loader2, Save, Server, Trash2 } from 'lucide-react';

/**
 * Gateway connection card.
 *
 * The PWA is normally served by the gateway itself, so the default base URL is
 * derived from the build (``/mobile``). Inside the native iOS shell (Capacitor)
 * the app runs from ``capacitor://localhost`` and therefore needs an absolute
 * gateway URL plus the shared token, stored on the device.
 */
export const GatewayConfigCard: React.FC<{ onSaved: () => void }> = ({ onSaved }) => {
  const [baseUrl, setBaseUrl] = useState(getGatewayBase() || DEFAULT_GATEWAY_URL);
  const [token, setToken] = useState(getGatewayToken());
  const [showToken, setShowToken] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    setSaving(true);
    setMessage(null);
    setError(null);
    saveGatewayConfig(baseUrl, token);
    try {
      const status = await fetchAgentStatus();
      setMessage(
        `Connected - agent: ${status.status}${status.isSimulated ? ' (simulator)' : ''}`
      );
      onSaved();
    } catch (err: any) {
      setError(err?.message || 'Could not reach the gateway with these settings');
    } finally {
      setSaving(false);
    }
  };

  const handleClear = () => {
    clearGatewayConfig();
    setBaseUrl(DEFAULT_GATEWAY_URL);
    setToken('');
    setMessage('Saved settings cleared; using the built-in defaults.');
    setError(null);
    onSaved();
  };

  return (
    <div className="p-4 rounded-2xl bg-zinc-900 border border-zinc-800 space-y-3 shadow-sm">
      <div className="flex items-center gap-2">
        <div className="p-2 rounded-lg bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
          <Server className="w-4 h-4" />
        </div>
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-white">
            Gateway connection
          </h3>
          <p className="text-[11px] text-zinc-400">
            Required for the iOS app (native shell) and for custom URLs
          </p>
        </div>
      </div>

      <label className="block space-y-1">
        <span className="text-[10px] uppercase tracking-wider text-zinc-500 font-mono flex items-center gap-1">
          <Link2 className="w-3 h-3" /> Gateway base URL
        </span>
        <input
          type="url"
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          placeholder={DEFAULT_GATEWAY_URL}
          className="w-full px-3 py-2 rounded-xl bg-zinc-950 border border-zinc-800 text-xs text-zinc-100 font-mono focus:outline-none focus:border-indigo-500"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
        />
      </label>

      <label className="block space-y-1">
        <span className="text-[10px] uppercase tracking-wider text-zinc-500 font-mono flex items-center gap-1">
          <KeyRound className="w-3 h-3" /> Shared token (DEVAGENT_TOKEN)
        </span>
        <div className="flex items-center gap-2">
          <input
            type={showToken ? 'text' : 'password'}
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="••••••••••••••••"
            className="flex-1 px-3 py-2 rounded-xl bg-zinc-950 border border-zinc-800 text-xs text-zinc-100 font-mono focus:outline-none focus:border-indigo-500"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
          />
          <button
            type="button"
            onClick={() => setShowToken((value) => !value)}
            className="p-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition"
            aria-label={showToken ? 'Hide token' : 'Show token'}
          >
            {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>
      </label>

      <div className="flex items-center gap-2">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex-1 px-3 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-xs font-semibold text-white inline-flex items-center justify-center gap-1.5 transition"
        >
          {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
          <span>{saving ? 'Testing connection...' : 'Save & test'}</span>
        </button>
        <button
          onClick={handleClear}
          className="px-3 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs font-medium text-zinc-300 inline-flex items-center gap-1.5 transition"
        >
          <Trash2 className="w-3.5 h-3.5" />
          <span>Reset</span>
        </button>
      </div>

      {message && (
        <p className="text-[11px] text-emerald-400 flex items-center gap-1.5 font-mono">
          <Check className="w-3.5 h-3.5" /> {message}
        </p>
      )}
      {error && (
        <p className="text-[11px] text-rose-400 font-mono break-words">{error}</p>
      )}
    </div>
  );
};
