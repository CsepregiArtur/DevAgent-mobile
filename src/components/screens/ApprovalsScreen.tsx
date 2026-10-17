import React, { useState } from 'react';
import type { Task } from '../../types/agent';
import { respondToApproval, ApiError } from '../../services/api';
import { DiffViewer } from '../DiffViewer';
import { 
  ShieldCheck, 
  AlertTriangle, 
  Check, 
  X, 
  FolderGit2, 
  Hash, 
  ArrowRight,
  Clock,
  CheckCircle2,
  AlertCircle
} from 'lucide-react';

interface ApprovalsScreenProps {
  tasks: Task[];
  onRefresh: () => void;
  onNavigateToTask: (taskId: string) => void;
}

export const ApprovalsScreen: React.FC<ApprovalsScreenProps> = ({
  tasks,
  onRefresh,
  onNavigateToTask,
}) => {
  const pendingApprovals = tasks.filter(
    (t) => t.status === 'awaiting_approval' && t.approval && t.approval.state === 'pending'
  );

  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [decision, setDecision] = useState<'approve' | 'reject' | null>(null);
  const [comment, setComment] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const handleOpenConfirm = (task: Task, action: 'approve' | 'reject') => {
    setSelectedTask(task);
    setDecision(action);
    setComment('');
  };

  const handleConfirmDecision = async () => {
    if (!selectedTask || !selectedTask.approval || !decision) return;

    setIsSubmitting(true);
    setFeedback(null);

    try {
      await respondToApproval(selectedTask.id, {
        action: decision,
        hash: selectedTask.approval.hash,
        comment,
      });

      setFeedback({
        type: 'success',
        message: decision === 'approve' ? 'Changes approved and resumed!' : 'Changes rejected.',
      });
      setSelectedTask(null);
      setDecision(null);
      onRefresh();
    } catch (err: any) {
      if (err instanceof ApiError && err.status === 409) {
        setFeedback({
          type: 'error',
          message: `Stale Approval: ${err.message}`,
        });
      } else {
        setFeedback({
          type: 'error',
          message: err.message || 'Approval action failed.',
        });
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="space-y-4 pb-12">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-white flex items-center gap-2">
            <span>Pending Approvals</span>
            {pendingApprovals.length > 0 && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-purple-600/30 text-purple-300 font-mono border border-purple-500/30">
                {pendingApprovals.length}
              </span>
            )}
          </h2>
          <p className="text-xs text-zinc-400">Two-phase security gate for code modifications</p>
        </div>
      </div>

      {feedback && (
        <div
          className={`p-3 rounded-xl text-xs flex items-center justify-between gap-2 ${
            feedback.type === 'success'
              ? 'bg-emerald-950/60 text-emerald-300 border border-emerald-500/30'
              : 'bg-rose-950/60 text-rose-300 border border-rose-500/30'
          }`}
        >
          <div className="flex items-center gap-2">
            {feedback.type === 'success' ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
            ) : (
              <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
            )}
            <span>{feedback.message}</span>
          </div>
          <button onClick={() => setFeedback(null)} className="text-zinc-400 hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {pendingApprovals.length === 0 ? (
        <div className="py-12 px-4 rounded-2xl bg-zinc-900 border border-zinc-800 text-center space-y-3">
          <div className="w-12 h-12 rounded-full bg-zinc-800 text-zinc-500 mx-auto flex items-center justify-center">
            <ShieldCheck className="w-6 h-6 text-zinc-400" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-white">All Caught Up</h3>
            <p className="text-xs text-zinc-400 max-w-xs mx-auto mt-1">
              There are no pending code changes awaiting your review.
            </p>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          {pendingApprovals.map((task) => {
            const approval = task.approval!;
            return (
              <div
                key={task.id}
                className="p-4 rounded-2xl bg-zinc-900 border-2 border-purple-500/40 shadow-sm space-y-3"
              >
                {/* Title & Hash */}
                <div className="flex items-start justify-between gap-2">
                  <div className="space-y-1">
                    <span className="text-[10px] font-mono text-purple-300 bg-purple-950/80 px-2 py-0.5 rounded border border-purple-800/60">
                      {task.id}
                    </span>
                    <h3 className="text-sm font-semibold text-white leading-snug">
                      {task.instruction}
                    </h3>
                  </div>
                  <button
                    onClick={() => onNavigateToTask(task.id)}
                    className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition"
                    title="View Full Task Details"
                  >
                    <ArrowRight className="w-4 h-4" />
                  </button>
                </div>

                {/* Workspace and Hash */}
                <div className="flex flex-wrap items-center gap-2 text-[10px] font-mono text-zinc-400">
                  <span className="flex items-center gap-1 bg-zinc-950 px-2 py-0.5 rounded border border-zinc-800">
                    <FolderGit2 className="w-3 h-3 text-zinc-500" />
                    <span>{task.workspace}</span>
                  </span>
                  <span className="flex items-center gap-1 bg-zinc-950 px-2 py-0.5 rounded border border-zinc-800">
                    <Hash className="w-3 h-3 text-zinc-500" />
                    <span className="truncate max-w-[140px]">{approval.hash}</span>
                  </span>
                </div>

                {/* Summary */}
                <p className="text-xs text-zinc-300 leading-relaxed bg-zinc-950/40 p-2.5 rounded-xl border border-zinc-800/50">
                  {approval.summary}
                </p>

                {/* Diff Viewer (a command prompt shows the exact command) */}
                {approval.gate === 'command' ? (
                  <div className="space-y-1.5">
                    <span className="text-[10px] font-mono text-amber-300 bg-amber-950/60 px-2 py-0.5 rounded border border-amber-800/60">
                      command approval
                    </span>
                    <pre className="text-[11px] font-mono whitespace-pre-wrap break-words bg-zinc-950 border border-zinc-800 rounded-xl p-3 text-amber-200">
                      {approval.diff.replace(/^#.*\n?/gm, '').trim()}
                    </pre>
                  </div>
                ) : (
                  <DiffViewer diff={approval.diff} files={approval.proposedChanges} />
                )}

                {/* Explicit Approve and Reject buttons */}
                <div className="pt-2 grid grid-cols-2 gap-3">
                  <button
                    onClick={() => handleOpenConfirm(task, 'reject')}
                    className="py-3 px-3 rounded-xl bg-rose-600/15 hover:bg-rose-600/25 border border-rose-500/40 text-rose-300 font-semibold text-xs flex items-center justify-center gap-1.5 transition active:scale-95 min-h-[44px]"
                  >
                    <X className="w-4 h-4 stroke-[2.5]" />
                    <span>Reject</span>
                  </button>

                  <button
                    onClick={() => handleOpenConfirm(task, 'approve')}
                    className="py-3 px-3 rounded-xl bg-purple-600 hover:bg-purple-500 text-white font-semibold text-xs flex items-center justify-center gap-1.5 shadow-md shadow-purple-600/20 transition active:scale-95 min-h-[44px]"
                  >
                    <Check className="w-4 h-4 stroke-[2.5]" />
                    <span>Approve</span>
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Confirmation Modal */}
      {selectedTask && decision && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/80 backdrop-blur-xs p-4 animate-in fade-in">
          <div className="w-full max-w-sm rounded-2xl bg-zinc-900 border border-zinc-800 p-5 shadow-2xl space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-zinc-800">
              <div className="flex items-center gap-2">
                {decision === 'approve' ? (
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
                    {decision === 'approve' ? 'Confirm Approval' : 'Confirm Rejection'}
                  </h3>
                  <span className="text-[10px] font-mono text-zinc-400">{selectedTask.id}</span>
                </div>
              </div>
              <button
                onClick={() => {
                  setSelectedTask(null);
                  setDecision(null);
                }}
                className="p-1 rounded-lg text-zinc-400 hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <p className="text-xs text-zinc-300 leading-relaxed">
              {decision === 'approve'
                ? 'Confirming will instruct the MacBook agent to apply this diff to the repository, checkout the feature branch, and run test suites.'
                : 'Confirming will reject this proposal and discard changes.'}
            </p>

            <div className="space-y-1">
              <label className="text-[11px] font-medium text-zinc-400 block">
                Comment for Agent Log (Optional)
              </label>
              <input
                type="text"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder={decision === 'approve' ? 'e.g., Looks good' : 'e.g., Please adjust logic'}
                className="w-full rounded-xl bg-zinc-950 border border-zinc-800 px-3 py-2 text-xs text-white placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>

            <div className="pt-2 flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  setSelectedTask(null);
                  setDecision(null);
                }}
                disabled={isSubmitting}
                className="flex-1 py-2.5 rounded-xl border border-zinc-800 text-zinc-300 text-xs font-medium hover:bg-zinc-800 transition min-h-[44px]"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmDecision}
                disabled={isSubmitting}
                className={`flex-1 py-2.5 rounded-xl text-white text-xs font-semibold shadow-md transition active:scale-95 min-h-[44px] ${
                  decision === 'approve'
                    ? 'bg-purple-600 hover:bg-purple-500'
                    : 'bg-rose-600 hover:bg-rose-500'
                }`}
              >
                {isSubmitting ? 'Submitting...' : decision === 'approve' ? 'Approve' : 'Reject'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
