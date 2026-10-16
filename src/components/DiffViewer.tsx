import React, { useState } from 'react';
import type { ChangedFile } from '../types/agent';
import { FileCode, FilePlus, FileEdit, FileX, Copy, Check, ChevronDown, ChevronRight } from 'lucide-react';

interface DiffViewerProps {
  diff: string;
  files?: ChangedFile[];
}

export const DiffViewer: React.FC<DiffViewerProps> = ({ diff, files = [] }) => {
  const [copied, setCopied] = useState(false);
  const [isExpanded, setIsExpanded] = useState(true);

  const handleCopy = () => {
    navigator.clipboard.writeText(diff);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const getFileIcon = (status: ChangedFile['status']) => {
    switch (status) {
      case 'created':
        return <FilePlus className="w-4 h-4 text-emerald-400 shrink-0" />;
      case 'deleted':
        return <FileX className="w-4 h-4 text-rose-400 shrink-0" />;
      case 'modified':
      default:
        return <FileEdit className="w-4 h-4 text-sky-400 shrink-0" />;
    }
  };

  const lines = diff.split('\n');

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/90 overflow-hidden shadow-sm">
      {/* File summary bar */}
      <div className="p-3 bg-zinc-900 border-b border-zinc-800 flex items-center justify-between gap-2">
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex items-center gap-2 text-left flex-1 min-w-0"
        >
          {isExpanded ? (
            <ChevronDown className="w-4 h-4 text-zinc-400 shrink-0" />
          ) : (
            <ChevronRight className="w-4 h-4 text-zinc-400 shrink-0" />
          )}
          <span className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
            Unified Diff ({files.length} {files.length === 1 ? 'file' : 'files'})
          </span>
        </button>

        <button
          onClick={handleCopy}
          className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 border border-zinc-700 transition"
          title="Copy diff to clipboard"
        >
          {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
          <span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>

      {/* Changed files list */}
      {files.length > 0 && (
        <div className="px-3 py-2 bg-zinc-950/60 border-b border-zinc-800/80 divide-y divide-zinc-800/40 text-xs">
          {files.map((file, idx) => (
            <div key={idx} className="py-1.5 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0 flex-1">
                {getFileIcon(file.status)}
                <span className="font-mono text-zinc-200 truncate">{file.path}</span>
              </div>
              <div className="flex items-center gap-1.5 font-mono text-[11px] shrink-0">
                {file.additions > 0 && (
                  <span className="text-emerald-400">+{file.additions}</span>
                )}
                {file.deletions > 0 && (
                  <span className="text-rose-400">-{file.deletions}</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Diff content lines */}
      {isExpanded && (
        <div className="font-mono text-xs overflow-x-auto max-h-[460px] overflow-y-auto divide-y divide-zinc-900/40 select-text p-1">
          {lines.map((line, idx) => {
            const isAdd = line.startsWith('+') && !line.startsWith('+++');
            const isDel = line.startsWith('-') && !line.startsWith('---');
            const isHunk = line.startsWith('@@');
            const isMeta = line.startsWith('diff ') || line.startsWith('index ') || line.startsWith('---') || line.startsWith('+++');

            let lineClass = 'text-zinc-300 hover:bg-zinc-800/50';
            if (isAdd) lineClass = 'bg-emerald-950/40 text-emerald-200';
            else if (isDel) lineClass = 'bg-rose-950/40 text-rose-200';
            else if (isHunk) lineClass = 'bg-indigo-950/30 text-indigo-300 font-semibold';
            else if (isMeta) lineClass = 'text-zinc-500 font-medium';

            return (
              <div
                key={idx}
                className={`px-3 py-0.5 flex whitespace-pre leading-relaxed min-w-fit ${lineClass}`}
              >
                <span className="w-8 shrink-0 text-zinc-600 select-none text-[10px] text-right pr-2">
                  {idx + 1}
                </span>
                <span className="select-text">{line || ' '}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
