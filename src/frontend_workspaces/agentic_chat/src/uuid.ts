export function randomUUID(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const hex = "0123456789abcdef";
  const rnd = (n: number) => Math.floor(Math.random() * n);
  const segment = (len: number) =>
    Array.from({ length: len }, () => hex[rnd(16)]).join("");
  return [
    segment(8),
    segment(4),
    "4" + segment(3),
    ((rnd(4) | 8) >>> 0).toString(16) + segment(3),
    segment(12),
  ].join("-");
}
