import express, { Request, Response } from 'express';
import { createServer as createViteServer } from 'vite';
import path from 'path';
import { fileURLToPath } from 'url';
import crypto from 'crypto';
import type { 
  AgentInfo, 
  Diagnostics, 
  Task, 
  TaskApproval, 
  TaskEvent, 
  TaskStatus, 
  CreateTaskPayload, 
  ApprovalDecisionPayload 
} from './src/types/agent.ts';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = Number(process.env.PORT) || 3000;

app.use(express.json());

// In-memory state
const startTime = Date.now();

let agentInfo: AgentInfo = {
  agentId: 'mbp-m3-max-local',
  hostname: 'Arthur-MacBook-Pro.local',
  workspace: '~/dev/mobile-agent-platform',
  status: 'online',
  lastHeartbeat: new Date().toISOString(),
  version: 'v1.4.2',
  os: 'macOS 15.1 (Darwin 24.1.0 arm64)',
  activeTaskId: null,
  batteryLevel: 94,
  isSimulated: true,
};

let simulatorActive = true;
let lastAgentContact = Date.now();

// SSE listeners
type SSEClient = {
  id: string;
  res: Response;
  taskId?: string;
};
let sseClients: SSEClient[] = [];

function broadcastEvent(event: TaskEvent, task?: Task) {
  const payload = JSON.stringify({ event, task });
  sseClients.forEach((client) => {
    if (!client.taskId || client.taskId === event.taskId) {
      client.res.write(`id: ${event.id}\nevent: message\ndata: ${payload}\n\n`);
    }
  });
}

function broadcastAgentStatus() {
  const payload = JSON.stringify({ agentInfo });
  sseClients.forEach((client) => {
    client.res.write(`event: agent_status\ndata: ${payload}\n\n`);
  });
}

const sampleDiff = `--- a/src/routes/auth.ts
+++ b/src/routes/auth.ts
@@ -12,6 +12,8 @@ import { Router } from 'express';
 import { validateCredentials } from '../services/auth';
+import { createRateLimiter } from '../middleware/rate-limiter';
 
 const router = Router();
+const authLimiter = createRateLimiter({ windowMs: 60 * 1000, max: 10 });
 
-router.post('/login', async (req, res) => {
+router.post('/login', authLimiter, async (req, res) => {
   const { email, password } = req.body;
--- /dev/null
+++ b/src/middleware/rate-limiter.ts
@@ -0,0 +1,38 @@
+import { Request, Response, NextFunction } from 'express';
+import Redis from 'ioredis';
+
+export function createRateLimiter(options: { windowMs: number; max: number }) {
+  const client = new Redis(process.env.REDIS_URL || 'redis://localhost:6379');
+  return async function rateLimiter(req: Request, res: Response, next: NextFunction) {
+    const key = "rate:" + (req.ip || "anon") + ":" + req.baseUrl;
+    const current = await client.incr(key);
+    if (current === 1) await client.pexpire(key, options.windowMs);
+    if (current > options.max) {
+      return res.status(429).json({ error: "Rate limit exceeded" });
+    }
+    res.setHeader("X-RateLimit-Limit", options.max);
+    next();
+  };
+}`;

// Initial tasks seed
const tasks: Task[] = [
  {
    id: 'task-7102',
    instruction: 'Add rate-limiting middleware to /api/auth routes with Redis token bucket',
    workspace: '~/dev/mobile-agent-platform',
    mode: 'supervised',
    model: 'DeepSeek-Coder-V2.5',
    status: 'awaiting_approval',
    currentStep: 'Proposed changes ready for code review and security inspection',
    stepIndex: 3,
    totalSteps: 5,
    createdAt: new Date(Date.now() - 14 * 60 * 1000).toISOString(),
    startedAt: new Date(Date.now() - 12 * 60 * 1000).toISOString(),
    approval: {
      id: 'appr-9014',
      taskId: 'task-7102',
      hash: 'sha256:d8c9710f24e4b50c058728a4128919a3b603a11e8c973a62',
      summary: 'Configured Redis token bucket limiter (60 req/min) for /api/auth endpoints with IP hash fallback.',
      proposedChanges: [
        {
          path: 'src/middleware/rate-limiter.ts',
          status: 'created',
          additions: 38,
          deletions: 0,
        },
        {
          path: 'src/routes/auth.ts',
          status: 'modified',
          additions: 9,
          deletions: 3,
        },
        {
          path: 'tests/middleware/rate-limiter.test.ts',
          status: 'created',
          additions: 62,
          deletions: 0,
        },
      ],
      diff: sampleDiff,
      state: 'pending',
      createdAt: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    },
    events: [
      {
        id: 'evt-1',
        taskId: 'task-7102',
        timestamp: new Date(Date.now() - 12 * 60 * 1000).toISOString(),
        type: 'status_change',
        message: 'Task picked up by Arthur-MacBook-Pro agent worker',
      },
      {
        id: 'evt-2',
        taskId: 'task-7102',
        timestamp: new Date(Date.now() - 11 * 60 * 1000).toISOString(),
        type: 'step',
        message: 'Analyzing auth routes and existing middleware pipeline',
        stepNumber: 1,
        totalSteps: 5,
      },
      {
        id: 'evt-3',
        taskId: 'task-7102',
        timestamp: new Date(Date.now() - 8 * 60 * 1000).toISOString(),
        type: 'step',
        message: 'Implemented src/middleware/rate-limiter.ts with redis token bucket and unit tests',
        stepNumber: 2,
        totalSteps: 5,
      },
      {
        id: 'evt-4',
        taskId: 'task-7102',
        timestamp: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
        type: 'approval_required',
        message: 'Two-phase approval required: inspection of rate limiting middleware changes before running live integration tests',
        stepNumber: 3,
        totalSteps: 5,
      },
    ],
  },
  {
    id: 'task-7095',
    instruction: 'Refactor DB connection pool configuration and add health check ping query',
    workspace: '~/dev/mobile-agent-platform',
    mode: 'autonomous',
    model: 'DeepSeek-Coder-V2.5',
    status: 'completed',
    currentStep: 'Task completed successfully with all unit tests passing',
    stepIndex: 4,
    totalSteps: 4,
    createdAt: new Date(Date.now() - 48 * 60 * 1000).toISOString(),
    startedAt: new Date(Date.now() - 47 * 60 * 1000).toISOString(),
    completedAt: new Date(Date.now() - 44 * 60 * 1000).toISOString(),
    result: {
      summary: 'Added connection timeout retry (3 retries with backoff), idle eviction, and exposed GET /health/db.',
      filesModified: ['src/db/connection.ts', 'src/routes/health.ts'],
      testsPassed: true,
      testOutput: 'PASS tests/db/connection.test.ts (2 tests)\nPASS tests/routes/health.test.ts (3 tests)\nTest Suites: 2 passed, 2 total\nTests: 5 passed, 5 total\nTime: 1.84s',
      durationMs: 184000,
      branchName: 'refactor/db-pool-health',
    },
    events: [
      {
        id: 'evt-101',
        taskId: 'task-7095',
        timestamp: new Date(Date.now() - 47 * 60 * 1000).toISOString(),
        type: 'status_change',
        message: 'Agent started execution on MacBook Pro',
      },
      {
        id: 'evt-102',
        taskId: 'task-7095',
        timestamp: new Date(Date.now() - 46 * 60 * 1000).toISOString(),
        type: 'step',
        message: 'Updated connection pool parameters and ping retry',
        stepNumber: 2,
        totalSteps: 4,
      },
      {
        id: 'evt-103',
        taskId: 'task-7095',
        timestamp: new Date(Date.now() - 45 * 60 * 1000).toISOString(),
        type: 'test_run',
        message: 'Ran test suite: 5/5 tests passed',
        stepNumber: 3,
        totalSteps: 4,
      },
      {
        id: 'evt-104',
        taskId: 'task-7095',
        timestamp: new Date(Date.now() - 44 * 60 * 1000).toISOString(),
        type: 'status_change',
        message: 'Task completed successfully',
        stepNumber: 4,
        totalSteps: 4,
      },
    ],
  },
];

// Simulator logic to simulate realistic agent steps when enabled
function processQueuedTask(task: Task) {
  if (!simulatorActive) {
    // Agent is offline or not simulating; leave in queued state!
    return;
  }

  task.status = 'running';
  task.startedAt = new Date().toISOString();
  task.currentStep = 'Checking repository git status and index';
  task.stepIndex = 1;
  task.totalSteps = task.mode === 'supervised' ? 4 : 3;
  agentInfo.activeTaskId = task.id;
  agentInfo.status = 'busy';

  const startEvt: TaskEvent = {
    id: `evt-${Date.now()}-1`,
    taskId: task.id,
    timestamp: new Date().toISOString(),
    type: 'status_change',
    message: `MacBook agent acquired task "${task.instruction.slice(0, 40)}..."`,
  };
  task.events.push(startEvt);
  broadcastEvent(startEvt, task);
  broadcastAgentStatus();

  // Step 1: analyze
  setTimeout(() => {
    if (task.status !== 'running') return;
    task.currentStep = 'Parsing workspace source tree and identifying target files';
    task.stepIndex = 2;
    const evt: TaskEvent = {
      id: `evt-${Date.now()}-2`,
      taskId: task.id,
      timestamp: new Date().toISOString(),
      type: 'step',
      message: 'Inspecting code dependencies and AST structure',
      stepNumber: 2,
      totalSteps: task.totalSteps,
    };
    task.events.push(evt);
    broadcastEvent(evt, task);

    // Step 2: generation & diff or approval
    setTimeout(() => {
      if (task.status !== 'running') return;

      if (task.mode === 'supervised') {
        task.status = 'awaiting_approval';
        task.currentStep = 'Code modifications generated; awaiting operator approval';
        task.stepIndex = 3;
        agentInfo.status = 'online';

        const hash = `sha256:${crypto.randomBytes(24).toString('hex')}`;
        task.approval = {
          id: `appr-${Date.now()}`,
          taskId: task.id,
          hash,
          summary: `Generated proposed changes for: "${task.instruction}"`,
          proposedChanges: [
            {
              path: 'src/features/agent-action.ts',
              status: 'modified',
              additions: 34,
              deletions: 8,
            },
            {
              path: 'tests/features/agent-action.test.ts',
              status: 'created',
              additions: 45,
              deletions: 0,
            },
          ],
          diff: `--- a/src/features/agent-action.ts
+++ b/src/features/agent-action.ts
@@ -8,7 +8,15 @@ export interface ActionOptions {
   timeoutMs?: number;
+  retries?: number;
+  validatePayload?: boolean;
 }
 
 export async function executeAction(options: ActionOptions) {
-  console.log('Executing basic action', options);
+  const retries = options.retries ?? 3;
+  if (options.validatePayload) {
+    validateActionPayload(options);
+  }
+  return runWithRetry(options, retries);
 }`,
          state: 'pending',
          createdAt: new Date().toISOString(),
        };

        const apprEvt: TaskEvent = {
          id: `evt-${Date.now()}-3`,
          taskId: task.id,
          timestamp: new Date().toISOString(),
          type: 'approval_required',
          message: 'Review required: Inspect diff and approve or reject before testing and committing',
          stepNumber: 3,
          totalSteps: task.totalSteps,
        };
        task.events.push(apprEvt);
        broadcastEvent(apprEvt, task);
        broadcastAgentStatus();
      } else {
        finishTaskAutonomous(task);
      }
    }, 4500);
  }, 3500);
}

function finishTaskAutonomous(task: Task) {
  task.currentStep = 'Running local test suites and verifying linters';
  task.stepIndex = 3;
  const testEvt: TaskEvent = {
    id: `evt-${Date.now()}-test`,
    taskId: task.id,
    timestamp: new Date().toISOString(),
    type: 'test_run',
    message: 'Test runner executed: 4 passed, 0 failed, duration 1.4s',
    stepNumber: 3,
    totalSteps: task.totalSteps,
  };
  task.events.push(testEvt);
  broadcastEvent(testEvt, task);

  setTimeout(() => {
    task.status = 'completed';
    task.completedAt = new Date().toISOString();
    task.currentStep = 'Task completed successfully';
    task.result = {
      summary: `Successfully completed: ${task.instruction}`,
      filesModified: ['src/features/agent-action.ts', 'tests/features/agent-action.test.ts'],
      testsPassed: true,
      testOutput: 'PASS tests/features/agent-action.test.ts\nAll tests passed successfully (4/4).',
      durationMs: 8200,
      branchName: `agent/feat-${task.id.slice(-6)}`,
    };
    agentInfo.status = 'online';
    agentInfo.activeTaskId = null;

    const compEvt: TaskEvent = {
      id: `evt-${Date.now()}-comp`,
      taskId: task.id,
      timestamp: new Date().toISOString(),
      type: 'status_change',
      message: 'Task completed successfully and working tree is clean.',
    };
    task.events.push(compEvt);
    broadcastEvent(compEvt, task);
    broadcastAgentStatus();
  }, 2500);
}

// Background agent heartbeat updater
setInterval(() => {
  if (simulatorActive) {
    agentInfo.lastHeartbeat = new Date().toISOString();
    lastAgentContact = Date.now();
  } else {
    const elapsed = Date.now() - lastAgentContact;
    if (elapsed > 45000 && agentInfo.status !== 'offline') {
      agentInfo.status = 'offline';
      broadcastAgentStatus();
    }
  }
}, 10000);

// SSE keepalive ping every 15s
setInterval(() => {
  sseClients.forEach((client) => {
    client.res.write(': keep-alive\n\n');
  });
}, 15000);

// --- REST API ROUTES ---

// 1. Agent Status
app.get('/api/agent/status', (_req: Request, res: Response) => {
  const elapsed = Math.floor((Date.now() - lastAgentContact) / 1000);
  res.json({
    connected: agentInfo.status !== 'offline',
    status: agentInfo.status,
    info: agentInfo,
    secondsSinceHeartbeat: elapsed,
    isSimulated: simulatorActive,
  });
});

// 2. Real MacBook Agent Heartbeat
app.post('/api/agent/heartbeat', (req: Request, res: Response) => {
  const { agentId, hostname, workspace, status, activeTaskId, os, version } = req.body || {};
  agentInfo = {
    agentId: agentId || 'macbook-agent',
    hostname: hostname || 'local-macbook',
    workspace: workspace || '~/dev',
    status: (status as any) || 'online',
    lastHeartbeat: new Date().toISOString(),
    version: version || 'v1.4.2',
    os: os || 'macOS',
    activeTaskId: activeTaskId || null,
    isSimulated: false,
  };
  simulatorActive = false;
  lastAgentContact = Date.now();
  broadcastAgentStatus();

  const queued = tasks.filter((t) => t.status === 'queued');
  res.json({
    ack: true,
    serverTime: new Date().toISOString(),
    queuedCount: queued.length,
    nextTaskId: queued[0]?.id || null,
  });
});

// 3. Agent simulator toggle
app.post('/api/agent/simulate-toggle', (req: Request, res: Response) => {
  const { enabled } = req.body || {};
  simulatorActive = typeof enabled === 'boolean' ? enabled : !simulatorActive;
  if (simulatorActive) {
    agentInfo.status = 'online';
    agentInfo.lastHeartbeat = new Date().toISOString();
    agentInfo.isSimulated = true;
    lastAgentContact = Date.now();

    const nextQueued = tasks.find((t) => t.status === 'queued');
    if (nextQueued) {
      processQueuedTask(nextQueued);
    }
  } else {
    agentInfo.status = 'offline';
    agentInfo.isSimulated = false;
  }
  broadcastAgentStatus();
  res.json({
    simulatorActive,
    agentStatus: agentInfo.status,
  });
});

// 4. List Tasks
app.get('/api/tasks', (req: Request, res: Response) => {
  const statusFilter = req.query.status as string;
  const searchFilter = (req.query.search as string || '').toLowerCase().trim();
  const limit = Math.min(Number(req.query.limit) || 50, 100);
  const offset = Number(req.query.offset) || 0;

  let filtered = [...tasks];
  if (statusFilter && statusFilter !== 'all') {
    filtered = filtered.filter((t) => t.status === statusFilter);
  }
  if (searchFilter) {
    filtered = filtered.filter(
      (t) =>
        t.instruction.toLowerCase().includes(searchFilter) ||
        t.id.toLowerCase().includes(searchFilter) ||
        t.workspace.toLowerCase().includes(searchFilter)
    );
  }

  filtered.sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());

  const total = filtered.length;
  const paginated = filtered.slice(offset, offset + limit);

  res.json({
    tasks: paginated,
    total,
    offset,
    limit,
  });
});

// 5. Create Task
app.post('/api/tasks', (req: Request, res: Response) => {
  const { instruction, workspace, mode, model } = req.body as CreateTaskPayload;

  if (!instruction || typeof instruction !== 'string' || !instruction.trim()) {
    return res.status(400).json({ error: 'Instruction text is required.' });
  }

  const taskId = `task-${Math.floor(1000 + Math.random() * 9000)}`;
  const targetWorkspace = workspace || agentInfo.workspace || '~/dev/mobile-agent-platform';
  const taskMode = mode || 'supervised';
  const taskModel = model || 'DeepSeek-Coder-V2.5';

  const isAgentOnline = agentInfo.status !== 'offline';

  const newTask: Task = {
    id: taskId,
    instruction: instruction.trim(),
    workspace: targetWorkspace,
    mode: taskMode,
    model: taskModel,
    status: 'queued',
    createdAt: new Date().toISOString(),
    events: [
      {
        id: `evt-${Date.now()}-create`,
        taskId,
        timestamp: new Date().toISOString(),
        type: 'status_change',
        message: isAgentOnline
          ? 'Task submitted and placed in agent execution queue'
          : 'Task queued. Execution will begin once MacBook agent establishes outbound connection.',
      },
    ],
  };

  tasks.unshift(newTask);

  broadcastEvent(newTask.events[0], newTask);

  if (isAgentOnline) {
    setTimeout(() => {
      processQueuedTask(newTask);
    }, 1200);
  }

  res.status(201).json({
    task: newTask,
    message: isAgentOnline ? 'Task accepted and queued' : 'Task queued (MacBook agent offline)',
  });
});

// 6. Get Task Details
app.get('/api/tasks/:id', (req: Request, res: Response) => {
  const task = tasks.find((t) => t.id === req.params.id);
  if (!task) {
    return res.status(404).json({ error: 'Task not found' });
  }
  res.json({ task });
});

// 7. Cancel Task
app.post('/api/tasks/:id/cancel', (req: Request, res: Response) => {
  const task = tasks.find((t) => t.id === req.params.id);
  if (!task) {
    return res.status(404).json({ error: 'Task not found' });
  }

  if (task.status === 'completed' || task.status === 'failed') {
    return res.status(400).json({ error: `Cannot cancel a task that is already ${task.status}.` });
  }

  task.status = 'cancelled';
  task.completedAt = new Date().toISOString();
  task.currentStep = 'Cancelled by operator';
  
  if (agentInfo.activeTaskId === task.id) {
    agentInfo.activeTaskId = null;
    agentInfo.status = 'online';
    broadcastAgentStatus();
  }

  const cancelEvt: TaskEvent = {
    id: `evt-${Date.now()}-cancel`,
    taskId: task.id,
    timestamp: new Date().toISOString(),
    type: 'status_change',
    message: 'Task was cancelled by operator.',
  };
  task.events.push(cancelEvt);
  broadcastEvent(cancelEvt, task);

  res.json({ task, message: 'Task cancelled successfully.' });
});

// 8. Retry Task
app.post('/api/tasks/:id/retry', (req: Request, res: Response) => {
  const task = tasks.find((t) => t.id === req.params.id);
  if (!task) {
    return res.status(404).json({ error: 'Task not found' });
  }

  task.status = 'queued';
  task.startedAt = undefined;
  task.completedAt = undefined;
  task.result = undefined;
  task.approval = undefined;
  task.currentStep = 'Queued for retry';

  const retryEvt: TaskEvent = {
    id: `evt-${Date.now()}-retry`,
    taskId: task.id,
    timestamp: new Date().toISOString(),
    type: 'status_change',
    message: 'Task queued for execution retry.',
  };
  task.events.push(retryEvt);
  broadcastEvent(retryEvt, task);

  if (agentInfo.status !== 'offline') {
    setTimeout(() => {
      processQueuedTask(task);
    }, 1000);
  }

  res.json({ task, message: 'Task re-queued for execution.' });
});

// 9. Two-Phase Approval Decision
app.post('/api/tasks/:id/approval', (req: Request, res: Response) => {
  const task = tasks.find((t) => t.id === req.params.id);
  if (!task) {
    return res.status(404).json({ error: 'Task not found' });
  }

  if (task.status !== 'awaiting_approval' || !task.approval) {
    return res.status(400).json({ 
      error: 'Task is not awaiting approval.', 
      currentState: task.status 
    });
  }

  const { action, hash, comment } = req.body as ApprovalDecisionPayload;

  if (!action || !['approve', 'reject'].includes(action)) {
    return res.status(400).json({ error: 'Invalid approval action. Must be "approve" or "reject".' });
  }

  if (task.approval.hash !== hash) {
    return res.status(409).json({ 
      error: 'Approval proposal hash mismatch. The proposed diff may have changed or become stale.',
      expectedHash: task.approval.hash,
      providedHash: hash,
    });
  }

  if (task.approval.state !== 'pending') {
    return res.status(409).json({
      error: `Approval proposal has already been ${task.approval.state}.`,
      state: task.approval.state,
    });
  }

  const now = new Date().toISOString();
  task.approval.state = action === 'approve' ? 'approved' : 'rejected';
  task.approval.respondedAt = now;
  task.approval.responseComment = comment?.trim() || undefined;
  task.approval.decision = action;

  if (action === 'approve') {
    task.status = 'running';
    task.currentStep = 'Approval granted. Applying changes and executing automated tests.';
    task.stepIndex = 4;

    const apprEvt: TaskEvent = {
      id: `evt-${Date.now()}-appr-grant`,
      taskId: task.id,
      timestamp: now,
      type: 'approval_resolved',
      message: `Approval granted by operator. Resuming execution${comment ? `: "${comment}"` : '.'}`,
    };
    task.events.push(apprEvt);
    broadcastEvent(apprEvt, task);

    setTimeout(() => {
      finishTaskAutonomous(task);
    }, 2000);
  } else {
    task.status = 'failed';
    task.completedAt = now;
    task.currentStep = 'Execution aborted due to operator rejection of proposed diff.';
    task.error = `Proposed diff was rejected${comment ? `: "${comment}"` : '.'}`;

    const rejEvt: TaskEvent = {
      id: `evt-${Date.now()}-appr-rej`,
      taskId: task.id,
      timestamp: now,
      type: 'approval_resolved',
      message: `Proposed changes rejected by operator${comment ? `: "${comment}"` : '.'}`,
    };
    task.events.push(rejEvt);
    broadcastEvent(rejEvt, task);

    if (agentInfo.activeTaskId === task.id) {
      agentInfo.activeTaskId = null;
      agentInfo.status = 'online';
      broadcastAgentStatus();
    }
  }

  res.json({
    task,
    approval: task.approval,
    message: action === 'approve' ? 'Changes approved and resumed.' : 'Changes rejected and task aborted.',
  });
});

// 10. SSE Stream for Single Task
app.get('/api/tasks/:id/events', (req: Request, res: Response) => {
  const taskId = req.params.id;
  const task = tasks.find((t) => t.id === taskId);
  if (!task) {
    return res.status(404).json({ error: 'Task not found' });
  }

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders?.();

  const clientId = `client-${Date.now()}-${Math.random()}`;
  const client: SSEClient = { id: clientId, res, taskId };
  sseClients.push(client);

  res.write(`event: init\ndata: ${JSON.stringify({ task, agentInfo })}\n\n`);

  req.on('close', () => {
    sseClients = sseClients.filter((c) => c.id !== clientId);
  });
});

// 11. Global SSE Stream
app.get('/api/events', (req: Request, res: Response) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders?.();

  const clientId = `global-${Date.now()}-${Math.random()}`;
  const client: SSEClient = { id: clientId, res };
  sseClients.push(client);

  res.write(`event: connected\ndata: ${JSON.stringify({ status: 'connected', time: new Date().toISOString() })}\n\n`);

  req.on('close', () => {
    sseClients = sseClients.filter((c) => c.id !== clientId);
  });
});

// 12. Diagnostics & Configuration
app.get('/api/diagnostics', (_req: Request, res: Response) => {
  const uptime = Math.floor((Date.now() - startTime) / 1000);
  const elapsedHeartbeat = Math.floor((Date.now() - lastAgentContact) / 1000);

  const stats = {
    total: tasks.length,
    queued: tasks.filter((t) => t.status === 'queued').length,
    running: tasks.filter((t) => t.status === 'running').length,
    awaitingApproval: tasks.filter((t) => t.status === 'awaiting_approval').length,
    completed: tasks.filter((t) => t.status === 'completed').length,
    failed: tasks.filter((t) => t.status === 'failed').length,
    cancelled: tasks.filter((t) => t.status === 'cancelled').length,
  };

  const diagnostics: Diagnostics = {
    gateway: {
      status: 'online',
      uptimeSeconds: uptime,
      timestamp: new Date().toISOString(),
      version: 'dev-agent-gateway v1.2.0',
      environment: process.env.NODE_ENV || 'development',
    },
    agent: {
      connected: agentInfo.status !== 'offline',
      status: agentInfo.status,
      info: agentInfo,
      secondsSinceHeartbeat: elapsedHeartbeat,
      isSimulated: simulatorActive,
    },
    aiProvider: {
      provider: 'DeepSeek Proxy (Local/Private)',
      endpointConfigured: true,
      defaultModel: 'DeepSeek-Coder-V2.5',
      availableModels: ['DeepSeek-Coder-V2.5', 'DeepSeek-V3', 'Claude-3.5-Sonnet (proxy fallback)'],
      status: 'ready',
    },
    queue: stats,
    workspaces: [
      '~/dev/mobile-agent-platform',
      '~/dev/customer-dashboard',
      '~/dev/infrastructure-iac',
    ],
  };

  res.json(diagnostics);
});

// 13. Available Workspaces
app.get('/api/workspaces', (_req: Request, res: Response) => {
  res.json({
    workspaces: [
      '~/dev/mobile-agent-platform',
      '~/dev/customer-dashboard',
      '~/dev/infrastructure-iac',
    ],
    defaultWorkspace: agentInfo.workspace || '~/dev/mobile-agent-platform',
  });
});

// Vite & Static file handling
async function startServer() {
  if (process.env.NODE_ENV === 'production') {
    app.use(express.static(path.resolve(__dirname, 'dist')));
    app.get('*', (_req: Request, res: Response) => {
      res.sendFile(path.resolve(__dirname, 'dist', 'index.html'));
    });
  } else {
    const vite = await createViteServer({
      server: { 
        middlewareMode: true,
        hmr: process.env.DISABLE_HMR !== 'true' 
      },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  }

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`[dev-agent-platform] Gateway running on http://0.0.0.0:${PORT}`);
  });
}

startServer().catch((err) => {
  console.error('Failed to start server:', err);
  process.exit(1);
});
