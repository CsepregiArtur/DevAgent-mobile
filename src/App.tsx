import React, { useEffect, useState, useCallback } from 'react';
import type { AgentInfo, Diagnostics, FleetStatus, Task } from './types/agent';
import { 
  fetchAgentStatus, 
  fetchTasks, 
  fetchDiagnostics, 
  fetchWorkspaces,
  clearSession,
  fetchMe,
  getGatewayToken,
  logout,
  setSessionToken,
  sseUrl,
} from './services/api';
import { Header } from './components/Header';
import { BottomNav, type TabType } from './components/BottomNav';
import { OfflineBanner } from './components/OfflineBanner';
import { DashboardScreen } from './components/screens/DashboardScreen';
import { NewTaskScreen } from './components/screens/NewTaskScreen';
import { TaskDetailScreen } from './components/screens/TaskDetailScreen';
import { ApprovalsScreen } from './components/screens/ApprovalsScreen';
import { HistoryScreen } from './components/screens/HistoryScreen';
import { SettingsScreen } from './components/screens/SettingsScreen';
import { LoginScreen } from './components/screens/LoginScreen';
import { Loader2, AlertCircle, RefreshCw } from 'lucide-react';

/** Used when the gateway cannot be reached: no agents, nothing running. */
const EMPTY_FLEET: FleetStatus = {
  connected: false,
  status: 'offline',
  info: null,
  secondsSinceHeartbeat: null,
  isSimulated: false,
  agents: [],
  onlineAgents: 0,
  totalAgents: 0,
  gatewayOnline: false,
};

export default function App() {
  const [sessionToken, setSessionTokenState] = useState<string>(getGatewayToken());
  const [authNotice, setAuthNotice] = useState<string | null>(null);
  const [canManageTokens, setCanManageTokens] = useState<boolean>(false);
  const [activeTab, setActiveTab] = useState<TabType>('home');
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);

  const [agentInfo, setAgentInfo] = useState<AgentInfo | null>(null);
  const [agentConnected, setAgentConnected] = useState<boolean>(true);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [gatewayReachable, setGatewayReachable] = useState<boolean>(false);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [workspaces, setWorkspaces] = useState<string[]>(['~/dev/mobile-agent-platform']);
  const [defaultWorkspace, setDefaultWorkspace] = useState<string>('~/dev/mobile-agent-platform');

  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const loadAllData = useCallback(async (isSilent = false) => {
    if (!isSilent) setIsRefreshing(true);
    setError(null);

    try {
      const me = await fetchMe().catch((err: any) => {
        if (err?.status === 401) {
          clearSession();
          setSessionTokenState('');
        }
        return null;
      });
      if (me) setCanManageTokens(me.canManageTokens);

      // Agent-scoped keys can run a worker, but not drive the app.
      if (me && !me.canUseTasks) {
        clearSession();
        setSessionTokenState('');
        setAuthNotice(
          `The token you entered is a '${me.auth.kind}' key (scopes: ${me.auth.scopes.join(
            ', '
          )}). Sign in with a 'pwa' or 'admin' key — generate one in Settings → API keys, ` +
            'or use the bootstrap DEVAGENT_TOKEN.'
        );
        return;
      }

      const [agentRes, tasksRes, diagRes, wsRes] = await Promise.all([
        fetchAgentStatus().catch(() => EMPTY_FLEET),
        fetchTasks().catch(() => ({ tasks: [], total: 0, offset: 0, limit: 50 })),
        fetchDiagnostics().catch(() => null),
        fetchWorkspaces().catch(() => ({
          workspaces: ['~/dev/mobile-agent-platform'],
          defaultWorkspace: '~/dev/mobile-agent-platform',
        })),
      ]);

      setAgentConnected(agentRes.connected);
      if (agentRes.info) setAgentInfo(agentRes.info);
      setAgents(agentRes.agents || []);
      setGatewayReachable(agentRes.gatewayOnline === true);
      setTasks(tasksRes.tasks || []);
      if (diagRes) setDiagnostics(diagRes);
      if (wsRes.workspaces) setWorkspaces(wsRes.workspaces);
      if (wsRes.defaultWorkspace) setDefaultWorkspace(wsRes.defaultWorkspace);
    } catch (err: any) {
      console.error('Error fetching platform data', err);
      if (!isSilent) {
        setError(err.message || 'Could not connect to gateway server');
      }
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (!sessionToken) return;
    loadAllData();

    // Setup global SSE event listener for instant reactivity across devices
    let eventSource: EventSource | null = null;
    let reconnectTimeout: any = null;
    let isDisposed = false;

    function connectGlobalSSE() {
      if (isDisposed) return;
      try {
        eventSource = new EventSource(sseUrl('/api/events'));

        eventSource.addEventListener('message', (e) => {
          try {
            const data = JSON.parse(e.data);
            if (data.task) {
              setTasks((prev) => {
                const idx = prev.findIndex((t) => t.id === data.task.id);
                if (idx >= 0) {
                  const updated = [...prev];
                  updated[idx] = data.task;
                  return updated;
                }
                return [data.task, ...prev];
              });
            }
          } catch (err) {
            console.error('Failed to parse SSE payload', err);
          }
        });

        eventSource.addEventListener('agent_status', (e: any) => {
          try {
            const data = JSON.parse(e.data);
            if (data.agentInfo) {
              setAgentInfo(data.agentInfo);
            }
            if (Array.isArray(data.agents)) {
              setAgents(data.agents);
              setGatewayReachable(true);
              setAgentConnected(data.agents.some((agent: AgentInfo) => agent.online));
            } else {
              setAgentConnected(data.agentInfo?.status !== 'offline');
            }
          } catch (err) {
            console.error('Failed to parse agent_status SSE', err);
          }
        });

        eventSource.onerror = () => {
          if (eventSource) {
            eventSource.close();
            eventSource = null;
          }
          if (!isDisposed) {
            reconnectTimeout = setTimeout(connectGlobalSSE, 4000);
          }
        };
      } catch (err) {
        if (!isDisposed) {
          reconnectTimeout = setTimeout(connectGlobalSSE, 5000);
        }
      }
    }

    connectGlobalSSE();

    // Fallback poll every 25s
    const pollInterval = setInterval(() => {
      loadAllData(true);
    }, 25000);

    return () => {
      isDisposed = true;
      if (eventSource) eventSource.close();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      clearInterval(pollInterval);
    };
  }, [loadAllData]);

  const handleTabChange = (tab: TabType) => {
    setSelectedTaskId(null);
    setActiveTab(tab);
  };

  const handleNavigateToTask = (taskId: string) => {
    setSelectedTaskId(taskId);
  };

  const handleTaskCreated = (taskId: string) => {
    loadAllData(true);
    setSelectedTaskId(taskId);
  };

  const pendingApprovalsCount = tasks.filter(
    (t) => t.status === 'awaiting_approval' && t.approval?.state === 'pending'
  ).length;

  const handleLogout = async () => {
    try {
      await logout();
    } catch {
      /* the token may already be invalid; local cleanup is what matters */
    }
    clearSession();
    setSessionTokenState('');
    setAgents([]);
    setAgentInfo(null);
    setAgentConnected(false);
    setTasks([]);
    setDiagnostics(null);
    setCanManageTokens(false);
  };

  if (!sessionToken) {
    return (
      <LoginScreen
        notice={authNotice}
        onAuthenticated={() => {
          setAuthNotice(null);
          setSessionTokenState(getGatewayToken());
          loadAllData();
        }}
      />
    );
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 flex flex-col selection:bg-indigo-500 selection:text-white">
      {/* Offline banner */}
      <OfflineBanner />

      {/* Top Header */}
      <Header
        agentStatus={agentInfo?.status || (agentConnected ? 'online' : 'offline')}
        isSimulated={agentInfo?.isSimulated}
        agents={agents}
        onRefresh={() => loadAllData(false)}
        isRefreshing={isRefreshing}
      />

      {/* Main Content Area */}
      <main className="flex-1 w-full max-w-xl mx-auto px-3.5 pt-3 pb-[calc(5rem+env(safe-area-inset-bottom,0px))]">
        {isLoading ? (
          <div className="py-20 flex flex-col items-center justify-center space-y-3">
            <Loader2 className="w-8 h-8 text-indigo-500 animate-spin" />
            <p className="text-xs text-zinc-400 font-mono">Connecting to agent gateway...</p>
          </div>
        ) : error ? (
          <div className="py-12 px-4 rounded-2xl bg-zinc-900 border border-zinc-800 text-center space-y-3">
            <div className="p-3 rounded-full bg-rose-500/10 text-rose-400 w-12 h-12 mx-auto flex items-center justify-center">
              <AlertCircle className="w-6 h-6" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-white">Gateway Connection Error</h3>
              <p className="text-xs text-zinc-400 mt-1 max-w-xs mx-auto">{error}</p>
            </div>
            <button
              onClick={() => loadAllData(false)}
              className="px-4 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs font-medium text-white inline-flex items-center gap-1.5 transition"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              <span>Retry Connection</span>
            </button>
          </div>
        ) : selectedTaskId ? (
          <TaskDetailScreen
            taskId={selectedTaskId}
            onBack={() => setSelectedTaskId(null)}
            onRefreshAll={() => loadAllData(true)}
          />
        ) : (
          <>
            {activeTab === 'home' && (
              <DashboardScreen
                agentInfo={agentInfo}
                agentConnected={agentConnected}
                agents={agents}
                gatewayReachable={gatewayReachable}
                tasks={tasks}
                diagnostics={diagnostics}
                onNavigateToTask={handleNavigateToTask}
                onNavigateToNewTask={() => setActiveTab('new')}
                onNavigateToApprovals={() => setActiveTab('approvals')}
                onNavigateToTasks={() => setActiveTab('tasks')}
              />
            )}

            {activeTab === 'tasks' && (
              <HistoryScreen
                tasks={tasks}
                isLoading={isLoading}
                onNavigateToTask={handleNavigateToTask}
                onRefresh={() => loadAllData(false)}
              />
            )}

            {activeTab === 'new' && (
              <NewTaskScreen
                workspaces={workspaces}
                defaultWorkspace={defaultWorkspace}
                agentConnected={agentConnected}
                agents={agents}
                onTaskCreated={handleTaskCreated}
                onCancel={() => setActiveTab('home')}
              />
            )}

            {activeTab === 'approvals' && (
              <ApprovalsScreen
                tasks={tasks}
                onRefresh={() => loadAllData(true)}
                onNavigateToTask={handleNavigateToTask}
              />
            )}

            {activeTab === 'settings' && (
              <SettingsScreen
                diagnostics={diagnostics}
                agentInfo={agentInfo}
                agents={agents}
                gatewayReachable={gatewayReachable}
                canManageTokens={canManageTokens}
                onLogout={handleLogout}
                onRefresh={() => loadAllData(false)}
              />
            )}
          </>
        )}
      </main>

      {/* Persistent Mobile Bottom Navigation */}
      <BottomNav
        activeTab={activeTab}
        onChangeTab={handleTabChange}
        pendingApprovalsCount={pendingApprovalsCount}
      />
    </div>
  );
}
