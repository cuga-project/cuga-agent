export const TEXT_EXTENSIONS = [
  ".txt",
  ".md",
  ".json",
  ".yaml",
  ".yml",
  ".log",
  ".csv",
  ".html",
  ".css",
  ".js",
  ".ts",
  ".py",
];

export const JSON_UPLOAD_SUFFIXES = [".json", ".jsonl", ".ndjson"];

export const JSON_UPLOAD_ACCEPT = ".json,.jsonl,.ndjson";

export function filterJsonUploadFiles(files: File[]): File[] {
  return files.filter((file) =>
    JSON_UPLOAD_SUFFIXES.some((suffix) => file.name.toLowerCase().endsWith(suffix)),
  );
}

export function isTextWorkspaceFile(name: string): boolean {
  return TEXT_EXTENSIONS.some((ext) => name.toLowerCase().endsWith(ext));
}
