// Shared source for the generated n8n reporting helpers.
function safe(value, key = "") {
  if (/token|password|secret|authorization|api.?key/i.test(key)) return "[REDACTED]";
  if (Array.isArray(value)) return value.map((item) => safe(item));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([field]) => field !== "run")
        .map(([field, item]) => [field, safe(item, field)]),
    );
  }
  return value;
}
