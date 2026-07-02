import type { FileNode } from "./types";

export function collectDirectoryPaths(nodes: FileNode[]): Set<string> {
  const out = new Set<string>();
  const walk = (list: FileNode[]) => {
    for (const node of list) {
      if (node.type === "directory") {
        out.add(node.path);
        if (node.children?.length) walk(node.children);
      }
    }
  };
  walk(nodes);
  return out;
}
