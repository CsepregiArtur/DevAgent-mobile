import React, { useEffect, useState } from 'react';
import {
  ApiError,
  fetchAuthConfig,
  getGatewayBase,
  getGatewayToken,
  login,
  saveGatewayConfig,
  setSessionToken,
  DEFAULT_GATEWAY_URL,
} from '../../services/api';
import {
  AlertCircle,
  ChevronDown,
  ChevronUp,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Lock,
  LogIn,
  ShieldCheck,
  Terminal,
} from 'lucide-react';

interface LoginScreenProps {
  onAuthenticated: () => void;
  /** shown when a previous session used the wrong kind of token */
  notice?: string | null;
}

/**
 * First screen of the app: authenticate with an API key (like a provider token)
 * or with the admin password (if configured on the gateway). The gateway URL is
 * editable for the native iOS shell and for self-hosted installations.
 */
export const LoginScreen: React.FC<LoginScreenProps> = ({ onAuthenticated, notice }) => {
  const [mode, setMode] = useState<'token' | 'password'>('token');
  const [token, setToken] = useState(getGatewayToken());
  const [password, setPassword] = useState('');
  const [baseUrl, setBaseUrl] = useState(getGatewayBase() || DEFAULT_GATEWAY_URL);
  const [showSecret, setShowSecret] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [passwordLogin, setPasswordLogin] = useState(false);
  const [gatewayVersion, setGatewayVersion] = useState<string | null>(null);
  const [reachable, setReachable] = useState<boolean | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Saving the base URL first also tells us whether the gateway is reachable.
    saveGatewayConfig(baseUrl, '');
    fetchAuthConfig()
      .then((config) => {
        setPasswordLogin(config.passwordLogin);
        setGatewayVersion(config.gateway?.version || null);
        setReachable(true);
        if (!config.passwordLogin) setMode('token');
      })
      .catch(() => setReachable(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (isSubmitting) return;
    setError(null);

    const secret = mode === 'token' ? token.trim() : password;
    if (!secret) {
      setError(mode === 'token' ? 'Paste your API token.' : 'Enter the admin password.');
      return;
    }

    setIsSubmitting(true);
    try {
      saveGatewayConfig(baseUrl, '');
      const result = await login(
        mode === 'token' ? { token: secret } : { password: secret }
      );
      setSessionToken(result.token);
      onAuthenticated();
    } catch (err: any) {
      if (err instanceof ApiError) {
        setError(
          err.status === 401
            ? 'Invalid credentials. Check the token (or password) and the gateway URL.'
            : err.message
        );
        if (err.status === 0 || err.status === 404) setReachable(false);
      } else {
        setError(err?.message || 'Could not reach the gateway.');
        setReachable(false);
      }
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 flex flex-col selection:bg-indigo-500 selection:text-white">
      <header className="px-5 pt-10 pb-6">
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-11 h-11 rounded-2xl bg-indigo-600/20 text-indigo-400 border border-indigo-500/30">
            <Terminal className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-white leading-tight">DevAgent</h1>
            <p className="text-[11px] text-zinc-400 font-mono">
              platform gateway{gatewayVersion ? ` · v${gatewayVersion}` : ''}
            </p>
          </div>
        </div>
        <p className="mt-5 text-sm text-zinc-400 leading-relaxed max-w-sm">
          Sign in to dispatch tasks, follow their execution live and approve changes before
          anything is applied.
        </p>
      </header>

      <main className="flex-1 w-full max-w-md mx-auto px-5 pb-10">
        {notice && (
          <div className="mb-3 p-3 rounded-xl bg-amber-950/40 border border-amber-500/30 text-[11px] text-amber-200 flex items-start gap-2 leading-relaxed">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-amber-400" />
            <span>{notice}</span>
          </div>
        )}
        <form
          onSubmit={handleSubmit}
          className="p-5 rounded-2xl bg-zinc-900 border border-zinc-800 shadow-sm space-y-4"
        >
          <div className="grid grid-cols-2 gap-1.5 p-1 rounded-xl bg-zinc-950 border border-zinc-800">
            <button
              type="button"
              onClick={() => setMode('token')}
              className={`py-2 rounded-lg text-xs font-medium transition flex items-center justify-center gap-1.5 ${
                mode === 'token'
                  ? 'bg-zinc-800 text-white shadow-sm'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <KeyRound className="w-3.5 h-3.5" />
              API token
            </button>
            <button
              type="button"
              disabled={!passwordLogin}
              onClick={() => setMode('password')}
              className={`py-2 rounded-lg text-xs font-medium transition flex items-center justify-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed ${
                mode === 'password'
                  ? 'bg-zinc-800 text-white shadow-sm'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
              title={passwordLogin ? 'Sign in with the admin password' : 'Disabled on this gateway'}
            >
              <Lock className="w-3.5 h-3.5" />
              Password
            </button>
          </div>

          {mode === 'token' ? (
            <label className="block space-y-1.5">
              <span className="text-[10px] uppercase tracking-wider text-zinc-500 font-mono">
                API token
              </span>
              <div className="flex items-center gap-2">
                <input
                  type={showSecret ? 'text' : 'password'}
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  placeholder="dvk_…"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  autoComplete="off"
                  className="flex-1 px-3 py-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-xs font-mono text-zinc-100 focus:outline-none focus:border-indigo-500"
                />
                <button
                  type="button"
                  onClick={() => setShowSecret((value) => !value)}
                  className="p-2.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition"
                  aria-label={showSecret ? 'Hide token' : 'Show token'}
                >
                  {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              <p className="text-[10px] text-zinc-500 leading-relaxed">
                Generate one on the server:{' '}
                <span className="font-mono text-zinc-400">
                  python -m app.cli token --name iPhone --kind pwa
                </span>
                , apoi îl poți administra din <strong>Settings → API keys</strong>.
              </p>
            </label>
          ) : (
            <label className="block space-y-1.5">
              <span className="text-[10px] uppercase tracking-wider text-zinc-500 font-mono">
                Admin password
              </span>
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="••••••••••"
                autoComplete="current-password"
                className="w-full px-3 py-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-xs font-mono text-zinc-100 focus:outline-none focus:border-indigo-500"
              />
              <p className="text-[10px] text-zinc-500">
                Issues a session token valid for the configured period (default 12h).
              </p>
            </label>
          )}

          {/* Advanced: gateway URL */}
          <div className="pt-1">
            <button
              type="button"
              onClick={() => setShowAdvanced((value) => !value)}
              className="text-[11px] text-zinc-400 hover:text-zinc-200 flex items-center gap-1 transition"
            >
              {showAdvanced ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
              Gateway URL
              <span className="font-mono text-zinc-500 truncate max-w-[220px]">{baseUrl}</span>
            </button>
            {showAdvanced && (
              <div className="mt-2 space-y-1.5">
                <input
                  type="url"
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder={DEFAULT_GATEWAY_URL}
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  className="w-full px-3 py-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-xs font-mono text-zinc-100 focus:outline-none focus:border-indigo-500"
                />
                <p className="text-[10px] text-zinc-500">
                  {reachable === false
                    ? 'Gateway unreachable with this URL.'
                    : reachable === true
                    ? 'Gateway reachable.'
                    : 'Checking…'}
                </p>
              </div>
            )}
          </div>

          {error && (
            <div className="p-3 rounded-xl bg-rose-950/50 border border-rose-500/40 text-rose-300 text-[11px] flex items-start gap-2">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-rose-400" />
              <span className="leading-relaxed">{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 text-sm font-semibold text-white inline-flex items-center justify-center gap-2 transition active:scale-[0.99]"
          >
            {isSubmitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <LogIn className="w-4 h-4" />}
            <span>{isSubmitting ? 'Signing in…' : 'Sign in'}</span>
          </button>

          <p className="text-[10px] text-zinc-500 flex items-start gap-1.5 leading-relaxed">
            <ShieldCheck className="w-3.5 h-3.5 shrink-0 text-emerald-500 mt-0.5" />
            Credentials stay on this device; the gateway only stores a hash of each token.
          </p>
        </form>
      </main>
    </div>
  );
};
