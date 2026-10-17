import React, { useState } from 'react';
import type { Diagnostics, AgentInfo } from '../../types/agent';
import { PWAInstallButton } from '../PWAInstallButton';
import { GatewayConfigCard } from '../GatewayConfigCard';
import { AgentsCard } from '../AgentsCard';
import { ApiKeysCard } from '../ApiKeysCard';
import { InstallAgentCard } from '../InstallAgentCard';
import { ProvidersCard } from '../ProvidersCard';
import { 
  Server, 
  Laptop, 
  Cpu, 
  ShieldCheck, 
  Activity, 
  Wifi, 
  WifiOff, 
  Layers, 
  Clock, 
  Info
} from 'lucide-react';

interface SettingsScreenProps {
  diagnostics: Diagnostics | null;
  agentInfo: AgentInfo | null;
  agents: AgentInfo[];
  gatewayReachable?: boolean;
  canManageTokens?: boolean;
  onLogout?: () => void;
  onRefresh: () => void;
}

export const SettingsScreen: React.FC<SettingsScreenProps> = ({
  diagnostics,
  agentInfo,
  agents,
  gatewayReachable = true,
  canManageTokens = false,
  onLogout,
  onRefresh,
}) => {
  return (
    <div className="space-y-4 pb-12">
      {/* Header */}
      <div>
        <h2 className="text-base font-semibold text-white">Diagnostics & Gateway</h2>
        <p className="text-xs text-zinc-400">System infrastructure and agent connectivity</p>
      </div>

      {/* 0. Gateway URL + shared token (needed by the iOS app) */}
      <GatewayConfigCard onSaved={onRefresh} />

      {/* 0b. API keys (create / revoke, like a provider dashboard) */}
      <ApiKeysCard canManage={canManageTokens} onKeysChanged={onRefresh} />

      {/* 1. The worker fleet (every machine that can execute tasks) */}
      <AgentsCard agents={agents} onChanged={onRefresh} gatewayReachable={gatewayReachable} />

      {/* 1b. Primary agent details */}
      <div className="p-4 rounded-2xl bg-zinc-900 border border-zinc-800 shadow-sm space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <Laptop className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-white">
                Primary agent
              </h3>
              <p className="text-[11px] text-zinc-400 font-mono">
                {agentInfo?.hostname || 'no agent has checked in yet'}
              </p>
            </div>
          </div>
          <span
            className={`text-xs px-2.5 py-1 rounded-full font-medium ${
              agentInfo?.status === 'online' || agentInfo?.status === 'busy'
                ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                : 'bg-zinc-800 text-zinc-400'
            }`}
          >
            {agentInfo?.status || 'Offline'}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-2 text-xs font-mono">
          <div className="p-2.5 rounded-xl bg-zinc-950/60 border border-zinc-800/60">
            <span className="text-[10px] text-zinc-500 block uppercase">OS / Architecture</span>
            <span className="text-zinc-200 truncate block mt-0.5">
              {agentInfo?.os || '—'}
            </span>
          </div>

          <div className="p-2.5 rounded-xl bg-zinc-950/60 border border-zinc-800/60">
            <span className="text-[10px] text-zinc-500 block uppercase">Last Heartbeat</span>
            <span className="text-zinc-200 truncate block mt-0.5">
              {diagnostics?.agent.secondsSinceHeartbeat !== null && diagnostics?.agent.secondsSinceHeartbeat !== undefined
                ? `${diagnostics.agent.secondsSinceHeartbeat}s ago`
                : 'Never'}
            </span>
          </div>

          <div className="p-2.5 rounded-xl bg-zinc-950/60 border border-zinc-800/60 col-span-2">
            <span className="text-[10px] text-zinc-500 block uppercase">Active Workspace</span>
            <span className="text-zinc-200 truncate block mt-0.5">
              {agentInfo?.workspace || '—'}
            </span>
          </div>
        </div>

        {/* Worker simulator removed: only real agents execute tasks. */}
      </div>

      {/* 2. Install the worker: compiled binary from GitHub Releases (macOS / Windows) */}
      <InstallAgentCard />

      {/* 3. AI providers (add / edit / default; attached per task) */}
      <ProvidersCard canManage={canManageTokens} onChanged={onRefresh} />

      {/* 4. Queue Diagnostics Counters */}
      <div className="p-4 rounded-2xl bg-zinc-900 border border-zinc-800 space-y-2.5 shadow-sm">
        <div className="flex items-center gap-2">
          <Layers className="w-4 h-4 text-zinc-400" />
          <h3 className="text-xs font-semibold uppercase tracking-wider text-white">
            Queue Statistics
          </h3>
        </div>

        <div className="grid grid-cols-3 gap-2 text-center text-xs font-mono">
          <div className="p-2 rounded-xl bg-zinc-950 border border-zinc-800">
            <span className="text-zinc-500 text-[10px] block">Queued</span>
            <span className="text-amber-400 font-bold text-sm block">
              {diagnostics?.queue.queued ?? 0}
            </span>
          </div>
          <div className="p-2 rounded-xl bg-zinc-950 border border-zinc-800">
            <span className="text-zinc-500 text-[10px] block">Running</span>
            <span className="text-blue-400 font-bold text-sm block">
              {diagnostics?.queue.running ?? 0}
            </span>
          </div>
          <div className="p-2 rounded-xl bg-zinc-950 border border-zinc-800">
            <span className="text-zinc-500 text-[10px] block">Review</span>
            <span className="text-purple-400 font-bold text-sm block">
              {diagnostics?.queue.awaitingApproval ?? 0}
            </span>
          </div>
          <div className="p-2 rounded-xl bg-zinc-950 border border-zinc-800">
            <span className="text-zinc-500 text-[10px] block">Done</span>
            <span className="text-emerald-400 font-bold text-sm block">
              {diagnostics?.queue.completed ?? 0}
            </span>
          </div>
          <div className="p-2 rounded-xl bg-zinc-950 border border-zinc-800">
            <span className="text-zinc-500 text-[10px] block">Failed</span>
            <span className="text-rose-400 font-bold text-sm block">
              {diagnostics?.queue.failed ?? 0}
            </span>
          </div>
          <div className="p-2 rounded-xl bg-zinc-950 border border-zinc-800">
            <span className="text-zinc-500 text-[10px] block">Total</span>
            <span className="text-zinc-300 font-bold text-sm block">
              {diagnostics?.queue.total ?? 0}
            </span>
          </div>
        </div>
      </div>

      {/* 5. PWA Installation Status */}
      <PWAInstallButton variant="settings" />

      {/* 6. Session */}
      {onLogout && (
        <button
          onClick={onLogout}
          className="w-full py-2.5 rounded-xl bg-zinc-900 border border-zinc-800 hover:bg-zinc-800 text-xs font-medium text-rose-300 transition"
        >
          Sign out of this device
        </button>
      )}
    </div>
  );
};
