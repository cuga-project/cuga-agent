export function isAbortError(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (err instanceof Error && err.name === "AbortError") return true;
  return false;
}

export type AddToast = (
  kind: "error" | "info" | "success" | "warning",
  title: string,
  subtitle: string,
) => void;
