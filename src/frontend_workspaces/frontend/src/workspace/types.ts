export interface FileNode {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: FileNode[];
}

export interface WorkspaceData {
  tree: FileNode[];
  timestamp: number;
}

export interface WorkspaceFilePreview {
  path: string;
  content: string;
  name: string;
}

export type WorkspaceNotify = (
  kind: "success" | "error" | "warning" | "info",
  title: string,
  subtitle?: string,
) => void;
