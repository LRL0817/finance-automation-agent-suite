import fs from "node:fs";
import path from "node:path";

export function normalizePath(input: string): string {
  return path.resolve(input.trim());
}

export function ensureDirectory(input: string): boolean {
  try {
    return fs.statSync(input).isDirectory();
  } catch {
    return false;
  }
}

export function isPathInside(child: string, parent: string): boolean {
  const normalizedChild = normalizeForCompare(path.resolve(child));
  const normalizedParent = normalizeForCompare(path.resolve(parent));

  if (normalizedChild === normalizedParent) {
    return true;
  }

  const withSeparator = normalizedParent.endsWith(path.sep)
    ? normalizedParent
    : `${normalizedParent}${path.sep}`;

  return normalizedChild.startsWith(withSeparator);
}

function normalizeForCompare(value: string): string {
  const resolved = path.normalize(value);
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}
