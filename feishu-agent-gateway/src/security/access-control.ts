import { ensureDirectory, isPathInside, normalizePath } from "../utils/paths.js";
import type { AppConfig } from "../config.js";

export class AccessControl {
  constructor(private readonly config: AppConfig) {}

  hasAllowedUsers(): boolean {
    return this.config.security.allowedUserIds.size > 0;
  }

  isAllowedUser(userId: string): boolean {
    return this.config.security.allowedUserIds.has(userId);
  }

  validateProjectPath(input: string): { ok: true; path: string } | { ok: false; reason: string } {
    const normalized = normalizePath(input);

    if (this.config.security.projectWhitelist.length === 0) {
      return { ok: false, reason: "PROJECT_WHITELIST 为空，请先在 .env 中配置允许访问的项目目录。" };
    }

    if (!ensureDirectory(normalized)) {
      return { ok: false, reason: `项目目录不存在或不是目录：${normalized}` };
    }

    const allowed = this.config.security.projectWhitelist.some((root) =>
      isPathInside(normalized, root)
    );

    if (!allowed) {
      return { ok: false, reason: `项目目录不在白名单内：${normalized}` };
    }

    return { ok: true, path: normalized };
  }
}
