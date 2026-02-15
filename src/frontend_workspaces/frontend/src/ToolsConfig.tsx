import React, { useState } from "react";
import { Wrench, Plus, Pencil, Trash2 } from "lucide-react";
import type { ToolEntry } from "./types/tools";
import { AddToolModal } from "./AddToolModal";
import "./ToolsConfig.css";

interface ToolsConfigProps {
  tools: ToolEntry[];
  onChange: (tools: ToolEntry[]) => void;
}

export function ToolsConfig({ tools, onChange }: ToolsConfigProps) {
  const [modalOpen, setModalOpen] = useState(false);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);

  const handleAdd = (tool: ToolEntry) => {
    onChange([...tools, tool]);
    setModalOpen(false);
  };

  const handleEdit = (tool: ToolEntry) => {
    if (editingIndex === null) return;
    const next = [...tools];
    next[editingIndex] = tool;
    onChange(next);
    setEditingIndex(null);
  };

  const handleRemove = (index: number) => {
    onChange(tools.filter((_, i) => i !== index));
  };

  const editingTool = editingIndex !== null ? tools[editingIndex] ?? null : null;

  return (
    <section className="manage-section tools-config-section">
      <h3>
        <Wrench size={14} style={{ verticalAlign: "middle", marginRight: 6 }} />
        Tools
      </h3>
      <div className="tools-config-list">
        {tools.length === 0 ? (
          <div className="tools-config-empty">No tools added yet.</div>
        ) : (
          tools.map((t, i) => (
            <div key={i} className="tools-config-card">
              <div className="tools-config-card-main">
                <span className="tools-config-name">{t.name || (t.type === "mcp" ? "MCP" : "OpenAPI")}</span>
                <span className={`tools-config-badge tools-config-badge-${t.type}`}>
                  {t.type === "mcp" ? "MCP" : "OpenAPI"}
                </span>
              </div>
              {t.url && (
                <div className="tools-config-url" title={t.url}>
                  {t.url}
                </div>
              )}
              {t.auth?.type && t.auth.type !== "none" && (
                <div className="tools-config-auth-hint">Auth: {t.auth.type}</div>
              )}
              <div className="tools-config-actions">
                <button
                  type="button"
                  className="tools-config-action-btn"
                  onClick={() => setEditingIndex(i)}
                  title="Edit"
                >
                  <Pencil size={14} />
                </button>
                <button
                  type="button"
                  className="tools-config-action-btn tools-config-action-btn-danger"
                  onClick={() => handleRemove(i)}
                  title="Remove"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))
        )}
      </div>
      <button type="button" className="tools-config-add-btn" onClick={() => setModalOpen(true)}>
        <Plus size={14} />
        Add tool
      </button>

      {modalOpen && (
        <AddToolModal
          onClose={() => setModalOpen(false)}
          onSave={handleAdd}
          initial={null}
        />
      )}
      {editingIndex !== null && (
        <AddToolModal
          onClose={() => setEditingIndex(null)}
          onSave={handleEdit}
          initial={editingTool}
        />
      )}
    </section>
  );
}
