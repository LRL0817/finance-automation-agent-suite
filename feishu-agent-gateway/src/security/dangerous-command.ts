const dangerousPatterns: Array<{ pattern: RegExp; reason: string }> = [
  { pattern: /\brm\s+-rf\b/i, reason: "包含 rm -rf 递归删除命令" },
  { pattern: /\bdel\s+\/[fsq]/i, reason: "包含 Windows 强制删除命令" },
  { pattern: /\bRemove-Item\b.*\b-Recurse\b/i, reason: "包含 PowerShell 递归删除命令" },
  { pattern: /\bgit\s+reset\s+--hard\b/i, reason: "包含 git reset --hard" },
  { pattern: /\bgit\s+clean\s+-[fdx]/i, reason: "包含 git clean 删除未跟踪文件" },
  { pattern: /\bformat\b/i, reason: "包含 format 相关危险操作" },
  { pattern: /\bdrop\s+database\b/i, reason: "包含 drop database" },
  { pattern: /\bchmod\s+-R\s+777\b/i, reason: "包含 chmod -R 777" },
  { pattern: /\b(curl|wget)\b.+\|\s*(sh|bash|powershell|pwsh)\b/i, reason: "包含下载脚本后直接执行" },
  { pattern: /删除.*(全部|所有|整个|目录|文件)/i, reason: "包含大范围删除意图" },
  { pattern: /(清空|格式化).*(目录|磁盘|数据库|仓库|项目)/i, reason: "包含清空或格式化意图" }
];

export function detectDangerousIntent(task: string): { dangerous: boolean; reason?: string } {
  const matched = dangerousPatterns.find(({ pattern }) => pattern.test(task));
  if (!matched) {
    return { dangerous: false };
  }

  return { dangerous: true, reason: matched.reason };
}
