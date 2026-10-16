import React from 'react';
import type { TaskStatus, AgentStatus } from '../types/agent';
import { 
  CheckCircle2, 
  Clock, 
  PlayCircle, 
  AlertTriangle, 
  XCircle, 
  Ban,
  Radio,
  Wifi,
  WifiOff,
  Activity
} from 'lucide-react';

interface TaskStatusBadgeProps {
  status: TaskStatus;
  size?: 'sm' | 'md' | 'lg';
}

export const TaskStatusBadge: React.FC<TaskStatusBadgeProps> = ({ status, size = 'md' }) => {
  const sizeClasses = {
    sm: 'text-xs px-2 py-0.5 gap-1',
    md: 'text-xs px-2.5 py-1 gap-1.5',
    lg: 'text-sm px-3 py-1.5 gap-2',
  }[size];

  switch (status) {
    case 'queued':
      return (
        <span className={`inline-flex items-center font-medium rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20 ${sizeClasses}`}>
          <Clock className="w-3.5 h-3.5" />
          <span>Queued</span>
        </span>
      );
    case 'running':
      return (
        <span className={`inline-flex items-center font-medium rounded-full bg-blue-500/15 text-blue-400 border border-blue-500/30 ${sizeClasses}`}>
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500"></span>
          </span>
          <span>Running</span>
        </span>
      );
    case 'awaiting_approval':
      return (
        <span className={`inline-flex items-center font-medium rounded-full bg-purple-500/15 text-purple-300 border border-purple-500/30 ${sizeClasses}`}>
          <AlertTriangle className="w-3.5 h-3.5 text-purple-400" />
          <span>Needs Review</span>
        </span>
      );
    case 'completed':
      return (
        <span className={`inline-flex items-center font-medium rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 ${sizeClasses}`}>
          <CheckCircle2 className="w-3.5 h-3.5" />
          <span>Completed</span>
        </span>
      );
    case 'failed':
      return (
        <span className={`inline-flex items-center font-medium rounded-full bg-rose-500/10 text-rose-400 border border-rose-500/20 ${sizeClasses}`}>
          <XCircle className="w-3.5 h-3.5" />
          <span>Failed</span>
        </span>
      );
    case 'cancelled':
      return (
        <span className={`inline-flex items-center font-medium rounded-full bg-zinc-800 text-zinc-400 border border-zinc-700 ${sizeClasses}`}>
          <Ban className="w-3.5 h-3.5" />
          <span>Cancelled</span>
        </span>
      );
    default:
      return (
        <span className={`inline-flex items-center font-medium rounded-full bg-zinc-800 text-zinc-300 ${sizeClasses}`}>
          <span>{status}</span>
        </span>
      );
  }
};

interface AgentStatusBadgeProps {
  status: AgentStatus;
  isSimulated?: boolean;
}

export const AgentStatusBadge: React.FC<AgentStatusBadgeProps> = ({ status, isSimulated }) => {
  switch (status) {
    case 'online':
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
          <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
          <span>Online</span>
          {isSimulated && <span className="text-[10px] text-emerald-300/70 ml-0.5">(Sim)</span>}
        </span>
      );
    case 'busy':
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-blue-500/15 text-blue-400 border border-blue-500/30">
          <Activity className="w-3.5 h-3.5 animate-spin" />
          <span>Executing</span>
        </span>
      );
    case 'reconnecting':
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20">
          <Radio className="w-3.5 h-3.5 animate-pulse" />
          <span>Reconnecting</span>
        </span>
      );
    case 'offline':
    default:
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-zinc-800 text-zinc-400 border border-zinc-700">
          <WifiOff className="w-3.5 h-3.5" />
          <span>No agent online</span>
        </span>
      );
  }
};
