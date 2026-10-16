import React from 'react';
import type { AgentInfo, AgentStatus } from '../types/agent';
import { AgentStatusBadge } from './StatusBadge';
import { PWAInstallButton } from './PWAInstallButton';
import { Terminal, RefreshCw, Wifi, WifiOff } from 'lucide-react';

interface HeaderProps {
  agentStatus: AgentStatus;
  isSimulated?: boolean;
  /** the whole fleet: the badge summarises it when more than one machine is connected */
  agents?: AgentInfo[];
  onRefresh?: () => void;
  isRefreshing?: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  agentStatus,
  isSimulated,
  agents,
  onRefresh,
  isRefreshing,
}) => {
  const online = (agents || []).filter((agent) => agent.online);
  const fleetBadge =
    agents && agents.length > 0 ? (
      <span
        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${
          online.length > 0
            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
            : 'bg-zinc-800 text-zinc-400 border-zinc-700'
        }`}
        title={agents
          .map((agent) => `${agent.hostname}: ${agent.online ? 'online' : 'offline'}`)
          .join('\n')}
      >
        {online.length > 0 ? <Wifi className="w-3.5 h-3.5" /> : <WifiOff className="w-3.5 h-3.5" />}
        <span>
          {online.length === 0
            ? 'No agent online'
            : online.length === 1
            ? `${online[0].hostname} online`
            : `${online.length} agents online`}
        </span>
      </span>
    ) : null;

  return (
    <header className="sticky top-0 z-40 bg-zinc-950/90 backdrop-blur-md border-b border-zinc-800/80 px-4 py-2.5 transition">
      <div className="max-w-xl mx-auto flex items-center justify-between gap-2">
        {/* Brand */}
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-indigo-600/20 text-indigo-400 border border-indigo-500/30 shrink-0">
            <Terminal className="w-4 h-4" />
          </div>
          <div className="min-w-0">
            <h1 className="text-sm font-semibold tracking-tight text-white leading-tight truncate">
              DevAgent
            </h1>
            <p className="text-[10px] text-zinc-400 font-mono truncate">
              platform gateway
            </p>
          </div>
        </div>

        {/* Right Actions: Status Badge & Install / Refresh */}
        <div className="flex items-center gap-2 shrink-0">
          {fleetBadge || <AgentStatusBadge status={agentStatus} isSimulated={isSimulated} />}

          {onRefresh && (
            <button
              onClick={onRefresh}
              disabled={isRefreshing}
              className="p-2 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition active:scale-95 disabled:opacity-50 min-w-[40px] min-h-[40px] flex items-center justify-center"
              aria-label="Refresh gateway status and tasks"
              title="Refresh"
            >
              <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin text-indigo-400' : ''}`} />
            </button>
          )}

          <PWAInstallButton variant="header" />
        </div>
      </div>
    </header>
  );
};
