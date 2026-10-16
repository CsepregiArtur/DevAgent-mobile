import React, { useEffect, useState } from 'react';
import type { Task, TaskApproval } from '../../types/agent';
import { 
  fetchTask, 
  cancelTask, 
  retryTask, 
  respondToApproval, 
  subscribeToTaskEvents, 
  ApiError 
} from '../../services/api';
import { TaskStatusBadge } from '../StatusBadge';
import { DiffViewer } from '../DiffViewer';
import { 
  ArrowLeft, 
  Clock, 
  CheckCircle2, 
  XCircle, 
  AlertTriangle, 
  RotateCcw, 
  Ban, 
  Radio, 
  Activity, 
  FileCode, 
  ShieldCheck, 
  Terminal, 
  Calendar,
  FolderGit2,
  Cpu,
  Loader2,
  Check,
  X
} from 'lucide-react';

interface TaskDetailScreenProps {
  taskId: string;
  onBack: () => void;
  onRefreshAll?: () => void;
}

export const TaskDetailScreen: React.FC<TaskDetailScreenProps> = ({
  taskId,
  onBack,
  onRefreshAll,
}) => {
  const [task, setTask] = useState<Task | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sseStatus, setSseStatus] = useState<'connected' | 'reconnecting' | 'disconnected'>('reconnecting');
  const [actionLoading, setActionLoading] = useState(false);
  const [actionFeedback, setActionFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Approval modal state
  const [showApprovalModal, setShowApprovalModal] = useState<boolean>(false);
  const [pendingDecision, setPendingDecision] = useState<'approve' | 'reject' | null>(null);
  const [decisionComment, setDecisionComment] = useState('');

  // Initial fetch and SSE subscription
  useEffect(() => {
    let isMounted = true;

    async function load() {
      setIsLoading(true);
      setError(null);
      try {
        const result = await fetchTask(taskId);
        if (isMounted) {
          setTask(result.task);
        }
      } catch (err: any) {
        if (isMounted) {
          setError(err.message || 'Failed to fetch task');
        }
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    }

    load();

    const unsubscribe = subscribeToTaskEvents(taskId, {
      onInit: (data) => {
        if (isMounted && data.task) {
          setTask(data.task);
        }
      },
      onEvent: (event, updatedTask) => {
        if (!isMounted) return;
        if (updatedTask) {
          setTask(updatedTask);
        } else {
          setTask((prev) => {
            if (!prev) return null;
            // Prevent duplicate event
            if (prev.events.some((e) => e.id === event.id)) return prev;
            return {
              ...prev,
              events: [...prev.events, event],
            };
          });
        }
      },
      onStatusChange: (status) => {
        if (isMounted) setSseStatus(status);
      },
      onError: () => {
        if (isMounted) setSseStatus('reconnecting');
      },
    });

    return () => {
      isMounted = false;
      unsubscribe();
    };
  }, [taskId]);

  const handleCancel = async () => {
    if (!task) return;
    if (!confirm('Are you sure you want to cancel this task?')) return;

    setActionLoading(true);
    setActionFeedback(null);
    try {
      const res = await cancelTask(task.id);
      setTask(res.task);
      setActionFeedback({ type: 'success', text: 'Task cancelled.' });
      onRefreshAll?.();
    } catch (err: any) {
      setActionFeedback({ type: 'error', text: err.message || 'Failed to cancel task' });
    } finally {
      setActionLoading(false);
    }
  };

  const handleRetry = async () => {
    if (!task) return;
    setActionLoading(true);
    setActionFeedback(null);
    try {
      const res = await retryTask(task.id);
      setTask(res.task);
      setActionFeedback({ type: 'success', text: 'Task re-queued for execution.' });
      onRefreshAll?.();
    } catch (err: any) {
      setActionFeedback({ type: 'error', text: err.message || 'Failed to retry task' });
    } finally {
      setActionLoading(false);
    }
  };

  const openDecisionModal = (decision: 'approve' | 'reject') => {
    setPendingDecision(decision);
    setDecisionComment('');
    setShowApprovalModal(true);
  };

  const handleConfirmDecision = async () => {
    if (!task || !task.approval || !pendingDecision) return;

    setActionLoading(true);
    setActionFeedback(null);

    try {
      const res = await respondToApproval(task.id, {
        action: pendingDecision,
        hash: task.approval.hash,
        comment: decisionComment,
      });

      setTask(res.task);
      setShowApprovalModal(false);
      setActionFeedback({
        type: 'success',
        text: pendingDecision === 'approve' ? 'Proposed changes approved.' : 'Proposed changes rejected.',
      });
      onRefreshAll?.();
    } catch (err: any) {
      setShowApprovalModal(false);
      if (err instanceof ApiError && err.status === 409) {
        setActionFeedback({
          type: 'error',
          text: `Stale Approval: ${err.message}`,
        });
      } else {
        setActionFeedback({
          type: 'error',
          text: err.message || 'Failed to submit approval response',
        });
      }
    } finally {
      setActionLoading(false);
    }
  };

  if (isLoading) {
    return (
      <div className="py-12 flex flex-col items-center justify-center space-y-3">
        <Loader2 className="w-8 h-8 text-indigo-400 animate-spin" />
        <span className="text-xs text-zinc-400 font-mono">Loading task {taskId}...</span>
      </div>
    );
  }

  if (error || !task) {
    return (
      <div className="py-10 space-y-4 text-center">
        <div className="p-3 rounded-full bg-rose-500/10 text-rose-400 w-12 h-12 mx-auto flex items-center justify-center">
          <XCircle className="w-6 h-6" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-white">Could not load task</h3>
          <p className="text-xs text-zinc-400 mt-1">{error || 'Task not found'}</p>
        </div>
        <button
          onClick={onBack}
          className="px-4 py-2 rounded-xl bg-zinc-800 text-xs font-medium text-zinc-300 hover:bg-zinc-700"
        >
          Return to Dashboard
        </button>
      </div>
    );
  }

  const isAwaiting = task.status === 'awaiting_approval' && task.approval;

  return (
    <div className="space-y-4 pb-12">
      {/* Top Bar with back button and SSE status */}
      <div className="flex items-center justify-between gap-2">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-xs font-medium text-zinc-400 hover:text-white py-1 px-2 -ml-2 rounded-lg transition"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back</span>
        </button>

        {/* Live SSE indicator */}
        <div className="flex items-center gap-1.5 text-[11px] font-mono px-2 py-0.5 rounded-full bg-zinc-900 border border-zinc-800">
          <span
            className={`w-2 h-2 rounded-full ${
              sseStatus === 'connected'
                ? 'bg-emerald-500'
                : sseStatus === 'reconnecting'
                ? 'bg-amber-500 animate-pulse'
                : 'bg-zinc-600'
            }`}
          />
          <span className="text-zinc-400 capitalize">{sseStatus}</span>
        </div>
      </div>

      {/* Action feedback banner */}
      {actionFeedback && (
        <div
          className={`p-3 rounded-xl text-xs flex items-center justify-between gap-2 ${
            actionFeedback.type === 'success'
              ? 'bg-emerald-950/50 text-emerald-300 border border-emerald-500/30'
              : 'bg-rose-950/50 text-rose-300 border border-rose-500/30'
          }`}
        >
          <span>{actionFeedback.text}</span>
          <button
            onClick={() => setActionFeedback(null)}
            className="text-zinc-400 hover:text-white"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Task Header Card */}
      <div className="p-4 rounded-2xl bg-zinc-900 border border-zinc-800 shadow-sm space-y-3">
        <div className="flex items-start justify-between gap-3">
          <span className="text-xs font-mono font-semibold text-zinc-400 bg-zinc-950 px-2 py-1 rounded border border-zinc-800">
            {task.id}
          </span>
          <TaskStatusBadge status={task.status} size="md" />
        </div>

        <h2 className="text-sm font-semibold text-white leading-snug">
          {task.instruction}
        </h2>

        {/* Metadata pills */}
        <div className="pt-2 border-t border-zinc-800/80 flex flex-wrap gap-2 text-[11px] font-mono text-zinc-400">
          <span className="flex items-center gap-1 bg-zinc-950/60 px-2 py-1 rounded border border-zinc-800/50 truncate max-w-full">
            <FolderGit2 className="w-3 h-3 text-zinc-500 shrink-0" />
            <span className="truncate">{task.workspace}</span>
          </span>
          <span className="flex items-center gap-1 bg-zinc-950/60 px-2 py-1 rounded border border-zinc-800/50">
            <ShieldCheck className="w-3 h-3 text-zinc-500 shrink-0" />
            <span className="capitalize">{task.mode}</span>
          </span>
          <span className="flex items-center gap-1 bg-zinc-950/60 px-2 py-1 rounded border border-zinc-800/50">
            <Cpu className="w-3 h-3 text-zinc-500 shrink-0" />
            <span>{task.model}</span>
          </span>
          <span className="flex items-center gap-1 bg-zinc-950/60 px-2 py-1 rounded border border-zinc-800/50">
            <Calendar className="w-3 h-3 text-zinc-500 shrink-0" />
            <span>{new Date(task.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
          </span>
        </div>
      </div>

      {/* TWO-PHASE APPROVAL CALLOUT */}
      {isAwaiting && task.approval && (
        <div className="p-4 rounded-2xl bg-gradient-to-b from-purple-950/70 to-zinc-900 border-2 border-purple-500/50 shadow-md space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-purple-500/20 text-purple-300">
                <AlertTriangle className="w-4 h-4" />
              </div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-purple-300">
                Operator Approval Required
              </h3>
            </div>
            <span className="text-[10px] font-mono text-purple-300/70 truncate max-w-[120px]">
              {task.approval.hash.slice(0, 18)}...
            </span>
          </div>

          <p className="text-xs text-zinc-200">
            {task.approval.summary}
          </p>

          {/* Unified Diff Inspection */}
          <DiffViewer 
            diff={task.approval.diff} 
            files={task.approval.proposedChanges} 
          />

          {/* Explicit Approve and Reject Actions */}
          <div className="pt-2 flex items-center gap-3">
            <button
              onClick={() => openDecisionModal('reject')}
              disabled={actionLoading}
              className="flex-1 py-3 px-3 rounded-xl bg-rose-600/15 hover:bg-rose-600/25 border border-rose-500/40 text-rose-300 font-semibold text-xs flex items-center justify-center gap-1.5 transition active:scale-95 min-h-[44px]"
            >
              <X className="w-4 h-4 stroke-[2.5]" />
              <span>Reject Changes</span>
            </button>

            <button
              onClick={() => openDecisionModal('approve')}
              disabled={actionLoading}
              className="flex-1 py-3 px-3 rounded-xl bg-purple-600 hover:bg-purple-500 text-white font-semibold text-xs flex items-center justify-center gap-1.5 shadow-md shadow-purple-600/20 transition active:scale-95 min-h-[44px]"
            >
              <Check className="w-4 h-4 stroke-[2.5]" />
              <span>Approve & Apply</span>
            </button>
          </div>
        </div>
      )}

      {/* Running Execution Step Banner */}
      {task.status === 'running' && (
        <div className="p-3.5 rounded-2xl bg-blue-950/40 border border-blue-500/40 flex items-center gap-3">
          <Activity className="w-5 h-5 text-blue-400 animate-spin shrink-0" />
          <div className="min-w-0 flex-1">
            <span className="text-[10px] font-semibold text-blue-400 uppercase tracking-wider block">
              Executing Step {task.stepIndex || 1} of {task.totalSteps || 4}
            </span>
            <p className="text-xs text-white font-mono truncate mt-0.5">
              {task.currentStep || 'Working on codebase...'}
            </p>
          </div>
        </div>
      )}

      {/* Task Result Summary if Completed */}
      {task.status === 'completed' && task.result && (
        <div className="p-4 rounded-2xl bg-emerald-950/25 border border-emerald-500/30 space-y-3">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-emerald-300">
              Execution Result
            </h3>
          </div>

          <p className="text-xs text-zinc-200 leading-relaxed">
            {task.result.summary}
          </p>

          {task.result.testOutput && (
            <div className="p-3 rounded-xl bg-black/60 border border-zinc-800 font-mono text-[11px] text-zinc-300 overflow-x-auto whitespace-pre">
              {task.result.testOutput}
            </div>
          )}

          <div className="flex items-center justify-between text-[11px] font-mono text-zinc-400 pt-1 border-t border-emerald-900/40">
            <span>Branch: {task.result.branchName || 'main'}</span>
            <span>Duration: {task.result.durationMs ? `${(task.result.durationMs / 1000).toFixed(1)}s` : 'N/A'}</span>
          </div>
        </div>
      )}

      {/* Failure Error if Failed */}
      {task.status === 'failed' && (
        <div className="p-4 rounded-2xl bg-rose-950/30 border border-rose-500/30 space-y-2">
          <div className="flex items-center gap-2">
            <XCircle className="w-4 h-4 text-rose-400" />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-rose-300">
              Execution Error
            </h3>
          </div>
          <p className="text-xs text-rose-200/90 leading-relaxed">
            {task.error || task.currentStep || 'Task failed during execution.'}
          </p>
        </div>
      )}

      {/* Live Progress & Event Timeline */}
      <div className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400 px-1">
          Event History & Progress ({task.events.length})
        </h3>

        <div className="rounded-2xl bg-zinc-900 border border-zinc-800 p-3 space-y-3 divide-y divide-zinc-800/60 shadow-sm">
          {task.events.length === 0 ? (
            <p className="text-xs text-zinc-500 text-center py-4">No events logged yet.</p>
          ) : (
            task.events.map((evt, idx) => (
              <div key={evt.id || idx} className={idx > 0 ? 'pt-3' : ''}>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-start gap-2 min-w-0">
                    <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 mt-1.5 shrink-0" />
                    <div>
                      <p className="text-xs text-zinc-200 font-medium leading-tight">
                        {evt.message}
                      </p>
                      {evt.stepNumber && (
                        <span className="text-[10px] font-mono text-zinc-500">
                          Step {evt.stepNumber} of {evt.totalSteps || 4}
                        </span>
                      )}
                    </div>
                  </div>
                  <span className="text-[10px] font-mono text-zinc-500 shrink-0">
                    {new Date(evt.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Task Control Actions (Cancel or Retry) */}
      <div className="pt-2 flex items-center gap-3">
        {(task.status === 'queued' || task.status === 'running' || task.status === 'awaiting_approval') && (
          <button
            onClick={handleCancel}
            disabled={actionLoading}
            className="w-full py-3 px-4 rounded-xl border border-zinc-700 hover:bg-zinc-800 text-zinc-300 font-medium text-xs flex items-center justify-center gap-2 transition min-h-[44px]"
          >
            <Ban className="w-4 h-4 text-zinc-400" />
            <span>Cancel Task</span>
          </button>
        )}

        {(task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') && (
          <button
            onClick={handleRetry}
            disabled={actionLoading}
            className="w-full py-3 px-4 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-200 font-medium text-xs flex items-center justify-center gap-2 transition min-h-[44px]"
          >
            <RotateCcw className="w-4 h-4 text-indigo-400" />
            <span>Retry Task</span>
          </button>
        )}
      </div>

      {/* APPROVAL CONFIRMATION MODAL */}
      {showApprovalModal && pendingDecision && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/80 backdrop-blur-xs p-4 animate-in fade-in">
          <div className="w-full max-w-sm rounded-2xl bg-zinc-900 border border-zinc-800 p-5 shadow-2xl space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-zinc-800">
              <div className="flex items-center gap-2">
                {pendingDecision === 'approve' ? (
                  <div className="p-2 rounded-lg bg-purple-500/20 text-purple-300">
                    <Check className="w-5 h-5 stroke-[2.5]" />
                  </div>
                ) : (
                  <div className="p-2 rounded-lg bg-rose-500/20 text-rose-300">
                    <X className="w-5 h-5 stroke-[2.5]" />
                  </div>
                )}
                <div>
                  <h3 className="text-sm font-semibold text-white">
                    {pendingDecision === 'approve' ? 'Confirm Approval' : 'Confirm Rejection'}
                  </h3>
                  <p className="text-[11px] text-zinc-400">Two-phase security verification</p>
                </div>
              </div>
              <button
                onClick={() => setShowApprovalModal(false)}
                className="p-1 rounded-lg text-zinc-400 hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <p className="text-xs text-zinc-300 leading-relaxed">
              {pendingDecision === 'approve'
                ? 'Are you sure you want to approve this diff? The agent will apply the files to your repository and execute integration tests.'
                : 'Are you sure you want to reject this diff? The agent will abort execution and leave the working tree clean.'}
            </p>

            <div className="space-y-1">
              <label className="text-[11px] font-medium text-zinc-400 block">
                Operator Comment (Optional)
              </label>
              <input
                type="text"
                value={decisionComment}
                onChange={(e) => setDecisionComment(e.target.value)}
                placeholder={
                  pendingDecision === 'approve'
                    ? 'e.g., Looks solid, proceed to test'
                    : 'e.g., Need higher rate limit or different Redis key'
                }
                className="w-full rounded-xl bg-zinc-950 border border-zinc-800 px-3 py-2 text-xs text-white placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>

            <div className="pt-2 flex items-center gap-2">
              <button
                type="button"
                onClick={() => setShowApprovalModal(false)}
                className="flex-1 py-2.5 rounded-xl border border-zinc-800 text-zinc-300 text-xs font-medium hover:bg-zinc-800 transition min-h-[44px]"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmDecision}
                disabled={actionLoading}
                className={`flex-1 py-2.5 rounded-xl text-white text-xs font-semibold shadow-md transition active:scale-95 min-h-[44px] ${
                  pendingDecision === 'approve'
                    ? 'bg-purple-600 hover:bg-purple-500'
                    : 'bg-rose-600 hover:bg-rose-500'
                }`}
              >
                {actionLoading ? 'Submitting...' : pendingDecision === 'approve' ? 'Yes, Approve' : 'Yes, Reject'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
