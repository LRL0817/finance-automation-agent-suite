import { readdir, rm, stat } from "node:fs/promises";
import path from "node:path";
import type { AppConfig } from "../config.js";
import { logger } from "../logger.js";

interface CleanupStats {
  filesDeleted: number;
  dirsDeleted: number;
  bytesDeleted: number;
}

export class AttachmentCleaner {
  private timer?: NodeJS.Timeout;

  constructor(private readonly config: AppConfig) {}

  start(): void {
    void this.cleanOnce();
    this.timer = setInterval(() => {
      void this.cleanOnce();
    }, this.config.attachments.cleanupIntervalMs);
    this.timer.unref();
  }

  stop(): void {
    if (!this.timer) {
      return;
    }

    clearInterval(this.timer);
    this.timer = undefined;
  }

  async cleanOnce(): Promise<void> {
    const dir = this.config.attachments.dir;
    if (!isSafeAttachmentDir(dir)) {
      logger.warn("Skip attachment cleanup because directory is unsafe", { dir });
      return;
    }

    const cutoffMs = Date.now() - this.config.attachments.retentionMs;
    const stats: CleanupStats = { filesDeleted: 0, dirsDeleted: 0, bytesDeleted: 0 };

    try {
      await cleanupDir(dir, cutoffMs, stats, true);
      if (stats.filesDeleted > 0 || stats.dirsDeleted > 0) {
        logger.info("Attachment cleanup completed", { dir, ...stats });
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (!message.includes("ENOENT")) {
        logger.warn("Attachment cleanup failed", error);
      }
    }
  }
}

async function cleanupDir(
  dir: string,
  cutoffMs: number,
  stats: CleanupStats,
  isRoot: boolean
): Promise<boolean> {
  const entries = await readdir(dir, { withFileTypes: true }).catch((error: unknown) => {
    if (error instanceof Error && "code" in error && error.code === "ENOENT") {
      return [];
    }
    throw error;
  });

  let hasRemainingEntries = false;
  for (const entry of entries) {
    const entryPath = path.join(dir, entry.name);

    if (entry.isDirectory()) {
      const removed = await cleanupDir(entryPath, cutoffMs, stats, false);
      hasRemainingEntries ||= !removed;
      continue;
    }

    if (!entry.isFile()) {
      hasRemainingEntries = true;
      continue;
    }

    const fileStat = await stat(entryPath).catch(() => null);
    if (!fileStat) {
      continue;
    }

    if (fileStat.mtimeMs > cutoffMs) {
      hasRemainingEntries = true;
      continue;
    }

    await rm(entryPath, { force: true });
    stats.filesDeleted += 1;
    stats.bytesDeleted += fileStat.size;
  }

  if (!isRoot && !hasRemainingEntries) {
    await rm(dir, { force: true, recursive: false }).catch(() => undefined);
    stats.dirsDeleted += 1;
    return true;
  }

  return false;
}

function isSafeAttachmentDir(dir: string): boolean {
  const resolved = path.resolve(dir);
  const root = path.parse(resolved).root;
  if (resolved === root) {
    return false;
  }

  const normalized = resolved.toLowerCase();
  return normalized.includes(`${path.sep}data${path.sep}attachments`) || normalized.endsWith(`${path.sep}attachments`);
}
