import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import type { RunnerName, SessionRecord, TaskRecord, TaskStatus } from "../domain/types.js";
import { nowIso } from "../utils/text.js";

const require = createRequire(import.meta.url);

interface SqliteStatement {
  run(...params: unknown[]): unknown;
  get(...params: unknown[]): unknown;
  all(...params: unknown[]): unknown[];
}

interface SqliteDatabase {
  exec(sql: string): void;
  prepare(sql: string): SqliteStatement;
  close(): void;
}

const { DatabaseSync } = require("node:sqlite") as {
  DatabaseSync: new (filename: string) => SqliteDatabase;
};

export class GatewayDatabase {
  private readonly db: SqliteDatabase;

  constructor(private readonly sqlitePath: string) {
    fs.mkdirSync(path.dirname(sqlitePath), { recursive: true });
    this.db = new DatabaseSync(sqlitePath);
    this.migrate();
  }

  close(): void {
    this.db.close();
  }

  getOrCreateSession(params: {
    sessionId: string;
    userId: string;
    chatId: string;
    runner: RunnerName;
    projectPath?: string | null;
  }): SessionRecord {
    const existing = this.getSession(params.sessionId);
    if (existing) {
      return existing;
    }

    const now = nowIso();
    this.db
      .prepare(
        `INSERT INTO sessions (
          session_id, user_id, chat_id, project_path, runner, task_status,
          current_task_id, runner_thread_id, pending_task, pending_reason, last_output, last_error,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'idle', NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)`
      )
      .run(params.sessionId, params.userId, params.chatId, params.projectPath ?? null, params.runner, now, now);

    const created = this.getSession(params.sessionId);
    if (!created) {
      throw new Error("Failed to create session");
    }
    return created;
  }

  getSession(sessionId: string): SessionRecord | null {
    const row = this.db.prepare("SELECT * FROM sessions WHERE session_id = ?").get(sessionId);
    return (row as SessionRecord | undefined) ?? null;
  }

  getLatestSessionForUsers(userIds: string[]): SessionRecord | null {
    if (userIds.length === 0) {
      return null;
    }

    const placeholders = userIds.map(() => "?").join(", ");
    const row = this.db
      .prepare(
        `SELECT * FROM sessions
         WHERE user_id IN (${placeholders})
         ORDER BY updated_at DESC
         LIMIT 1`
      )
      .get(...userIds);

    return (row as SessionRecord | undefined) ?? null;
  }

  bindProject(sessionId: string, projectPath: string): void {
    this.db
      .prepare(
        `UPDATE sessions
         SET project_path = ?, task_status = 'idle', runner_thread_id = NULL,
             pending_task = NULL, pending_reason = NULL, last_error = NULL, updated_at = ?
         WHERE session_id = ?`
      )
      .run(projectPath, nowIso(), sessionId);
  }

  applyDefaultProjectPath(projectPath: string): void {
    this.db
      .prepare(
        `UPDATE sessions
         SET project_path = ?, updated_at = ?
         WHERE project_path IS NULL`
      )
      .run(projectPath, nowIso());
  }

  restoreMissingRunnerThreadsFromTasks(): void {
    this.db.exec(`
      UPDATE sessions
      SET runner_thread_id = (
        SELECT tasks.runner_thread_id
        FROM tasks
        WHERE tasks.session_id = sessions.session_id
          AND tasks.runner_thread_id IS NOT NULL
        ORDER BY tasks.updated_at DESC
        LIMIT 1
      )
      WHERE runner_thread_id IS NULL
        AND EXISTS (
          SELECT 1
          FROM tasks
          WHERE tasks.session_id = sessions.session_id
            AND tasks.runner_thread_id IS NOT NULL
        );
    `);
  }

  updateSessionStatus(params: {
    sessionId: string;
    status: TaskStatus;
    currentTaskId?: string | null;
    runnerThreadId?: string | null;
    lastOutput?: string | null;
    lastError?: string | null;
  }): void {
    const currentTaskId = params.currentTaskId === undefined ? undefined : params.currentTaskId;
    const runnerThreadId = params.runnerThreadId === undefined ? undefined : params.runnerThreadId;
    const lastOutput = params.lastOutput === undefined ? undefined : params.lastOutput;
    const lastError = params.lastError === undefined ? undefined : params.lastError;

    const updates = ["task_status = ?", "updated_at = ?"];
    const values: unknown[] = [params.status, nowIso()];

    if (currentTaskId !== undefined) {
      updates.push("current_task_id = ?");
      values.push(currentTaskId);
    }
    if (runnerThreadId !== undefined) {
      updates.push("runner_thread_id = ?");
      values.push(runnerThreadId);
    }
    if (lastOutput !== undefined) {
      updates.push("last_output = ?");
      values.push(lastOutput);
    }
    if (lastError !== undefined) {
      updates.push("last_error = ?");
      values.push(lastError);
    }

    values.push(params.sessionId);
    this.db.prepare(`UPDATE sessions SET ${updates.join(", ")} WHERE session_id = ?`).run(...values);
  }

  setPendingApproval(sessionId: string, task: string, reason: string): void {
    this.db
      .prepare(
        `UPDATE sessions
         SET task_status = 'pending_approval', pending_task = ?, pending_reason = ?,
             last_error = NULL, updated_at = ?
         WHERE session_id = ?`
      )
      .run(task, reason, nowIso(), sessionId);
  }

  clearPendingApproval(sessionId: string): void {
    this.db
      .prepare(
        `UPDATE sessions
         SET pending_task = NULL, pending_reason = NULL, updated_at = ?
         WHERE session_id = ?`
      )
      .run(nowIso(), sessionId);
  }

  createTask(params: {
    taskId: string;
    sessionId: string;
    userId: string;
    projectPath: string;
    runner: RunnerName;
    prompt: string;
    runnerThreadId?: string | null;
  }): TaskRecord {
    const now = nowIso();
    this.db
      .prepare(
        `INSERT INTO tasks (
          task_id, session_id, user_id, project_path, runner, prompt, runner_thread_id, status,
          last_output, changed_files, test_result, error, created_at, updated_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', NULL, NULL, NULL, NULL, ?, ?, NULL)`
      )
      .run(
        params.taskId,
        params.sessionId,
        params.userId,
        params.projectPath,
        params.runner,
        params.prompt,
        params.runnerThreadId ?? null,
        now,
        now
      );

    const created = this.getTask(params.taskId);
    if (!created) {
      throw new Error("Failed to create task");
    }
    return created;
  }

  getTask(taskId: string): TaskRecord | null {
    const row = this.db.prepare("SELECT * FROM tasks WHERE task_id = ?").get(taskId);
    return (row as TaskRecord | undefined) ?? null;
  }

  getLatestTask(sessionId: string): TaskRecord | null {
    const row = this.db
      .prepare("SELECT * FROM tasks WHERE session_id = ? ORDER BY created_at DESC LIMIT 1")
      .get(sessionId);
    return (row as TaskRecord | undefined) ?? null;
  }

  updateTask(params: {
    taskId: string;
    status?: TaskStatus;
    lastOutput?: string | null;
    changedFiles?: string[] | null;
    testResult?: string | null;
    error?: string | null;
    runnerThreadId?: string | null;
    finished?: boolean;
  }): void {
    const updates = ["updated_at = ?"];
    const values: unknown[] = [nowIso()];

    if (params.status !== undefined) {
      updates.push("status = ?");
      values.push(params.status);
    }
    if (params.lastOutput !== undefined) {
      updates.push("last_output = ?");
      values.push(params.lastOutput);
    }
    if (params.changedFiles !== undefined) {
      updates.push("changed_files = ?");
      values.push(params.changedFiles ? JSON.stringify(params.changedFiles) : null);
    }
    if (params.testResult !== undefined) {
      updates.push("test_result = ?");
      values.push(params.testResult);
    }
    if (params.error !== undefined) {
      updates.push("error = ?");
      values.push(params.error);
    }
    if (params.runnerThreadId !== undefined) {
      updates.push("runner_thread_id = ?");
      values.push(params.runnerThreadId);
    }
    if (params.finished) {
      updates.push("finished_at = ?");
      values.push(nowIso());
    }

    values.push(params.taskId);
    this.db.prepare(`UPDATE tasks SET ${updates.join(", ")} WHERE task_id = ?`).run(...values);
  }

  private migrate(): void {
    this.db.exec(`
      PRAGMA journal_mode = WAL;

      CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        chat_id TEXT NOT NULL,
        project_path TEXT,
        runner TEXT NOT NULL,
        task_status TEXT NOT NULL,
        current_task_id TEXT,
        runner_thread_id TEXT,
        pending_task TEXT,
        pending_reason TEXT,
        last_output TEXT,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        project_path TEXT NOT NULL,
        runner TEXT NOT NULL,
        prompt TEXT NOT NULL,
        runner_thread_id TEXT,
        status TEXT NOT NULL,
        last_output TEXT,
        changed_files TEXT,
        test_result TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        finished_at TEXT,
        FOREIGN KEY(session_id) REFERENCES sessions(session_id)
      );

      CREATE INDEX IF NOT EXISTS idx_tasks_session_created
      ON tasks(session_id, created_at DESC);
    `);

    this.addColumnIfMissing("sessions", "runner_thread_id", "TEXT");
    this.addColumnIfMissing("tasks", "runner_thread_id", "TEXT");
  }

  private addColumnIfMissing(tableName: string, columnName: string, columnType: string): void {
    const columns = this.db.prepare(`PRAGMA table_info(${tableName})`).all() as Array<{ name: string }>;
    if (columns.some((column) => column.name === columnName)) {
      return;
    }

    this.db.exec(`ALTER TABLE ${tableName} ADD COLUMN ${columnName} ${columnType}`);
  }
}
