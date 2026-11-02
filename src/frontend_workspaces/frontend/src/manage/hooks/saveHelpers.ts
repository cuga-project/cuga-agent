export function isAbortError(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (err instanceof Error && err.name === "AbortError") return true;
  return false;
}

// The config store returns published versions as strings ("3"); older payloads used numbers.
export function parsePublishedVersion(v: unknown): number | null {
  const n = typeof v === "string" && v.trim() !== "" ? Number(v) : v;
  return typeof n === "number" && Number.isInteger(n) ? n : null;
}

export type AddToast = (
  kind: "error" | "info" | "success" | "warning",
  title: string,
  subtitle: string,
) => void;
