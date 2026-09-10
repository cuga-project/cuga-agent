export function isSecretRef(v: unknown): boolean {
  if (typeof v !== "string") return false;
  return v.startsWith("db://") || v.startsWith("vault://") || v.startsWith("aws://") || v.startsWith("env://");
}

export function normalizeSecretRef(ref: string): string {
  const base = ref.split("#")[0] ?? ref;
  return base.endsWith("/") ? base.slice(0, -1) : base;
}

/** Extract the secret slug from db://, vault://, aws://, or env:// refs. */
export function secretIdFromRef(ref: string): string {
  if (!ref) return "";
  if (ref.startsWith("vault://")) {
    const path = ref.slice("vault://".length).split("#")[0]?.replace(/\/+$/, "") ?? "";
    const parts = path.split("/").filter(Boolean);
    return parts[parts.length - 1] ?? path;
  }
  if (ref.startsWith("db://")) {
    return ref.slice("db://".length).split("/")[0]?.split("#")[0] ?? "";
  }
  if (ref.startsWith("aws://")) {
    return ref.slice("aws://".length).split("#")[0] ?? "";
  }
  if (ref.startsWith("env://")) {
    return ref.slice("env://".length);
  }
  return ref;
}

export function matchSecretRef(
  storedRef: string,
  options: { id: string; ref: string }[]
): string {
  if (!storedRef) return "";
  const exact = options.find((s) => s.ref === storedRef);
  if (exact) return exact.ref;

  const storedNorm = normalizeSecretRef(storedRef);
  const normMatch = options.find((s) => normalizeSecretRef(s.ref) === storedNorm);
  if (normMatch) return normMatch.ref;

  const storedId = secretIdFromRef(storedRef);
  if (storedId) {
    const idMatch = options.find((s) => s.id === storedId);
    if (idMatch) {
      if (storedRef.startsWith("env://")) {
        return `env://${storedId}`;
      }
      return idMatch.ref;
    }
  }

  return storedRef;
}

export function storedRefMissingFromList(
  storedRef: string,
  options: { id: string; ref: string }[],
  selectedRef: string
): boolean {
  if (!isSecretRef(storedRef)) return false;
  if (options.some((s) => s.ref === selectedRef || s.ref === storedRef)) return false;
  const storedId = secretIdFromRef(storedRef);
  if (storedId && options.some((s) => s.id === storedId)) return false;
  return true;
}
