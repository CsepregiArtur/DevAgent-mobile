import React, { useEffect, useState } from 'react';
import {
  ApiError,
  createToken,
  deleteToken,
  fetchTokens,
  revokeToken,
} from '../services/api';
import type { ApiTokenMeta } from '../services/api';
import {
  AlertTriangle,
  BadgeCheck,
  Check,
  Copy,
  KeyRound,
  Loader2,
  Lock,
  Plus,
  RefreshCw,
  ShieldAlert,
  Trash2,
  XCircle,
} from 'lucide-react';

interface ApiKeysCardProps {
  canManage: boolean;
  onKeysChanged?: () => void;
}

const kindStyles: Record<string, string> = {
  admin: 'bg-rose-500/10 text-rose-300 border-rose-500/20',
  pwa: 'bg-indigo-500/10 text-indigo-300 border-indigo-500/20',
  agent: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20',
  session: 'bg-amber-500/10 text-amber-300 border-amber-500/20',
};

function relative(iso?: string | null): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

/**
 * API key management, like a provider dashboard: create named keys (pwa / agent /
 * admin), see them once, revoke or delete them. The plaintext never leaves the
 * gateway again after creation.
 */
export const ApiKeysCard: React.FC<ApiKeysCardProps> = ({ canManage, onKeysChanged }) => {
  const [tokens, setTokens] = useState<ApiTokenMeta[]>([]);
  const [kinds, setKinds] = useState<{ kind: string; scopes: string[] }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [kind, setKind] = useState('pwa');
  const [expiresInDays, setExpiresInDays] = useState('');
  const [creating, setCreating] = useState(false);
  const [newToken, setNewToken] = useState<{ token: string; name: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = async () => {
    if (!canManage) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchTokens();
      setTokens(data.tokens);
      setKinds(data.kinds);
    } catch (err: any) {
      if (err instanceof ApiError && err.status === 403) {
        setError('This session cannot manage API keys.');
      } else {
        setError(err?.message || 'Could not load API keys.');
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canManage]);

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    if (creating || !name.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const result = await createToken({
        name: name.trim(),
        kind,
        ...(expiresInDays ? { expiresInDays: Number(expiresInDays) } : {}),
      });
      setNewToken({ token: result.token, name: result.meta.name });
      setName('');
      setExpiresInDays('');
      await load();
      onKeysChanged?.();
    } catch (err: any) {
      setError(err?.message || 'Could not create the key.');
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (id: string) => {
    setBusyId(id);
    setError(null);
    try {
      await revokeToken(id);
      await load();
      onKeysChanged?.();
    } catch (err: any) {
      setError(err?.message || 'Could not revoke the key.');
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (id: string) => {
    setBusyId(id);
    setError(null);
    try {
      await deleteToken(id);
      await load();
      onKeysChanged?.();
    } catch (err: any) {
      setError(err?.message || 'Could not delete the key.');
    } finally {
      setBusyId(null);
    }
  };

  const copyToken = async () => {
    if (!newToken) return;
    try {
      await navigator.clipboard.writeText(newToken.token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError('Could not copy; select the token manually.');
    }
  };

  return (
    <div className="p-4 rounded-2xl bg-zinc-900 border border-zinc-800 shadow-sm space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <div className="p-2 rounded-lg bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
            <KeyRound className="w-4 h-4" />
          </div>
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-white">API keys</h3>
            <p className="text-[11px] text-zinc-400">
              {canManage
                ? 'Create one key per device; revoke it any time.'
                : 'Sign in with an admin key to manage keys.'}
            </p>
          </div>
        </div>
        {canManage && (
          <button
            onClick={load}
            className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition"
            title="Refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        )}
      </div>

      {canManage && (
        <form onSubmit={handleCreate} className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="iPhone-Artur"
              className="px-3 py-2 rounded-xl bg-zinc-950 border border-zinc-800 text-xs text-zinc-100 focus:outline-none focus:border-indigo-500"
            />
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value)}
              className="px-3 py-2 rounded-xl bg-zinc-950 border border-zinc-800 text-xs font-mono text-zinc-200 focus:outline-none focus:border-indigo-500"
            >
              {(kinds.length
                ? kinds
                : [
                    { kind: 'pwa', scopes: ['pwa'] },
                    { kind: 'agent', scopes: ['agent'] },
                    { kind: 'admin', scopes: ['admin'] },
                  ]
              ).map((entry) => (
                <option key={entry.kind} value={entry.kind}>
                  {entry.kind} · {entry.scopes.join(', ')}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <input
              value={expiresInDays}
              onChange={(event) => setExpiresInDays(event.target.value.replace(/[^0-9]/g, ''))}
              placeholder="Expiră în zile (opțional)"
              className="flex-1 px-3 py-2 rounded-xl bg-zinc-950 border border-zinc-800 text-xs text-zinc-100 focus:outline-none focus:border-indigo-500"
            />
            <button
              type="submit"
              disabled={creating || !name.trim()}
              className="px-3.5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-xs font-semibold text-white inline-flex items-center gap-1.5 transition"
            >
              {creating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
              <span>Generate</span>
            </button>
          </div>
        </form>
      )}

      {newToken && (
        <div className="p-3 rounded-xl bg-emerald-950/40 border border-emerald-500/30 space-y-2">
          <p className="text-[11px] text-emerald-200 flex items-center gap-1.5">
            <BadgeCheck className="w-3.5 h-3.5" />
            Key „{newToken.name}" creat. Copiaz-o acum — nu mai e afișată.
          </p>
          <div className="flex items-start gap-2">
            <code className="flex-1 break-all text-[10px] font-mono text-emerald-100 bg-black/40 rounded-lg p-2">
              {newToken.token}
            </code>
            <button
              onClick={copyToken}
              className="p-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 transition shrink-0"
              aria-label="Copy key"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
            <button
              onClick={() => setNewToken(null)}
              className="p-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 transition shrink-0"
              aria-label="Dismiss"
            >
              <XCircle className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="text-[11px] text-rose-400 font-mono flex items-start gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" /> {error}
        </p>
      )}

      {canManage && (
        <div className="space-y-2">
          {tokens.length === 0 && !loading && (
            <p className="text-[11px] text-zinc-500">No keys yet.</p>
          )}
          {tokens.map((token) => (
            <div
              key={token.id}
              className="p-2.5 rounded-xl bg-zinc-950/60 border border-zinc-800/60 space-y-1"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-xs text-zinc-100 truncate">{token.name}</span>
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${
                      kindStyles[token.kind] || kindStyles.pwa
                    }`}
                  >
                    {token.kind}
                  </span>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <span
                    className={`text-[10px] font-mono ${
                      token.status === 'active'
                        ? 'text-emerald-400'
                        : token.status === 'expired'
                        ? 'text-amber-400'
                        : 'text-rose-400'
                    }`}
                  >
                    {token.status}
                  </span>
                  {token.bootstrap ? (
                    <span title="Defined in the server environment" className="p-1 text-zinc-500">
                      <Lock className="w-3.5 h-3.5" />
                    </span>
                  ) : (
                    <>
                      {token.status === 'active' && (
                        <button
                          onClick={() => handleRevoke(token.id)}
                          disabled={busyId === token.id}
                          title="Revoke"
                          className="p-1 rounded-lg text-zinc-500 hover:text-amber-400 hover:bg-zinc-800 transition disabled:opacity-50"
                        >
                          <ShieldAlert className="w-3.5 h-3.5" />
                        </button>
                      )}
                      <button
                        onClick={() => handleDelete(token.id)}
                        disabled={busyId === token.id}
                        title="Delete"
                        className="p-1 rounded-lg text-zinc-500 hover:text-rose-400 hover:bg-zinc-800 transition disabled:opacity-50"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] font-mono text-zinc-500">
                <span className="text-zinc-400">{token.prefix}…</span>
                <span>created {relative(token.createdAt)}</span>
                <span>used {relative(token.lastUsedAt)}</span>
                {token.expiresAt && <span>expires {relative(token.expiresAt)}</span>}
                {token.bootstrap && <span className="text-zinc-600">server env</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
