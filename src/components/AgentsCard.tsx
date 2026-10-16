import React, { useState } from 'react';
import type { AgentInfo } from '../types/agent';
import { removeAgent } from '../services/api';
import {
  Battery,
  Cpu,
  FolderGit2,
  Layers,
  Laptop,
  Loader2,
  ServerCog,
  Trash2,
  Wifi,
  WifiOff,
} from 'lucide-react';

interface AgentsCardProps {
  agents: AgentInfo[];
  /** compact variant is used on the dashboard, the full one in Settings */
  compact?: boolean;
  queuedCount?: number;
  onChanged?: () => void;
}

function heartbeatLabel(agent: AgentInfo): string {
  if (agent.online) return 'live';
  const seconds = agent.secondsSinceHeartbeat;
  if (seconds === null || seconds === undefined) return 'never seen';
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

const statusStyles: Record<string, string> = {
  online: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
  busy: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  reconnecting: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  offline: 'bg-zinc-800 text-zinc-400 border-zinc-700',
};

/**
 * The worker fleet: one row per agent (MacBook, second laptop, Linux box...).
 * Agents that stop heartbeating stay listed as offline so you can see the history
 * and forget decommissioned machines.
 */
export const AgentsCard: React.FC<AgentsCardProps> = ({
  agents,
  compact = false,
  queuedCount = 0,
  onChanged,
}) => {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const online = agents.filter((agent) => agent.online);
  const offline = agents.filter((agent) => !agent.online);
  const ordered = [...online, ...offline];
  const visible = compact ? ordered.slice(0, 3) : ordered;

  const handleForget = async (agentId: string) => {
    setBusyId(agentId);
    setError(null);
    try {
      await removeAgent(agentId);
      onChanged?.();
    } catch (err: any) {
      setError(err?.message || 'Could not remove the agent');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="p-4 rounded-2xl bg-zinc-900 border border-zinc-800 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <div
            className={`p-2.5 rounded-xl border shrink-0 ${
              online.length > 0
                ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
                : 'bg-zinc-800 border-zinc-700 text-zinc-500'
            }`}
          >
            <Laptop className="w-5 h-5" />
          </div>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-white flex items-center gap-2">
              {online.length === 0
                ? 'No agent connected'
                : online.length === 1
                ? online[0].hostname
                : `${online.length} agents online`}
            </h2>
            <p className="text-[11px] text-zinc-400 font-mono mt-0.5 flex items-center gap-1.5">
              {online.length > 0 ? (
                <>
                  <Wifi className="w-3.5 h-3.5 text-emerald-500" />
                  <span>
                    {online.length} online · {agents.length} known
                  </span>
                </>
              ) : (
                <>
                  <WifiOff className="w-3.5 h-3.5 text-zinc-500" />
                  <span>tasks stay queued until a worker reconnects</span>
                </>
              )}
            </p>
          </div>
        </div>
        <span className="text-[10px] font-mono px-2 py-1 rounded-full bg-zinc-800 border border-zinc-700 text-zinc-300 shrink-0">
          fleet
        </span>
      </div>

      {visible.length > 0 && (
        <div className="mt-3 space-y-2">
          {visible.map((agent) => (
            <div
              key={agent.agentId}
              className="p-2.5 rounded-xl bg-zinc-950/60 border border-zinc-800/60"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className={`w-2 h-2 rounded-full shrink-0 ${
                      agent.online ? 'bg-emerald-400' : 'bg-zinc-600'
                    }`}
                  />
                  <span className="text-xs font-medium text-zinc-100 truncate">
                    {agent.hostname}
                  </span>
                  <span className="text-[10px] font-mono text-zinc-500 truncate">
                    {agent.agentId}
                  </span>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  {agent.batteryLevel !== undefined && agent.batteryLevel !== null && (
                    <span className="text-[10px] font-mono text-zinc-400 flex items-center gap-0.5">
                      <Battery className="w-3 h-3" />
                      {agent.batteryLevel}%
                    </span>
                  )}
                  <span
                    className={`text-[10px] px-2 py-0.5 rounded-full border font-medium ${
                      statusStyles[agent.status] || statusStyles.offline
                    }`}
                  >
                    {agent.online ? agent.status : 'offline'}
                  </span>
                  {!compact && !agent.online && onChanged && (
                    <button
                      onClick={() => handleForget(agent.agentId)}
                      disabled={busyId === agent.agentId}
                      title="Remove this agent from the fleet"
                      className="p-1 rounded-lg text-zinc-500 hover:text-rose-400 hover:bg-zinc-800 transition disabled:opacity-50"
                    >
                      {busyId === agent.agentId ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="w-3.5 h-3.5" />
                      )}
                    </button>
                  )}
                </div>
              </div>

              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-zinc-400 font-mono">
                <span className="flex items-center gap-1 min-w-0">
                  <FolderGit2 className="w-3 h-3 text-zinc-500" />
                  <span className="truncate max-w-[180px] sm:max-w-xs">{agent.workspace}</span>
                </span>
                <span className="flex items-center gap-1">
                  <Cpu className="w-3 h-3 text-zinc-500" />
                  {agent.os}
                </span>
                <span>heartbeat: {heartbeatLabel(agent)}</span>
                {agent.capabilities && agent.capabilities.length > 0 && (
                  <span className="text-zinc-500">{agent.capabilities.join(', ')}</span>
                )}
              </div>

              {agent.activeTaskId && (
                <p className="mt-1 text-[10px] font-mono text-blue-300">
                  working on {agent.activeTaskId}
                </p>
              )}
            </div>
          ))}
          {compact && ordered.length > visible.length && (
            <p className="text-[10px] text-zinc-500 font-mono">
              +{ordered.length - visible.length} more in Settings
            </p>
          )}
        </div>
      )}

      {error && <p className="mt-2 text-[11px] text-rose-400 font-mono">{error}</p>}

      {compact && (
        <div className="mt-3 pt-3 border-t border-zinc-800/80 grid grid-cols-3 gap-2 text-center text-[11px]">
          <div className="bg-zinc-950/40 p-2 rounded-lg border border-zinc-800/40">
            <span className="text-zinc-500 block">Agents</span>
            <span className="text-zinc-300 font-semibold block">
              {online.length} / {agents.length}
            </span>
          </div>
          <div className="bg-zinc-950/40 p-2 rounded-lg border border-zinc-800/40">
            <span className="text-zinc-500 block">Queue</span>
            <span className="text-zinc-300 font-semibold block">
              {queuedCount} {queuedCount === 1 ? 'task' : 'tasks'}
            </span>
          </div>
          <div className="bg-zinc-950/40 p-2 rounded-lg border border-zinc-800/40">
            <span className="text-zinc-500 block">Gateway</span>
            <span className="text-emerald-400 font-semibold block">Active</span>
          </div>
        </div>
      )}

      {visible.length === 0 && (
        <div className="mt-3 p-3 rounded-xl bg-zinc-950/60 border border-zinc-800/60 space-y-1.5">
          <p className="text-[11px] text-zinc-300 flex items-center gap-1.5">
            <ServerCog className="w-3.5 h-3.5 text-zinc-500" />
            Start a worker to execute tasks:
          </p>
          <pre className="text-[10px] font-mono text-zinc-400 overflow-x-auto whitespace-pre-wrap">
{`python3 agent/devagent_agent.py --env-file ~/.devagent/agent.env`}
          </pre>
          <p className="text-[10px] text-zinc-500 flex items-center gap-1.5">
            <Layers className="w-3 h-3" />
            Each machine gets its own DEVAGENT_AGENT_ID and can be targeted per task.
          </p>
        </div>
      )}
    </div>
  );
};
