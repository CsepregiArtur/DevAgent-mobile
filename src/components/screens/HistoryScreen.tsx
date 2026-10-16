import React, { useState } from 'react';
import type { Task, TaskStatus } from '../../types/agent';
import { TaskStatusBadge } from '../StatusBadge';
import { 
  Search, 
  Filter, 
  ChevronRight, 
  FolderGit2, 
  Calendar, 
  CheckCircle2, 
  XCircle,
  FileCode,
  Clock
} from 'lucide-react';

interface HistoryScreenProps {
  tasks: Task[];
  isLoading: boolean;
  onNavigateToTask: (taskId: string) => void;
  onRefresh: () => void;
}

export const HistoryScreen: React.FC<HistoryScreenProps> = ({
  tasks,
  isLoading,
  onNavigateToTask,
}) => {
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');

  const filterOptions = [
    { id: 'all', label: 'All' },
    { id: 'running', label: 'Running' },
    { id: 'awaiting_approval', label: 'Review' },
    { id: 'queued', label: 'Queued' },
    { id: 'completed', label: 'Done' },
    { id: 'failed', label: 'Failed' },
  ];

  const filteredTasks = tasks.filter((task) => {
    const matchesStatus = statusFilter === 'all' || task.status === statusFilter;
    const query = search.toLowerCase().trim();
    const matchesSearch =
      !query ||
      task.instruction.toLowerCase().includes(query) ||
      task.id.toLowerCase().includes(query) ||
      task.workspace.toLowerCase().includes(query);

    return matchesStatus && matchesSearch;
  });

  return (
    <div className="space-y-3 pb-12">
      {/* Header */}
      <div>
        <h2 className="text-base font-semibold text-white">Task History</h2>
        <p className="text-xs text-zinc-400">All agent executions and queue events</p>
      </div>

      {/* Search Input */}
      <div className="relative">
        <Search className="w-4 h-4 text-zinc-500 absolute left-3 top-1/2 -translate-y-1/2" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by instruction or task ID..."
          className="w-full pl-9 pr-3 py-2.5 rounded-xl bg-zinc-900 border border-zinc-800 text-xs text-white placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
      </div>

      {/* Filter Pills */}
      <div className="flex items-center gap-1.5 overflow-x-auto pb-1 no-scrollbar text-xs">
        {filterOptions.map((opt) => (
          <button
            key={opt.id}
            onClick={() => setStatusFilter(opt.id)}
            className={`px-3 py-1.5 rounded-lg font-medium whitespace-nowrap transition active:scale-95 min-h-[36px] flex items-center ${
              statusFilter === opt.id
                ? 'bg-indigo-600 text-white'
                : 'bg-zinc-900 text-zinc-400 hover:text-zinc-200 border border-zinc-800'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {/* Task List */}
      <div className="divide-y divide-zinc-800/80 rounded-2xl bg-zinc-900 border border-zinc-800 overflow-hidden shadow-sm">
        {filteredTasks.length === 0 ? (
          <div className="p-8 text-center text-xs text-zinc-500 space-y-1">
            <p className="font-medium text-zinc-400">No tasks match your filter.</p>
            <p>Try clearing your search query or selecting a different status.</p>
          </div>
        ) : (
          filteredTasks.map((task) => (
            <button
              key={task.id}
              onClick={() => onNavigateToTask(task.id)}
              className="w-full text-left p-3.5 hover:bg-zinc-800/40 transition flex items-center justify-between gap-3 group"
            >
              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="flex items-center gap-2">
                  <TaskStatusBadge status={task.status} size="sm" />
                  <span className="text-[11px] font-mono text-zinc-500">{task.id}</span>
                  <span className="text-[10px] text-zinc-500 ml-auto font-mono">
                    {new Date(task.createdAt).toLocaleDateString([], { month: 'short', day: 'numeric' })}
                  </span>
                </div>

                <p className="text-xs font-medium text-zinc-200 line-clamp-2 group-hover:text-white transition leading-snug">
                  {task.instruction}
                </p>

                {task.result?.summary && (
                  <p className="text-[11px] text-zinc-400 font-mono truncate">
                    ✓ {task.result.summary}
                  </p>
                )}

                <div className="flex items-center gap-2 text-[10px] font-mono text-zinc-500">
                  <span className="truncate max-w-[160px]">{task.workspace}</span>
                  <span>•</span>
                  <span>{task.events.length} events</span>
                </div>
              </div>

              <ChevronRight className="w-4 h-4 text-zinc-600 group-hover:text-zinc-400 shrink-0" />
            </button>
          ))
        )}
      </div>
    </div>
  );
};
