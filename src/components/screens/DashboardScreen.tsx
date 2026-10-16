import React from 'react';
import type { AgentInfo, Diagnostics, Task } from '../../types/agent';
import { TaskStatusBadge, AgentStatusBadge } from '../StatusBadge';
import { PWAInstallButton } from '../PWAInstallButton';
import { 
  Laptop, 
  FolderGit2, 
  Clock, 
  CheckCircle2, 
  AlertTriangle, 
  ChevronRight, 
  Plus, 
  Layers, 
  Terminal,
  Activity,
  ArrowUpRight
} from 'lucide-react';

interface DashboardScreenProps {
  agentInfo: AgentInfo | null;
  agentConnected: boolean;
  tasks: Task[];
  diagnostics: Diagnostics | null;
  onNavigateToTask: (taskId: string) => void;
  onNavigateToNewTask: () => void;
  onNavigateToApprovals: () => void;
  onNavigateToTasks: () => void;
}

export const DashboardScreen: React.FC<DashboardScreenProps> = ({
  agentInfo,
  agentConnected,
  tasks,
  diagnostics,
  onNavigateToTask,
  onNavigateToNewTask,
  onNavigateToApprovals,
  onNavigateToTasks,
}) => {
  const runningTask = tasks.find((t) => t.status === 'running');
  const awaitingTask = tasks.find((t) => t.status === 'awaiting_approval');
  const queuedTasks = tasks.filter((t) => t.status === 'queued');
  const lastCompleted = tasks.find((t) => t.status === 'completed');
  const recentTasks = tasks.slice(0, 4);

  return (
    <div className="space-y-4 pb-6">
      {/* 1. Agent Status Card */}
      <div className="p-4 rounded-2xl bg-zinc-900 border border-zinc-800 shadow-sm relative overflow-hidden">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className={`p-2.5 rounded-xl border ${
              agentConnected 
                ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' 
                : 'bg-zinc-800 border-zinc-700 text-zinc-500'
            }`}>
              <Laptop className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold text-white">
                  {agentInfo?.hostname || 'MacBook Agent'}
                </h2>
              </div>
              <p className="text-xs text-zinc-400 font-mono flex items-center gap-1 mt-0.5">
                <FolderGit2 className="w-3.5 h-3.5 text-zinc-500" />
                <span className="truncate max-w-[210px] sm:max-w-xs">
                  {agentInfo?.workspace || '~/dev/mobile-agent-platform'}
                </span>
              </p>
            </div>
          </div>
          <AgentStatusBadge 
            status={agentInfo?.status || (agentConnected ? 'online' : 'offline')} 
            isSimulated={agentInfo?.isSimulated}
          />
        </div>

        {/* Diagnostic Meta Bar */}
        <div className="mt-3 pt-3 border-t border-zinc-800/80 grid grid-cols-3 gap-2 text-center text-[11px]">
          <div className="bg-zinc-950/40 p-2 rounded-lg border border-zinc-800/40">
            <span className="text-zinc-500 block">Agent OS</span>
            <span className="text-zinc-300 font-mono font-medium truncate block">macOS arm64</span>
          </div>
          <div className="bg-zinc-950/40 p-2 rounded-lg border border-zinc-800/40">
            <span className="text-zinc-500 block">Queue</span>
            <span className="text-zinc-300 font-semibold block">
              {queuedTasks.length} {queuedTasks.length === 1 ? 'task' : 'tasks'}
            </span>
          </div>
          <div className="bg-zinc-950/40 p-2 rounded-lg border border-zinc-800/40">
            <span className="text-zinc-500 block">Gateway</span>
            <span className="text-emerald-400 font-semibold block">Active</span>
          </div>
        </div>
      </div>

      {/* 2. Urgent Attention: Awaiting Approval Banner */}
      {awaitingTask && (
        <div className="p-4 rounded-2xl bg-gradient-to-r from-purple-950/70 to-indigo-950/70 border border-purple-600/40 shadow-sm relative overflow-hidden">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <div className="p-2 rounded-lg bg-purple-500/20 text-purple-300 border border-purple-500/30">
                <AlertTriangle className="w-5 h-5" />
              </div>
              <div>
                <span className="text-[10px] font-bold uppercase tracking-wider text-purple-300">
                  Action Required
                </span>
                <h3 className="text-sm font-semibold text-white leading-tight">
                  Code Review & Approval
                </h3>
              </div>
            </div>
            <span className="text-[10px] font-mono bg-purple-900/60 text-purple-200 px-2 py-0.5 rounded border border-purple-700/50">
              {awaitingTask.id}
            </span>
          </div>

          <p className="text-xs text-zinc-300 mt-2 line-clamp-2">
            {awaitingTask.instruction}
          </p>

          <div className="mt-3 flex items-center justify-between gap-2 pt-2 border-t border-purple-800/30">
            <span className="text-[11px] text-purple-300">
              {awaitingTask.approval?.proposedChanges.length || 0} files ready for inspection
            </span>
            <button
              onClick={() => onNavigateToTask(awaitingTask.id)}
              className="px-3.5 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-500 text-white font-medium text-xs flex items-center gap-1.5 shadow-sm transition active:scale-95"
            >
              <span>Inspect Diff</span>
              <ArrowUpRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}

      {/* 3. Live Running Execution */}
      {runningTask && (
        <div 
          onClick={() => onNavigateToTask(runningTask.id)}
          className="p-4 rounded-2xl bg-blue-950/40 border border-blue-600/40 cursor-pointer hover:border-blue-500 transition active:scale-[0.99]"
        >
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className="relative flex h-2.5 w-2.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-500"></span>
              </span>
              <span className="text-xs font-semibold text-blue-300 uppercase tracking-wide">
                Live Agent Execution
              </span>
            </div>
            <span className="text-xs text-zinc-400 font-mono">
              Step {runningTask.stepIndex || 1} of {runningTask.totalSteps || 4}
            </span>
          </div>

          <p className="text-sm font-medium text-white mt-1.5 truncate">
            {runningTask.instruction}
          </p>

          {runningTask.currentStep && (
            <div className="mt-2.5 flex items-center gap-2 text-xs text-blue-200/90 font-mono bg-blue-900/30 p-2 rounded-lg border border-blue-800/30">
              <Activity className="w-3.5 h-3.5 animate-spin text-blue-400 shrink-0" />
              <span className="truncate">{runningTask.currentStep}</span>
            </div>
          )}
        </div>
      )}

      {/* 4. Action: Start New Task */}
      <div className="grid grid-cols-2 gap-3">
        <button
          onClick={onNavigateToNewTask}
          className="p-3.5 rounded-2xl bg-indigo-600 hover:bg-indigo-500 text-white font-medium text-sm flex items-center justify-center gap-2 shadow-sm transition active:scale-95 col-span-2 min-h-[48px]"
        >
          <Plus className="w-5 h-5 stroke-[2.5]" />
          <span>Dispatch New Task</span>
        </button>
      </div>

      {/* 5. Last Completed Result */}
      {lastCompleted && (
        <div 
          onClick={() => onNavigateToTask(lastCompleted.id)}
          className="p-4 rounded-2xl bg-zinc-900/80 border border-zinc-800 hover:border-zinc-700 transition cursor-pointer"
        >
          <div className="flex items-center justify-between gap-2 mb-2">
            <span className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
              Last Completed Task
            </span>
            <span className="text-xs font-mono text-zinc-500">{lastCompleted.id}</span>
          </div>

          <p className="text-xs font-medium text-zinc-200 line-clamp-1">
            {lastCompleted.instruction}
          </p>

          {lastCompleted.result && (
            <div className="mt-2 text-xs text-zinc-400 bg-zinc-950/60 p-2.5 rounded-xl border border-zinc-800/60">
              <p className="text-zinc-300 font-normal leading-relaxed line-clamp-2">
                {lastCompleted.result.summary}
              </p>
              <div className="mt-2 flex items-center gap-3 text-[11px] font-mono">
                <span className="text-emerald-400 flex items-center gap-1">
                  ✓ Tests Passed
                </span>
                <span className="text-zinc-500">•</span>
                <span className="text-zinc-400">
                  {lastCompleted.result.filesModified.length} files modified
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 6. Recent Task History */}
      <div className="space-y-2">
        <div className="flex items-center justify-between px-1">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
            Recent Tasks
          </h3>
          <button
            onClick={onNavigateToTasks}
            className="text-xs text-indigo-400 hover:text-indigo-300 flex items-center gap-0.5"
          >
            <span>View All ({tasks.length})</span>
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="divide-y divide-zinc-800/70 rounded-2xl bg-zinc-900 border border-zinc-800 overflow-hidden shadow-sm">
          {recentTasks.length === 0 ? (
            <div className="p-6 text-center text-xs text-zinc-500">
              No tasks in history yet.
            </div>
          ) : (
            recentTasks.map((task) => (
              <button
                key={task.id}
                onClick={() => onNavigateToTask(task.id)}
                className="w-full text-left p-3.5 hover:bg-zinc-800/40 transition flex items-center justify-between gap-3 group"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <TaskStatusBadge status={task.status} size="sm" />
                    <span className="text-[11px] font-mono text-zinc-500">{task.id}</span>
                  </div>
                  <p className="text-xs font-medium text-zinc-200 line-clamp-1 group-hover:text-white transition">
                    {task.instruction}
                  </p>
                </div>
                <ChevronRight className="w-4 h-4 text-zinc-600 group-hover:text-zinc-400 shrink-0" />
              </button>
            ))
          )}
        </div>
      </div>

      {/* Install Card for Mobile Browser Users */}
      <PWAInstallButton variant="card" />
    </div>
  );
};
