import React from "react";
import { ContainedList, ContainedListItem, Tag, Tooltip, Button, Link } from "@carbon/react";
import { Information } from "@carbon/icons-react";

export interface EnvPreset {
  id: string;
  label: string;
  default_provider: string;
  default_model: string;
  ready: boolean;
  env_vars: Record<string, boolean>;
  missing: string[];
}

interface Props {
  presets: EnvPreset[];
  currentProvider: string;
  currentModel: string;
  onApply: (preset: EnvPreset) => void;
  onFocusProviderSelect?: () => void;
}

function providerMonogram(id: string): string {
  switch (id) {
    case "openai":
      return "OA";
    case "openrouter":
      return "OR";
    case "watsonx":
      return "Wx";
    case "azure":
      return "Az";
    case "cohere":
      return "Co";
    default:
      return id.slice(0, 2).toUpperCase();
  }
}

function providerCategory(id: string): "Cloud" | "Enterprise" {
  return id === "watsonx" || id === "azure" ? "Enterprise" : "Cloud";
}

function isPresetActive(preset: EnvPreset, currentProvider: string, currentModel: string): boolean {
  if (preset.default_provider !== currentProvider) return false;
  if (preset.default_provider !== "litellm") return true;
  const presetPrefix = preset.default_model.split("/")[0];
  const currentPrefix = (currentModel || "").split("/")[0];
  return presetPrefix === currentPrefix;
}

function Monogram({ id }: { id: string }) {
  return (
    <span
      aria-hidden="true"
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 24,
        height: 24,
        background: "var(--cds-layer-02)",
        color: "var(--cds-text-primary)",
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: 0.4,
        borderRadius: 2,
      }}
    >
      {providerMonogram(id)}
    </span>
  );
}

function InfoTooltip({ label }: { label: string }) {
  return (
    <Tooltip label={label} align="top">
      <button
        type="button"
        style={{
          background: "none",
          border: "none",
          padding: 0,
          marginLeft: 6,
          cursor: "help",
          display: "inline-flex",
          color: "var(--cds-icon-secondary)",
        }}
        aria-label={label}
      >
        <Information size={14} />
      </button>
    </Tooltip>
  );
}

export function EnvPresetsPanel({
  presets,
  currentProvider,
  currentModel,
  onApply,
  onFocusProviderSelect,
}: Props) {
  // Render nothing when NO preset has any env signal. Locals never
  // appear here — they live in the Provider Select; bottom Link
  // points users there.
  const rows = presets.filter((p) => Object.values(p.env_vars).some(Boolean));
  if (rows.length === 0) return null;

  return (
    <div style={{ marginBottom: "1rem" }}>
      <ContainedList
        size="sm"
        kind="on-page"
        label={
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            Detected in your environment
            <InfoTooltip label="Providers found in your .env or shell. One click sets the provider; credentials stay on this machine." />
          </span>
        }
      >
        {rows.map((preset) => {
          const active = isPresetActive(preset, currentProvider, currentModel);
          const category = providerCategory(preset.id);

          let actionSlot: React.ReactNode;
          if (active) {
            actionSlot = (
              <Tag type="blue" size="sm">
                Active
              </Tag>
            );
          } else if (preset.ready) {
            actionSlot = (
              <Button kind="ghost" size="sm" onClick={() => onApply(preset)}>
                Use
              </Button>
            );
          } else {
            const missingText = `Missing: ${preset.missing.join(", ")}`;
            actionSlot = (
              <Tooltip label={missingText} align="left">
                <button
                  type="button"
                  style={{ background: "none", border: "none", padding: 0, cursor: "help" }}
                  aria-label={missingText}
                >
                  <Tag type="gray" size="sm">
                    Set up
                  </Tag>
                </button>
              </Tooltip>
            );
          }

          return (
            <ContainedListItem
              key={preset.id}
              renderIcon={() => <Monogram id={preset.id} />}
              action={
                <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                  <Tag type={category === "Enterprise" ? "purple" : "outline"} size="sm">
                    {category}
                  </Tag>
                  {actionSlot}
                </span>
              }
            >
              <span style={{ display: "inline-flex", alignItems: "center" }}>
                <span style={{ fontWeight: 500 }}>{preset.label.replace(" (via LiteLLM)", "")}</span>
                <InfoTooltip label={`Default model: ${preset.default_model}`} />
              </span>
            </ContainedListItem>
          );
        })}
      </ContainedList>
      {onFocusProviderSelect && (
        <div style={{ marginTop: "0.5rem", fontSize: "0.75rem" }}>
          <Link
            href="#"
            onClick={(e: React.MouseEvent) => {
              e.preventDefault();
              onFocusProviderSelect();
            }}
          >
            Or run locally with Fastembed or Ollama — no keys needed.
          </Link>
        </div>
      )}
    </div>
  );
}
