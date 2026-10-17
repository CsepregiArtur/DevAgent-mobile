export type AgentStatus = 'online' | 'offline' | 'busy' | 'reconnecting';

export interface AgentInfo {
  agentId: string;
  hostname: string;
  workspace: string;
  status: AgentStatus;
  lastHeartbeat: string;
  version: string;
  os: string;
  activeTaskId: string | null;
  batteryLevel?: number;
  isSimulated?: boolean;
  /** true when the gateway holds a live WebSocket for this agent */
  online?: boolean;
  /** seconds since the last heartbeat (null when never seen) */
  secondsSinceHeartbeat?: number | null;
  capabilities?: string[];
}

/** Fleet view: the MacBook is not the only possible worker. */
export interface AgentsResponse {
  agents: AgentInfo[];
  onlineAgents: number;
  totalAgents: number;
  primaryAgentId?: string | null;
}

export interface FleetStatus {
  connected: boolean;
  status: AgentStatus;
  info: AgentInfo | null;
  secondsSinceHeartbeat: number | null;
  isSimulated: boolean;
  agents?: AgentInfo[];
  onlineAgents?: number;
  totalAgents?: number;
  /** false when the app could not authenticate against the gateway at all */
  gatewayOnline?: boolean;
}

export type TaskStatus = 
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'completed'
  | 'failed'
  | 'cancelled';

export type TaskMode = 'autonomous' | 'supervised' | 'dry-run';

export interface ChangedFile {
  path: string;
  status: 'modified' | 'created' | 'deleted';
  additions: number;
  deletions: number;
}

export type ApprovalState = 'pending' | 'approved' | 'rejected' | 'expired';

export interface TaskApproval {
  id: string;
  taskId: string;
  hash: string;
  summary: string;
  proposedChanges: ChangedFile[];
  diff: string;
  state: ApprovalState;
  createdAt: string;
  expiresAt?: string;
  respondedAt?: string;
  responseComment?: string;
  decision?: 'approve' | 'reject';
}

export type TaskEventType = 
  | 'status_change'
  | 'step'
  | 'command'
  | 'diff_ready'
  | 'test_run'
  | 'approval_required'
  | 'approval_resolved'
  | 'log'
  | 'error';

export interface TaskEvent {
  id: string;
  taskId: string;
  timestamp: string;
  type: TaskEventType;
  message: string;
  details?: Record<string, any>;
  stepNumber?: number;
  totalSteps?: number;
}

export interface TaskResult {
  summary: string;
  filesModified: string[];
  testsPassed: boolean;
  testOutput?: string;
  errorMessage?: string;
  durationMs?: number;
  branchName?: string;
}

export interface Task {
  id: string;
  instruction: string;
  workspace: string;
  mode: TaskMode;
  model: string;
  status: TaskStatus;
  currentStep?: string;
  stepIndex?: number;
  totalSteps?: number;
  createdAt: string;
  startedAt?: string;
  completedAt?: string;
  approval?: TaskApproval;
  events: TaskEvent[];
  result?: TaskResult;
  error?: string;
}

export interface CreateTaskPayload {
  instruction: string;
  workspace?: string;
  mode?: TaskMode;
  model?: string;
  /** pin the task to one agent (empty/undefined = first capable idle agent) */
  agentId?: string;
  /** only agents advertising these capabilities may pick the task up */
  requiredCapabilities?: string[];
  /** AI provider for this task (undefined = gateway default) */
  providerId?: string;
}

/** AI provider configured on the gateway (credentials never leave masked). */
export interface ProviderInfo {
  id: string;
  name: string;
  kind: string;
  baseUrl: string;
  models: string[];
  defaultModel: string | null;
  enabled: boolean;
  isDefault: boolean;
  hasApiKey: boolean;
  apiKeyHint?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface ApprovalDecisionPayload {
  action: 'approve' | 'reject';
  hash: string;
  comment?: string;
}

export interface Diagnostics {
  gateway: {
    status: 'online' | 'degraded';
    uptimeSeconds: number;
    timestamp: string;
    version: string;
    environment: string;
  };
  agent: {
    connected: boolean;
    status: AgentStatus;
    info: AgentInfo | null;
    secondsSinceHeartbeat: number | null;
    isSimulated: boolean;
  };
  aiProvider: {
    provider: string;
    endpointConfigured: boolean;
    defaultModel: string;
    availableModels: string[];
    status: 'ready' | 'degraded';
    endpoint?: string | null;
    selection?: 'auto' | 'manual' | string;
    providers?: ProviderInfo[];
  };
  queue: {
    total: number;
    queued: number;
    running: number;
    awaitingApproval: number;
    completed: number;
    failed: number;
    cancelled: number;
  };
  workspaces: string[];
}
