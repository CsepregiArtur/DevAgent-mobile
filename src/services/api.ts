import type {
  AgentInfo,
  AgentsResponse,
  ApprovalDecisionPayload,
  CreateTaskPayload,
  Diagnostics,
  FleetStatus,
  Task,
  TaskApproval,
  TaskEvent,
} from '../types/agent';

// ---------------------------------------------------------------------------
// Gateway configuration
//
// The PWA is served from the gateway itself (https://<host>/mobile), so the
// default base URL is derived from Vite's BASE_URL. Inside the native iOS shell
// (Capacitor) the origin is `capacitor://localhost`, therefore both the base URL
// and the shared token are configurable at runtime and stored on the device.
// ---------------------------------------------------------------------------
const STORAGE_BASE_URL = 'devagent.baseUrl';
const STORAGE_TOKEN = 'devagent.token';

export const DEFAULT_GATEWAY_URL = 'https://deepseek-api.cs-hub.xyz/mobile';

export function getGatewayBase(): string {
  const stored = localStorage.getItem(STORAGE_BASE_URL);
  if (stored) return stored.replace(/\/+$/, '');
  const fromBuild = import.meta.env.BASE_URL || '/';
  return fromBuild === '/' ? '' : fromBuild.replace(/\/+$/, '');
}

export function getGatewayToken(): string {
  return localStorage.getItem(STORAGE_TOKEN) || '';
}

export function saveGatewayConfig(baseUrl: string, token: string): void {
  if (baseUrl.trim()) {
    localStorage.setItem(STORAGE_BASE_URL, baseUrl.trim().replace(/\/+$/, ''));
  }
  if (token.trim()) {
    localStorage.setItem(STORAGE_TOKEN, token.trim());
  }
}

export function clearGatewayConfig(): void {
  localStorage.removeItem(STORAGE_BASE_URL);
  localStorage.removeItem(STORAGE_TOKEN);
}

export function apiUrl(path: string): string {
  return `${getGatewayBase()}${path}`;
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = getGatewayToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

/** EventSource cannot send headers, so the token travels in the query string. */
export function sseUrl(path: string): string {
  const token = getGatewayToken();
  const separator = path.includes('?') ? '&' : '?';
  return `${apiUrl(path)}${token ? `${separator}token=${encodeURIComponent(token)}` : ''}`;
}

export class ApiError extends Error {
  status: number;
  data: any;

  constructor(message: string, status: number, data?: any) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  const contentType = res.headers.get('content-type') || '';
  let data: any = null;

  if (contentType.includes('application/json')) {
    try {
      data = await res.json();
    } catch {
      data = null;
    }
  } else {
    data = await res.text();
  }

  if (!res.ok) {
    const errorMsg =
      data?.error ||
      data?.message ||
      `Request failed with status ${res.status}: ${res.statusText}`;
    throw new ApiError(errorMsg, res.status, data);
  }

  return data as T;
}

export async function fetchAgentStatus(): Promise<FleetStatus> {
  const res = await fetch(apiUrl('/api/agent/status'), {
    headers: authHeaders({ Accept: 'application/json' }),
  });
  return handleResponse(res);
}

/** The fleet: every agent the gateway knows, with liveness and capabilities. */
export async function fetchAgents(): Promise<AgentsResponse> {
  const res = await fetch(apiUrl('/api/agents'), {
    headers: authHeaders({ Accept: 'application/json' }),
  });
  return handleResponse(res);
}

/** Forget a decommissioned agent that is no longer connected. */
export async function removeAgent(agentId: string): Promise<{ removed: string }> {
  const res = await fetch(apiUrl(`/api/agents/${encodeURIComponent(agentId)}`), {
    method: 'DELETE',
    headers: authHeaders({ Accept: 'application/json' }),
  });
  return handleResponse(res);
}

export async function fetchTasks(params?: {
  status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<{ tasks: Task[]; total: number; offset: number; limit: number }> {
  const query = new URLSearchParams();
  if (params?.status && params.status !== 'all') query.set('status', params.status);
  if (params?.search) query.set('search', params.search);
  if (params?.limit) query.set('limit', String(params.limit));
  if (params?.offset) query.set('offset', String(params.offset));

  const url = apiUrl(`/api/tasks${query.toString() ? `?${query.toString()}` : ''}`);
  const res = await fetch(url, { headers: authHeaders({ Accept: 'application/json' }) });
  return handleResponse(res);
}

export async function fetchTask(id: string): Promise<{ task: Task }> {
  const res = await fetch(apiUrl(`/api/tasks/${id}`), {
    headers: authHeaders({ Accept: 'application/json' }),
  });
  return handleResponse(res);
}

export async function createTask(payload: CreateTaskPayload): Promise<{
  task: Task;
  message: string;
}> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 15000);

  try {
    const res = await fetch(apiUrl('/api/tasks'), {
      method: 'POST',
      headers: authHeaders({
        'Content-Type': 'application/json',
        Accept: 'application/json',
      }),
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    return await handleResponse(res);
  } catch (err: any) {
    if (err.name === 'AbortError') {
      throw new ApiError('Task creation timed out after 15s. Please check network connection.', 408);
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

export async function cancelTask(id: string): Promise<{ task: Task; message: string }> {
  const res = await fetch(apiUrl(`/api/tasks/${id}/cancel`), {
    method: 'POST',
    headers: authHeaders({ Accept: 'application/json' }),
  });
  return handleResponse(res);
}

export async function retryTask(id: string): Promise<{ task: Task; message: string }> {
  const res = await fetch(apiUrl(`/api/tasks/${id}/retry`), {
    method: 'POST',
    headers: authHeaders({ Accept: 'application/json' }),
  });
  return handleResponse(res);
}

export async function respondToApproval(
  taskId: string,
  payload: ApprovalDecisionPayload
): Promise<{ task: Task; approval: TaskApproval; message: string }> {
  const res = await fetch(apiUrl(`/api/tasks/${taskId}/approval`), {
    method: 'POST',
    headers: authHeaders({
      'Content-Type': 'application/json',
      Accept: 'application/json',
    }),
    body: JSON.stringify(payload),
  });
  return handleResponse(res);
}

export async function fetchDiagnostics(): Promise<Diagnostics> {
  const res = await fetch(apiUrl('/api/diagnostics'), {
    headers: authHeaders({ Accept: 'application/json' }),
  });
  return handleResponse(res);
}

export async function fetchWorkspaces(): Promise<{
  workspaces: string[];
  defaultWorkspace: string;
}> {
  const res = await fetch(apiUrl('/api/workspaces'), {
    headers: authHeaders({ Accept: 'application/json' }),
  });
  return handleResponse(res);
}

export async function toggleAgentSimulator(
  enabled?: boolean
): Promise<{ simulatorActive: boolean; agentStatus: string }> {
  const res = await fetch(apiUrl('/api/agent/simulate-toggle'), {
    method: 'POST',
    headers: authHeaders({
      'Content-Type': 'application/json',
      Accept: 'application/json',
    }),
    body: JSON.stringify({ enabled }),
  });
  return handleResponse(res);
}

// SSE Connection manager with automatic reconnection, heartbeat tracking, and deduplication
export function subscribeToTaskEvents(
  taskId: string,
  callbacks: {
    onEvent?: (event: TaskEvent, updatedTask?: Task) => void;
    onInit?: (data: { task: Task; agentInfo: AgentInfo }) => void;
    onError?: (error: Event) => void;
    onStatusChange?: (status: 'connected' | 'reconnecting' | 'disconnected') => void;
  }
) {
  let eventSource: EventSource | null = null;
  let isClosed = false;
  let reconnectTimer: any = null;
  const seenEventIds = new Set<string>();

  function connect() {
    if (isClosed) return;
    callbacks.onStatusChange?.('reconnecting');

    try {
      eventSource = new EventSource(sseUrl(`/api/tasks/${taskId}/events`));

      eventSource.onopen = () => {
        callbacks.onStatusChange?.('connected');
      };

      eventSource.addEventListener('init', (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          callbacks.onInit?.(data);
        } catch (err) {
          console.error('Failed to parse init SSE event', err);
        }
      });

      eventSource.onmessage = (e: MessageEvent) => {
        try {
          if (e.lastEventId && seenEventIds.has(e.lastEventId)) {
            return; // Deduplicate
          }
          if (e.lastEventId) {
            seenEventIds.add(e.lastEventId);
          }

          const parsed = JSON.parse(e.data);
          if (parsed.event && parsed.event.id) {
            if (seenEventIds.has(parsed.event.id)) {
              return; // Deduplicate
            }
            seenEventIds.add(parsed.event.id);
          }

          callbacks.onEvent?.(parsed.event, parsed.task);
        } catch (err) {
          console.error('Failed to parse SSE payload', err);
        }
      };

      eventSource.onerror = (e) => {
        callbacks.onStatusChange?.('reconnecting');
        callbacks.onError?.(e);
        if (eventSource) {
          eventSource.close();
          eventSource = null;
        }
        if (!isClosed) {
          reconnectTimer = setTimeout(connect, 3000);
        }
      };
    } catch (err) {
      callbacks.onStatusChange?.('disconnected');
      if (!isClosed) {
        reconnectTimer = setTimeout(connect, 4000);
      }
    }
  }

  connect();

  return () => {
    isClosed = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  };
}
