const sensitive = /(?:api[_-]?key|authorization|token|secret|password)\s*[:=]\s*[^\s,}]+/gi;
export function redact(value: string): string { return value.replace(sensitive, (match) => `${match.split(/[:=]/)[0]}=[redacted]`); }
