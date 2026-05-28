export function truncateMiddle(value: string, maxLength = 3500): string {
  if (value.length <= maxLength) {
    return value;
  }

  const half = Math.floor((maxLength - 20) / 2);
  return `${value.slice(0, half)}\n...\n${value.slice(-half)}`;
}

export function tail(value: string, maxLength = 1800): string {
  if (value.length <= maxLength) {
    return value;
  }

  return value.slice(-maxLength);
}

export function nowIso(): string {
  return new Date().toISOString();
}
