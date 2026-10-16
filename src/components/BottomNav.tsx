import React from 'react';
import { Home, ListTodo, Plus, ShieldCheck, Settings } from 'lucide-react';

export type TabType = 'home' | 'tasks' | 'new' | 'approvals' | 'settings';

interface BottomNavProps {
  activeTab: TabType;
  onChangeTab: (tab: TabType) => void;
  pendingApprovalsCount?: number;
}

export const BottomNav: React.FC<BottomNavProps> = ({
  activeTab,
  onChangeTab,
  pendingApprovalsCount = 0,
}) => {
  const tabs = [
    { id: 'home' as TabType, label: 'Home', icon: Home },
    { id: 'tasks' as TabType, label: 'Tasks', icon: ListTodo },
    { id: 'new' as TabType, label: 'New Task', icon: Plus, isPrimary: true },
    {
      id: 'approvals' as TabType,
      label: 'Approvals',
      icon: ShieldCheck,
      badge: pendingApprovalsCount > 0 ? pendingApprovalsCount : undefined,
    },
    { id: 'settings' as TabType, label: 'Settings', icon: Settings },
  ];

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-40 bg-zinc-950/95 backdrop-blur-lg border-t border-zinc-800/80 pb-[calc(0.4rem+env(safe-area-inset-bottom,0px))] pt-1 px-2">
      <div className="max-w-xl mx-auto flex items-center justify-around">
        {tabs.map((tab) => {
          const isActive = activeTab === tab.id;
          const Icon = tab.icon;

          if (tab.isPrimary) {
            return (
              <button
                key={tab.id}
                onClick={() => onChangeTab(tab.id)}
                className="group relative flex flex-col items-center justify-center -mt-4 min-w-[56px] min-h-[56px] focus:outline-none"
                aria-label="Create New Task"
              >
                <div
                  className={`w-12 h-12 rounded-full flex items-center justify-center shadow-lg transition active:scale-95 ${
                    isActive
                      ? 'bg-indigo-500 text-white shadow-indigo-500/25 ring-2 ring-indigo-400 ring-offset-2 ring-offset-zinc-950'
                      : 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-indigo-600/30'
                  }`}
                >
                  <Plus className="w-6 h-6 stroke-[2.5]" />
                </div>
                <span className="text-[10px] font-medium text-zinc-300 mt-1">New Task</span>
              </button>
            );
          }

          return (
            <button
              key={tab.id}
              onClick={() => onChangeTab(tab.id)}
              className={`relative flex flex-col items-center justify-center py-1.5 px-3 rounded-xl transition min-w-[56px] min-h-[48px] active:scale-95 ${
                isActive ? 'text-indigo-400 font-semibold' : 'text-zinc-400 hover:text-zinc-200'
              }`}
              aria-label={tab.label}
            >
              <div className="relative flex items-center justify-center">
                <Icon className={`w-5 h-5 transition ${isActive ? 'stroke-[2.2]' : 'stroke-[1.8]'}`} />
                {typeof tab.badge === 'number' && (
                  <span className="absolute -top-1.5 -right-2.5 flex items-center justify-center min-w-[18px] h-[18px] px-1 text-[10px] font-bold text-white bg-purple-600 rounded-full ring-2 ring-zinc-950 animate-pulse">
                    {tab.badge}
                  </span>
                )}
              </div>
              <span className="text-[10px] mt-1 tracking-tight truncate max-w-[64px]">
                {tab.label}
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
};
