import React, { useState } from 'react';
import type { Diagnostics, AgentInfo } from '../../types/agent';
import { getGatewayBase, getGatewayToken, toggleAgentSimulator } from '../../services/api';
import { PWAInstallButton } from '../PWAInstallButton';
import { GatewayConfigCard } from '../GatewayConfigCard';
import { 
  Server, 
  Laptop, 
  Cpu, 
  ShieldCheck, 
  Activity, 
  Terminal, 
  Copy, 
  Check, 
  Wifi, 
  WifiOff, 
  Layers, 
  Clock, 
  ToggleLeft, 
  ToggleRight,
  Info
} from 'lucide-react';

interface SettingsScreenProps {
  diagnostics: Diagnostics | null;
  agentInfo: AgentInfo | null;
  onRefresh: () => void;
}

export const SettingsScreen: React.FC<SettingsScreenProps> = ({
  diagnostics,
  agentInfo,
  onRefresh,
}) => {
  const [copiedSnippet, setCopiedSnippet] = useState(false);
  const [isTogglingSim, setIsTogglingSim] = useState(false);

  const gatewayBase = getGatewayBase() || window.location.origin;
  const curlSnippet = `# pe MacBook (connects outbound, no inbound ports):
python3 devagent_agent.py --env-file ~/.devagent/agent.env --check

# heartbeat manual (fara executie de cod):
curl -X POST "${gatewayBase}/api/agent/heartbeat" \
  -H "Authorization: Bearer ${getGatewayToken() || '<DEVAGENT_TOKEN>'}" \
  -H "Content-Type: application/json" \
  -d '{"agentId":"macbook-pro","hostname":"$(hostname)","status":"online","workspace":"~/dev"}'`;

  const handleCopySnippet = () => {
    navigator.clipboard.writeText(curlSnippet);
    setCopiedSnippet(true);
    setTimeout(() => setCopiedSnippet(false), 2000);
  };

  const handleToggleSimulator = async () => {
    setIsTogglingSim(true);
    try {
      await toggleAgentSimulator();
      onRefresh();
    } catch (err) {
      console.error('Failed to toggle simulator:', err);
    } finally {
      setIsTogglingSim(false);
    }
  };

  return (
    <div className="space-y-4 pb-12">
      {/* Header */}
      <div>
        <h2 className="text-base font-semibold text-white">Diagnostics & Gateway</h2>
        <p className="text-xs text-zinc-400">System infrastructure and agent connectivity</p>
      </div>

      {/* 0. Gateway URL + shared token (needed by the iOS app) */}
      <GatewayConfigCard onSaved={onRefresh} />

      {/* 1. Agent Connectivity */}
      <div className="p-4 rounded-2xl bg-zinc-900 border border-zinc-800 shadow-sm space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <Laptop className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-white">
                MacBook Agent
              </h3>
              <p className="text-[11px] text-zinc-400 font-mono">
                {agentInfo?.hostname || 'Unknown host'}
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
              {agentInfo?.os || 'macOS 15.1 arm64'}
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
              {agentInfo?.workspace || '~/dev/mobile-agent-platform'}
            </span>
          </div>
        </div>

        {/* Worker Simulator Toggle for browser testing */}
        <div className="pt-2 border-t border-zinc-800/80 flex items-center justify-between">
          <div>
            <span className="text-xs font-medium text-white block">Agent Worker Simulator</span>
            <span className="text-[11px] text-zinc-400 block">
              Simulates MacBook agent execution and live approvals in browser
            </span>
          </div>
          <button
            onClick={handleToggleSimulator}
            disabled={isTogglingSim}
            className={`p-1.5 rounded-lg text-xs font-medium transition flex items-center gap-1.5 ${
              agentInfo?.isSimulated
                ? 'text-indigo-400 hover:text-indigo-300'
                : 'text-zinc-500 hover:text-zinc-300'
            }`}
            title="Toggle Agent Simulator"
          >
            {agentInfo?.isSimulated ? (
              <ToggleRight className="w-7 h-7 text-indigo-500" />
            ) : (
              <ToggleLeft className="w-7 h-7 text-zinc-600" />
            )}
          </button>
        </div>
      </div>

      {/* 2. Outbound Agent Connection Instructions */}
      <div className="p-4 rounded-2xl bg-zinc-900 border border-zinc-800 space-y-2.5 shadow-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-sky-400" />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-white">
              Connect Real MacBook Agent
            </h3>
          </div>
          <button
            onClick={handleCopySnippet}
            className="flex items-center gap-1 text-[11px] px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition"
          >
            {copiedSnippet ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{copiedSnippet ? 'Copied' : 'Copy'}</span>
          </button>
        </div>

        <p className="text-[11px] text-zinc-400 leading-relaxed">
          The MacBook agent connects <strong>outbound</strong> to this gateway. No public inbound ports on your MacBook are required. Run this heartbeat from your local terminal:
        </p>

        <div className="p-2.5 rounded-xl bg-black border border-zinc-800 font-mono text-[10px] text-zinc-300 overflow-x-auto whitespace-pre">
          {curlSnippet}
        </div>
      </div>

      {/* 3. AI Provider Configuration */}
      <div className="p-4 rounded-2xl bg-zinc-900 border border-zinc-800 space-y-2.5 shadow-sm">
        <div className="flex items-center gap-2">
          <div className="p-2 rounded-lg bg-purple-500/10 text-purple-400 border border-purple-500/20">
            <Cpu className="w-4 h-4" />
          </div>
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-white">
              AI Provider Endpoint
            </h3>
            <p className="text-[11px] text-zinc-400">
              {diagnostics?.aiProvider.provider || 'DeepSeek Proxy (Local/Private)'}
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2 text-xs font-mono">
          <div className="p-2.5 rounded-xl bg-zinc-950/60 border border-zinc-800/60">
            <span className="text-[10px] text-zinc-500 block uppercase">Default Model</span>
            <span className="text-zinc-200 truncate block mt-0.5">
              {diagnostics?.aiProvider.defaultModel || 'DeepSeek-Coder-V2.5'}
            </span>
          </div>

          <div className="p-2.5 rounded-xl bg-zinc-950/60 border border-zinc-800/60">
            <span className="text-[10px] text-zinc-500 block uppercase">Endpoint Status</span>
            <span className="text-emerald-400 truncate block mt-0.5 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
              Ready & Configured
            </span>
          </div>
        </div>
      </div>

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
    </div>
  );
};
