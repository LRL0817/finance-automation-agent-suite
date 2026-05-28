import { truncateMiddle } from "../utils/text.js";

/**
 * 结构化的财务错误原因。由制单程序在 error/context/log/output 中输出一行：
 *   FINANCE_ERROR_JSON={"stage":"...","reason":"...","impact":"...", ...}
 * 字段可缺省；解析失败时回到旧的正则兜底逻辑，不抛异常。
 */
export interface FinanceError {
  stage?: string;
  reason?: string;
  impact?: string;
  next_action?: string;
  bank?: string;
  doc?: string;
  safe_state?: string;
}

const FINANCE_ERROR_FIELDS: (keyof FinanceError)[] = [
  "stage",
  "reason",
  "impact",
  "next_action",
  "bank",
  "doc",
  "safe_state"
];

const FINANCE_ERROR_PREFIX = /FINANCE_ERROR_JSON\s*=\s*/i;
const MAX_FINANCE_ERROR_JSON_CHARS = 64 * 1024;

/**
 * 从一段自由文本里提取 FINANCE_ERROR_JSON 行并解析。
 * - 允许 JSON 单行出现在更长的日志里。
 * - 字段可缺省。
 * - 解析失败一律返回 null，绝不抛出。
 * - 所有字段都会做路径 / 敏感信息脱敏。
 */
export function parseFinanceErrorJson(raw: string | null | undefined): FinanceError | null {
  if (typeof raw !== "string" || !raw) {
    return null;
  }

  const match = raw.match(FINANCE_ERROR_PREFIX);
  if (!match || match.index === undefined) {
    return null;
  }

  const jsonStart = raw.indexOf("{", match.index);
  if (jsonStart === -1) {
    return null;
  }

  const jsonText = extractBalancedJson(raw, jsonStart);
  if (!jsonText) {
    return null;
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(jsonText);
  } catch {
    return null;
  }

  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return null;
  }

  const source = parsed as Record<string, unknown>;
  const result: FinanceError = {};
  let hasField = false;

  for (const field of FINANCE_ERROR_FIELDS) {
    const value = source[field];
    if (typeof value === "string" && value.trim()) {
      result[field] = sanitizeFinanceText(value.trim());
      hasField = true;
    } else if (typeof value === "number" || typeof value === "boolean") {
      result[field] = sanitizeFinanceText(String(value));
      hasField = true;
    }
  }

  return hasField ? result : null;
}

/**
 * 从 startIndex 处的 "{" 开始，按括号配对截取一段 JSON 文本（忽略字符串字面量里的括号）。
 * 找不到匹配的右括号时返回 null。
 */
function extractBalancedJson(raw: string, startIndex: number): string | null {
  let depth = 0;
  let inString = false;
  let escaped = false;
  const endIndex = Math.min(raw.length, startIndex + MAX_FINANCE_ERROR_JSON_CHARS);

  for (let i = startIndex; i < endIndex; i += 1) {
    const char = raw[i];

    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (char === "\\") {
        escaped = true;
      } else if (char === '"') {
        inString = false;
      }
      continue;
    }

    if (char === '"') {
      inString = true;
    } else if (char === "{") {
      depth += 1;
    } else if (char === "}") {
      depth -= 1;
      if (depth === 0) {
        return raw.slice(startIndex, i + 1);
      }
    }
  }

  return null;
}

/**
 * 依次从多个来源（error / context / log / output 等）里找第一条可解析的 FINANCE_ERROR_JSON。
 */
export function extractFinanceError(...sources: (string | null | undefined)[]): FinanceError | null {
  for (const source of sources) {
    const parsed = parseFinanceErrorJson(source);
    if (parsed) {
      return parsed;
    }
  }
  return null;
}

/**
 * 把结构化错误渲染成面向财务同事的中文区块（用于飞书兜底话术）。
 */
export function formatFinanceErrorMessage(error: FinanceError): string {
  const lines = ["任务没有完成。", ""];

  if (error.stage) {
    lines.push(`卡在哪一步：${error.stage}`);
  }
  if (error.reason) {
    lines.push(`原因：${error.reason}`);
  }
  if (error.bank) {
    lines.push(`涉及银行：${error.bank}`);
  }
  if (error.doc) {
    lines.push(`相关单据：${error.doc}`);
  }
  if (error.impact) {
    lines.push(`影响：${error.impact}`);
  }
  if (error.safe_state) {
    lines.push(`当前安全状态：${error.safe_state}`);
  } else {
    lines.push("当前安全状态：未确认是否已操作银行/产生待审核，请按“可能未完成”处理。");
  }
  if (error.next_action) {
    lines.push(`下一步：${error.next_action}`);
  } else {
    lines.push("下一步：请人工核对银行待审核/待复核队列，必要时联系技术同事查看网关日志。");
  }

  return truncateMiddle(lines.join("\n"), 2500);
}

/**
 * 把结构化错误渲染成给 Codex 的 prompt 区块，提示其优先采用。
 */
export function formatFinanceErrorPromptBlock(error: FinanceError): string {
  const lines = ["结构化错误原因（由制单程序直接给出，请优先采用，不要凭自由日志猜测）："];
  const labels: Record<keyof FinanceError, string> = {
    stage: "卡在哪一步(stage)",
    reason: "原因(reason)",
    impact: "影响(impact)",
    next_action: "下一步(next_action)",
    bank: "涉及银行(bank)",
    doc: "相关单据(doc)",
    safe_state: "安全状态(safe_state)"
  };

  for (const field of FINANCE_ERROR_FIELDS) {
    const value = error[field];
    if (value) {
      lines.push(`- ${labels[field]}：${value}`);
    }
  }
  if (!error.safe_state) {
    lines.push("- 安全状态(safe_state)：未确认是否已操作银行/产生待审核，请按可能未完成处理");
  }

  return lines.join("\n");
}

/**
 * 路径与敏感信息脱敏：隐藏本机路径、文件名、.env、密码、token 等。
 * 用于一切将进入飞书 / Codex prompt 的财务文本。
 */
export function sanitizeFinanceText(value: string): string {
  return (
    value
      // Windows 绝对路径
      .replace(/\\\\[^\s\\/:*?"<>|\r\n]+\\(?:[^\s\\/:*?"<>|\r\n]+\\)*[^\s\\/:*?"<>|\r\n]*/g, "[本机路径已隐藏]")
      .replace(/[A-Za-z]:\\(?:[^\s\\/:*?"<>|\r\n]+\\)*[^\s\\/:*?"<>|\r\n]*/g, "[本机路径已隐藏]")
      // *nix 绝对路径。保守要求路径段是常见 ASCII 文件名，避免误伤“付款银行/账号/金额”这类中文业务短语。
      .replace(/(^|[\s([{:="'`])\/(?:[A-Za-z0-9._-]+\/){1,}[A-Za-z0-9._-]+\/?/g, "$1[本机路径已隐藏]")
      // 常见敏感键值：password=***REDACTED***
      .replace(/\bauthorization\b\s*[:=]\s*bearer\s+\S+/gi, "authorization=[已隐藏]")
      .replace(/\bbearer\s+\S+/gi, "bearer [已隐藏]")
      .replace(
        /\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|app[_-]?secret|cookie|authorization|bearer)\b\s*[:=]\s*\S+/gi,
        "$1=[已隐藏]"
      )
      // .env 文件引用
      .replace(/(^|\s)\.env(\.\w+)?\b/g, "$1[环境配置已隐藏]")
      // 裸文件名（带敏感扩展名）
      .replace(/[^\s\\/:*?"<>|\r\n]+\.(?:py|bat|ps1|js|ts|json|log|md|txt|env)/gi, "[文件名已隐藏]")
  );
}

