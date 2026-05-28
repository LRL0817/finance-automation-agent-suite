export type RunnerName = "claude" | "codex";

export type TaskStatus =
  | "idle"
  | "pending_approval"
  | "running"
  | "completed"
  | "failed"
  | "canceled";

export interface SessionRecord {
  session_id: string;
  user_id: string;
  chat_id: string;
  project_path: string | null;
  runner: RunnerName;
  task_status: TaskStatus;
  current_task_id: string | null;
  runner_thread_id: string | null;
  pending_task: string | null;
  pending_reason: string | null;
  last_output: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskRecord {
  task_id: string;
  session_id: string;
  user_id: string;
  project_path: string;
  runner: RunnerName;
  prompt: string;
  runner_thread_id: string | null;
  status: TaskStatus;
  last_output: string | null;
  changed_files: string | null;
  test_result: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface IncomingFeishuMessage {
  chatId: string;
  userId: string;
  messageId?: string;
  messageType?: string;
  text: string;
  imageKeys: string[];
  videoKeys: string[];
}

export interface RunnerResult {
  output: string;
  changedFiles: string[];
  testResult: string;
  runnerThreadId?: string | null;
}

export interface RunnerContext {
  sessionId: string;
  taskId: string;
  userId: string;
  projectPath: string;
  prompt: string;
  imagePaths?: string[];
  videoFrameIntervalSeconds?: number;
  runnerThreadId?: string | null;
  signal: AbortSignal;
  onOutput: (chunk: string) => void;
}

export interface AgentRunner {
  name: RunnerName;
  run(context: RunnerContext): Promise<RunnerResult>;
}
