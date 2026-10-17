import React, { useEffect, useState } from 'react';
import type { AgentInfo, ProviderInfo, TaskMode, CreateTaskPayload } from '../../types/agent';
import { createTask, ApiError, fetchProviders, fetchProviderModels } from '../../services/api';
import { 
  Send, 
  X, 
  Sparkles, 
  FolderGit2, 
  ShieldCheck, 
  Cpu, 
  AlertCircle, 
  CheckCircle2, 
  Loader2,
  Laptop,
  RefreshCw,
  WifiOff
} from 'lucide-react';

interface NewTaskScreenProps {
  workspaces: string[];
  defaultWorkspace: string;
  agentConnected: boolean;
  /** the fleet: lets the operator pin the task to one machine */
  agents: AgentInfo[];
  onTaskCreated: (taskId: string) => void;
  onCancel: () => void;
}

export const NewTaskScreen: React.FC<NewTaskScreenProps> = ({
  workspaces,
  defaultWorkspace,
  agentConnected,
  agents,
  onTaskCreated,
  onCancel,
}) => {
  const [instruction, setInstruction] = useState('');
  const [workspace, setWorkspace] = useState(defaultWorkspace || workspaces[0] || '~/dev/mobile-agent-platform');
  const [mode, setMode] = useState<TaskMode>('supervised');
  const [model, setModel] = useState('');
  const [targetAgentId, setTargetAgentId] = useState<string>('');
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [providerId, setProviderId] = useState<string>('');
  const [models, setModels] = useState<string[]>([]);
  const [modelsSource, setModelsSource] = useState<'live' | 'config' | 'none'>('none');
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchProviders()
      .then((data) => {
        const enabled = (data.providers || []).filter((provider) => provider.enabled);
        setProviders(enabled);
        // Pre-select the gateway default so the model list can be fetched.
        setProviderId((current) =>
          current || (enabled.find((provider) => provider.isDefault) || enabled[0])?.id || '',
        );
      })
      .catch(() => setProviders([]));
  }, []);

  // Models come from the provider itself (GET {baseUrl}/models), so a new model
  // shows up without an app update. The gateway caches the answer for a few hours.
  const loadModels = React.useCallback(
    (refresh = false) => {
      if (!providerId) {
        setModels([]);
        setModelsSource('none');
        return;
      }
      setModelsLoading(true);
      fetchProviderModels(providerId, refresh)
        .then((data) => {
          setModels(data.models || []);
          setModelsSource(data.source);
          setModelsError(data.error ?? null);
          setModel((current) =>
            (data.models || []).includes(current)
              ? current
              : data.defaultModel || data.models?.[0] || '',
          );
        })
        .catch((err) => {
          setModels([]);
          setModelsSource('none');
          setModelsError(err?.message || 'Could not list models');
        })
        .finally(() => setModelsLoading(false));
    },
    [providerId],
  );

  useEffect(() => {
    loadModels();
  }, [loadModels]);

  const onlineAgents = agents.filter((agent) => agent.online);

  const promptChips = [
    'Add rate-limiter middleware for API routes',
    'Fix failing unit test in connection pool',
    'Implement health check endpoint with latency',
    'Refactor error handling to custom ApiError class',
  ];

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!instruction.trim() || isSubmitting) return;

    setIsSubmitting(true);
    setError(null);

    try {
      const payload: CreateTaskPayload = {
        instruction: instruction.trim(),
        workspace,
        mode,
        model,
        ...(targetAgentId ? { agentId: targetAgentId } : {}),
        ...(providerId ? { providerId } : {}),
      };

      const result = await createTask(payload);
      if (result.task && result.task.id) {
        onTaskCreated(result.task.id);
      } else {
        throw new Error('API returned invalid task response');
      }
    } catch (err: any) {
      console.error('Failed to create task:', err);
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(err.message || 'Network error: could not reach gateway server.');
      }
      setIsSubmitting(false);
    }
  };

  return (
    <div className="space-y-4 pb-12">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-white">Create New Task</h2>
          <p className="text-xs text-zinc-400">Dispatch instructions to local coding agent</p>
        </div>
        <button
          onClick={onCancel}
          className="p-2 rounded-xl text-zinc-400 hover:text-white hover:bg-zinc-800 transition"
          aria-label="Cancel"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Offline Warning Banner */}
      {!agentConnected && (
        <div className="p-3 rounded-xl bg-amber-950/40 border border-amber-500/30 text-amber-300 text-xs flex items-start gap-2.5">
          <WifiOff className="w-4 h-4 shrink-0 mt-0.5 text-amber-400" />
          <div className="space-y-1">
            <p className="font-semibold text-amber-200">
              {targetAgentId ? `Agent '${targetAgentId}' is offline` : 'No agent is online right now'}
            </p>
            <p className="text-[11px] text-amber-300/80 leading-relaxed">
              The task is accepted and queued safely on the gateway. Execution starts as soon as a
              matching worker connects (or reconnects).
            </p>
          </div>
        </div>
      )}

      {/* Error alert */}
      {error && (
        <div className="p-3 rounded-xl bg-rose-950/50 border border-rose-500/40 text-rose-300 text-xs flex items-start gap-2.5">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-rose-400" />
          <div className="min-w-0 flex-1">
            <p className="font-medium text-rose-200">Task Submission Failed</p>
            <p className="text-[11px] text-rose-300/80 mt-0.5">{error}</p>
          </div>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Instruction Input */}
        <div className="space-y-1.5">
          <label className="text-xs font-semibold uppercase tracking-wider text-zinc-400 block">
            Instructions <span className="text-rose-400">*</span>
          </label>
          <textarea
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            disabled={isSubmitting}
            rows={4}
            placeholder="e.g., Implement Redis token bucket rate limiter for auth routes and add unit tests..."
            className="w-full rounded-2xl bg-zinc-900 border border-zinc-800 p-3.5 text-sm text-white placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500 disabled:opacity-50 transition"
            required
          />
        </div>

        {/* Quick prompt templates chips */}
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium text-zinc-400">
            <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
            <span>Quick Suggestions</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {promptChips.map((chip, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => setInstruction(chip)}
                className="px-2.5 py-1.5 rounded-lg bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-[11px] text-zinc-300 text-left transition active:scale-95"
              >
                {chip}
              </button>
            ))}
          </div>
        </div>

        {/* Workspace Selection (editable: a repo inside one of the agent's roots) */}
        <div className="space-y-1.5">
          <label className="text-xs font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
            <FolderGit2 className="w-3.5 h-3.5 text-zinc-500" />
            <span>Target Workspace</span>
          </label>
          <input
            list="devagent-workspaces"
            value={workspace}
            onChange={(e) => setWorkspace(e.target.value)}
            disabled={isSubmitting}
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            placeholder="/Users/artur/PyProg/my-repo"
            className="w-full rounded-xl bg-zinc-900 border border-zinc-800 px-3 py-2.5 text-xs font-mono text-zinc-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 transition disabled:opacity-60"
          />
          <datalist id="devagent-workspaces">
            {workspaces.map((ws) => (
              <option key={ws} value={ws} />
            ))}
          </datalist>
          <p className="text-[10px] text-zinc-500">
            Type any folder or pick a suggestion. It must live inside the agent's allowed roots
            (<span className="font-mono">DEVAGENT_WORKSPACES</span>) and be a git repository.
          </p>
          {workspace.trim() !== '' && !workspace.trim().startsWith('/') && !workspace.trim().startsWith('~/') && (
            <p className="text-[10px] text-amber-400">
              Relative paths are resolved against the agent's first root — prefer an absolute path.
            </p>
          )}
        </div>

        {/* Worker selection (multi-machine fleets) */}
        <div className="space-y-1.5">
          <label className="text-xs font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
            <Laptop className="w-3.5 h-3.5 text-zinc-500" />
            <span>Run on</span>
          </label>
          <select
            value={targetAgentId}
            onChange={(e) => setTargetAgentId(e.target.value)}
            disabled={isSubmitting}
            className="w-full rounded-xl bg-zinc-900 border border-zinc-800 px-3 py-2.5 text-xs font-mono text-zinc-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 transition"
          >
            <option value="">Any idle agent ({onlineAgents.length} online)</option>
            {agents.map((agent) => (
              <option key={agent.agentId} value={agent.agentId}>
                {agent.hostname} · {agent.agentId} {agent.online ? '· online' : '· offline'}
              </option>
            ))}
          </select>
          <p className="text-[10px] text-zinc-500">
            Pin the task to one machine, or let the gateway pick the first capable idle worker.
          </p>
        </div>

        {/* AI provider (the gateway falls back to its default) */}
        <div className="space-y-1.5">
          <label className="text-xs font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
            <Cpu className="w-3.5 h-3.5 text-zinc-500" />
            <span>AI provider</span>
          </label>
          <select
            value={providerId}
            onChange={(e) => setProviderId(e.target.value)}
            disabled={isSubmitting}
            className="w-full rounded-xl bg-zinc-900 border border-zinc-800 px-3 py-2.5 text-xs font-mono text-zinc-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 transition"
          >
            <option value="">Gateway default</option>
            {providers.map((provider) => (
              <option key={provider.id} value={provider.id}>
                {provider.name}
                {provider.isDefault ? ' (default)' : ''} · {provider.defaultModel || 'model n/a'}
              </option>
            ))}
          </select>
          <p className="text-[10px] text-zinc-500">
            Manage providers in Settings → AI providers. Selection is per task and never touches
            the agent connection.
          </p>
        </div>

        {/* Execution Mode */}
        <div className="space-y-1.5">
          <label className="text-xs font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
            <ShieldCheck className="w-3.5 h-3.5 text-zinc-500" />
            <span>Execution Mode</span>
          </label>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => setMode('supervised')}
              className={`p-3 rounded-xl border text-left transition ${
                mode === 'supervised'
                  ? 'bg-purple-950/40 border-purple-500/60 text-purple-200'
                  : 'bg-zinc-900 border-zinc-800 text-zinc-400 hover:bg-zinc-800'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold">Supervised</span>
                {mode === 'supervised' && <CheckCircle2 className="w-3.5 h-3.5 text-purple-400" />}
              </div>
              <p className="text-[10px] text-zinc-400 mt-1">
                Pauses for diff inspection and approval before committing
              </p>
            </button>

            <button
              type="button"
              onClick={() => setMode('autonomous')}
              className={`p-3 rounded-xl border text-left transition ${
                mode === 'autonomous'
                  ? 'bg-indigo-950/40 border-indigo-500/60 text-indigo-200'
                  : 'bg-zinc-900 border-zinc-800 text-zinc-400 hover:bg-zinc-800'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold">Autonomous</span>
                {mode === 'autonomous' && <CheckCircle2 className="w-3.5 h-3.5 text-indigo-400" />}
              </div>
              <p className="text-[10px] text-zinc-400 mt-1">
                Executes end-to-end, runs tests, and completes automatically
              </p>
            </button>
          </div>
        </div>

        {/* Model Selection (loaded from the provider, not hardcoded) */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <label className="text-xs font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
              <Cpu className="w-3.5 h-3.5 text-zinc-500" />
              <span>AI Model Engine</span>
            </label>
            <button
              type="button"
              onClick={() => loadModels(true)}
              disabled={isSubmitting || modelsLoading || !providerId}
              className="text-[10px] font-mono text-zinc-400 hover:text-white flex items-center gap-1 disabled:opacity-50 transition"
              title="Re-read the model list from the provider"
            >
              <RefreshCw className={`w-3 h-3 ${modelsLoading ? 'animate-spin' : ''}`} />
              {modelsSource === 'live' ? 'live' : modelsSource === 'config' ? 'configured' : 'refresh'}
            </button>
          </div>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={isSubmitting || modelsLoading || models.length === 0}
            className="w-full rounded-xl bg-zinc-900 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 transition disabled:opacity-60"
          >
            {models.length === 0 && (
              <option value="">{modelsLoading ? 'Loading models…' : 'No models available'}</option>
            )}
            {models.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
                {entry === providers.find((p) => p.id === providerId)?.defaultModel ? ' (default)' : ''}
              </option>
            ))}
          </select>
          <p className="text-[10px] text-zinc-500">
            {modelsError
              ? `Provider did not answer /models (${modelsError}); showing configured names.`
              : modelsSource === 'live'
                ? 'Fetched live from the provider — new models appear automatically.'
                : 'Names configured in Settings → AI providers.'}
          </p>
        </div>

        {/* Form Actions */}
        <div className="pt-2 flex items-center gap-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={isSubmitting}
            className="flex-1 py-3 px-4 rounded-xl border border-zinc-800 text-zinc-300 font-medium text-xs hover:bg-zinc-900 transition min-h-[44px]"
          >
            Cancel
          </button>

          <button
            type="submit"
            disabled={!instruction.trim() || isSubmitting}
            className="flex-1 py-3 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-medium text-xs flex items-center justify-center gap-2 shadow-sm transition active:scale-95 min-h-[44px]"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Queuing...</span>
              </>
            ) : (
              <>
                <Send className="w-4 h-4" />
                <span>Dispatch Task</span>
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  );
};
