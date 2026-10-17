import React, { useEffect, useState } from 'react';
import {
  ApiError,
  createProvider,
  deleteProvider,
  fetchProviders,
  setDefaultProvider,
  updateProvider,
} from '../services/api';
import type { ProviderInfo } from '../types/agent';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  Cpu,
  Loader2,
  Pencil,
  Plus,
  Server,
  Star,
  StarOff,
  Trash2,
} from 'lucide-react';

interface ProvidersCardProps {
  canManage: boolean;
  onChanged?: () => void;
}

const emptyForm = {
  name: '',
  kind: 'openai',
  baseUrl: '',
  apiKey: '',
  models: '',
  defaultModel: '',
  enabled: true,
};

type FormState = typeof emptyForm;

/**
 * AI providers used by the agents. Providers are attached to tasks, so adding,
 * editing or switching one never affects the agent <-> gateway connection.
 */
export const ProvidersCard: React.FC<ProvidersCardProps> = ({ canManage, onChanged }) => {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchProviders();
      setProviders(data.providers);
    } catch (err: any) {
      setError(err?.message || 'Could not load providers.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const startCreate = () => {
    setEditingId(null);
    setForm(emptyForm);
    setShowForm(true);
  };

  const startEdit = (provider: ProviderInfo) => {
    setEditingId(provider.id);
    setForm({
      name: provider.name,
      kind: provider.kind,
      baseUrl: provider.baseUrl,
      apiKey: '',
      models: (provider.models || []).join(', '),
      defaultModel: provider.defaultModel || '',
      enabled: provider.enabled,
    });
    setShowForm(true);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setError(null);
    const payload = {
      name: form.name.trim(),
      kind: form.kind,
      baseUrl: form.baseUrl.trim(),
      models: form.models
        .split(',')
        .map((model) => model.trim())
        .filter(Boolean),
      ...(form.defaultModel.trim() ? { defaultModel: form.defaultModel.trim() } : {}),
      enabled: form.enabled,
      ...(form.apiKey.trim() ? { apiKey: form.apiKey.trim() } : {}),
    };
    try {
      if (editingId) {
        await updateProvider(editingId, payload);
      } else {
        await createProvider(payload);
      }
      setShowForm(false);
      setEditingId(null);
      setForm(emptyForm);
      await load();
      onChanged?.();
    } catch (err: any) {
      setError(err?.message || 'Could not save the provider.');
    } finally {
      setSaving(false);
    }
  };

  const act = async (id: string, action: 'default' | 'toggle' | 'delete') => {
    setBusyId(id);
    setError(null);
    try {
      if (action === 'default') await setDefaultProvider(id);
      if (action === 'delete') await deleteProvider(id);
      if (action === 'toggle') {
        const provider = providers.find((row) => row.id === id);
        if (provider) await updateProvider(id, { enabled: !provider.enabled });
      }
      await load();
      onChanged?.();
    } catch (err: any) {
      if (err instanceof ApiError && err.status === 403) {
        setError('This session cannot manage providers.');
      } else {
        setError(err?.message || 'Action failed.');
      }
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="p-4 rounded-2xl bg-zinc-900 border border-zinc-800 shadow-sm space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <div className="p-2 rounded-lg bg-purple-500/10 text-purple-400 border border-purple-500/20">
            <Cpu className="w-4 h-4" />
          </div>
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-white">AI providers</h3>
            <p className="text-[11px] text-zinc-400">
              Used per task — changing them never touches the agent connection
            </p>
          </div>
        </div>
        {canManage && (
          <button
            onClick={startCreate}
            className="px-2.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-[11px] font-medium text-zinc-200 inline-flex items-center gap-1 transition shrink-0"
          >
            <Plus className="w-3.5 h-3.5" /> Add
          </button>
        )}
      </div>

      {providers.length === 0 && !loading && (
        <p className="text-[11px] text-zinc-500">
          No provider configured. The agent falls back to its own env file
          (<span className="font-mono">DEVAGENT_LLM_BASE_URL</span>).
        </p>
      )}

      <div className="space-y-2">
        {providers.map((provider) => (
          <div
            key={provider.id}
            className={`p-2.5 rounded-xl bg-zinc-950/60 border ${
              provider.enabled ? 'border-zinc-800/60' : 'border-zinc-800/30 opacity-60'
            } space-y-1`}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <Server className="w-3.5 h-3.5 text-zinc-500 shrink-0" />
                <span className="text-xs text-zinc-100 truncate">{provider.name}</span>
                <span className="text-[10px] font-mono text-zinc-500">{provider.kind}</span>
                {provider.isDefault && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded border border-emerald-500/30 bg-emerald-500/10 text-emerald-300 font-mono">
                    default
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {busyId === provider.id ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-zinc-400" />
                ) : (
                  <>
                    {canManage && !provider.isDefault && provider.enabled && (
                      <button
                        onClick={() => act(provider.id, 'default')}
                        title="Set as default"
                        className="p-1 rounded-lg text-zinc-500 hover:text-emerald-400 hover:bg-zinc-800 transition"
                      >
                        <Star className="w-3.5 h-3.5" />
                      </button>
                    )}
                    {canManage && provider.isDefault && (
                      <span className="p-1 text-emerald-400" title="Default provider">
                        <StarOff className="w-3.5 h-3.5" />
                      </span>
                    )}
                    {canManage && (
                      <>
                        <button
                          onClick={() => startEdit(provider)}
                          title="Edit"
                          className="p-1 rounded-lg text-zinc-500 hover:text-indigo-300 hover:bg-zinc-800 transition"
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => act(provider.id, 'toggle')}
                          title={provider.enabled ? 'Disable' : 'Enable'}
                          className="px-1.5 py-0.5 rounded-lg text-[10px] font-mono text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition"
                        >
                          {provider.enabled ? 'on' : 'off'}
                        </button>
                        <button
                          onClick={() => act(provider.id, 'delete')}
                          title="Delete"
                          className="p-1 rounded-lg text-zinc-500 hover:text-rose-400 hover:bg-zinc-800 transition"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </>
                    )}
                  </>
                )}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] font-mono text-zinc-500">
              <span className="truncate max-w-[240px] text-zinc-400">{provider.baseUrl}</span>
              {provider.defaultModel && <span>model: {provider.defaultModel}</span>}
              {provider.models.length > 0 && <span>{provider.models.length} models</span>}
              <span className={provider.hasApiKey ? 'text-emerald-500/80' : 'text-amber-500/80'}>
                {provider.hasApiKey ? `key ${provider.apiKeyHint}` : 'no key (agent env)'}
              </span>
            </div>
          </div>
        ))}
      </div>

      {error && (
        <p className="text-[11px] text-rose-400 font-mono flex items-start gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" /> {error}
        </p>
      )}

      {showForm && canManage && (
        <form onSubmit={submit} className="space-y-2 pt-2 border-t border-zinc-800/80">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold text-zinc-200">
              {editingId ? 'Edit provider' : 'New provider'}
            </span>
            <button
              type="button"
              onClick={() => {
                setShowForm(false);
                setEditingId(null);
              }}
              className="text-[10px] text-zinc-500 hover:text-zinc-300 inline-flex items-center gap-1"
            >
              <ChevronUp className="w-3 h-3" /> close
            </button>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="Name (e.g. DeepSeek proxy)"
              required
              className="px-3 py-2 rounded-xl bg-zinc-950 border border-zinc-800 text-xs text-zinc-100 focus:outline-none focus:border-indigo-500"
            />
            <select
              value={form.kind}
              onChange={(event) => setForm({ ...form, kind: event.target.value })}
              className="px-3 py-2 rounded-xl bg-zinc-950 border border-zinc-800 text-xs font-mono text-zinc-200 focus:outline-none focus:border-indigo-500"
            >
              <option value="openai">openai-compatible</option>
              <option value="anthropic">anthropic</option>
              <option value="google">google</option>
              <option value="custom">custom</option>
            </select>
          </div>
          <input
            value={form.baseUrl}
            onChange={(event) => setForm({ ...form, baseUrl: event.target.value })}
            placeholder="https://deepseek-api.cs-hub.xyz/v1"
            required
            autoCapitalize="none"
            spellCheck={false}
            className="w-full px-3 py-2 rounded-xl bg-zinc-950 border border-zinc-800 text-xs font-mono text-zinc-100 focus:outline-none focus:border-indigo-500"
          />
          <input
            value={form.apiKey}
            onChange={(event) => setForm({ ...form, apiKey: event.target.value })}
            placeholder={editingId ? 'API key (leave empty to keep it)' : 'API key (optional)'}
            autoCapitalize="none"
            spellCheck={false}
            className="w-full px-3 py-2 rounded-xl bg-zinc-950 border border-zinc-800 text-xs font-mono text-zinc-100 focus:outline-none focus:border-indigo-500"
          />
          <input
            value={form.models}
            onChange={(event) => setForm({ ...form, models: event.target.value })}
            placeholder="Models, comma separated"
            className="w-full px-3 py-2 rounded-xl bg-zinc-950 border border-zinc-800 text-xs font-mono text-zinc-100 focus:outline-none focus:border-indigo-500"
          />
          <div className="grid grid-cols-2 gap-2">
            <input
              value={form.defaultModel}
              onChange={(event) => setForm({ ...form, defaultModel: event.target.value })}
              placeholder="Default model"
              className="px-3 py-2 rounded-xl bg-zinc-950 border border-zinc-800 text-xs font-mono text-zinc-100 focus:outline-none focus:border-indigo-500"
            />
            <button
              type="button"
              onClick={() => setForm({ ...form, enabled: !form.enabled })}
              className={`px-3 py-2 rounded-xl border text-xs font-mono transition ${
                form.enabled
                  ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                  : 'border-zinc-800 bg-zinc-950 text-zinc-500'
              }`}
            >
              {form.enabled ? 'enabled' : 'disabled'}
            </button>
          </div>
          <button
            type="submit"
            disabled={saving}
            className="w-full py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-xs font-semibold text-white inline-flex items-center justify-center gap-1.5 transition"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
            <span>{editingId ? 'Save changes' : 'Add provider'}</span>
          </button>
        </form>
      )}

      {!showForm && canManage && providers.length > 0 && (
        <button
          type="button"
          onClick={() => {
            setForm(emptyForm);
            setEditingId(null);
            setShowForm(true);
          }}
          className="text-[10px] text-zinc-500 hover:text-zinc-300 inline-flex items-center gap-1 transition"
        >
          <ChevronDown className="w-3 h-3" /> configure another endpoint
        </button>
      )}
    </div>
  );
};
