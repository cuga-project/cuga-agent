import React from "react";
import { ContainedList, ContainedListItem, Tag, Tooltip, Button, Link } from "@carbon/react";
import { Information, Checkmark } from "@carbon/icons-react";

export interface EnvPreset {
  id: string;
  label: string;
  default_provider: string;
  default_model: string;
  ready: boolean;
  env_vars: Record<string, boolean>;
  // Non-secret detected values (URL / project ID / region / etc.).
  // Credential material (KEY / APIKEY / TOKEN / SECRET / PASSWORD)
  // is filtered server-side and NEVER reaches the client.
  env_values?: Record<string, string>;
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
    case "gemini":
      return "Gm";
    case "voyage":
      return "Vy";
    case "mistral":
      return "Ms";
    case "togetherai":
      return "Tg";
    case "jina":
      return "Ja";
    default:
      return id.slice(0, 2).toUpperCase();
  }
}

function providerCategory(id: string): "Cloud" | "Enterprise" {
  return id === "watsonx" || id === "azure" ? "Enterprise" : "Cloud";
}

// Human-readable label for env var keys shown in the detection tooltip.
// Keeps the tooltip body readable instead of dumping raw ALL_CAPS names.
function envKeyLabel(name: string): string {
  const map: Record<string, string> = {
    WATSONX_URL: "URL",
    WATSONX_API_BASE: "URL",
    WATSONX_PROJECT_ID: "Project ID",
    AZURE_API_BASE: "Endpoint",
    AZURE_API_VERSION: "API version",
    OPENAI_BASE_URL: "Base URL",
  };
  return map[name] || name;
}

// Truncate long values (e.g., UUIDs, URLs) for compact tooltip display.
function truncate(s: string, max = 48): string {
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`;
}

function isPresetActive(preset: EnvPreset, currentProvider: string, currentModel: string): boolean {
  if (preset.default_provider !== currentProvider) return false;
  // For multi-preset providers (litellm hosts watsonx/azure/cohere/...)
  // distinguish by model prefix. A user who switches the specific
  // watsonx model (e.g. ibm/slate -> intfloat/e5) is still on the
  // Watsonx preset's creds, so prefix match keeps "Active" stable.
  if (preset.default_provider === "litellm") {
    return preset.default_model.split("/")[0] === (currentModel || "").split("/")[0];
  }
  // Single-preset providers (openai, openrouter): require exact model
  // match. Otherwise a user who picks "openai" via the Provider Select
  // and types a custom model (e.g. "text-embedding-ada-002") would
  // see a misleading "Active" indicator pointing at a preset they
  // never actually applied.
  return preset.default_model === currentModel;
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

// Per-row info tooltip: combines the default model with every non-secret
// detected env value (URL, project ID, region, etc.) into a single
// hover-revealed block. Secrets are filtered server-side and never reach
// this component, so this is always safe to render verbatim.
function RowInfoTooltip({
  model,
  envValues,
}: {
  model: string;
  envValues?: Record<string, string>;
}) {
  const entries = Object.entries(envValues || {});
  // Build a plain-text aria-label so screen readers + the Carbon tooltip
  // body share one source of truth.
  const plainLines = [
    `Default model: ${model}`,
    ...entries.map(([k, v]) => `${envKeyLabel(k)}: ${truncate(v)}`),
  ];
  const ariaLabel = plainLines.join(" — ");
  const body = (
    <div style={{ display: "grid", rowGap: 2, fontSize: 12, lineHeight: 1.4 }}>
      <div>
        <strong>Model:</strong> {model}
      </div>
      {entries.map(([k, v]) => (
        <div key={k}>
          <strong>{envKeyLabel(k)}:</strong> {truncate(v)}
        </div>
      ))}
    </div>
  );
  return (
    <Tooltip label={body} align="top">
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
        aria-label={ariaLabel}
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
            // green + Checkmark per the Carbon-designer review: success
            // semantics over neutral-blue, matches the rest of the app's
            // "this is the current state" tag color (Saved pill is also
            // green + Checkmark — consistent vocabulary).
            actionSlot = (
              <Tag type="green" size="sm" renderIcon={Checkmark}>
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
                  {/* Outline for both Cloud + Enterprise — the category
                      text earns its place (signals deployment context),
                      but coloring it purple-vs-outline was category soup
                      that fought the action-color vocabulary (green/red
                      = state, blue = primary action). Single outline keeps
                      the info without competing for attention. */}
                  <Tag type="outline" size="sm">
                    {category}
                  </Tag>
                  {actionSlot}
                </span>
              }
            >
              <span style={{ display: "inline-flex", alignItems: "center" }}>
                <span style={{ fontWeight: 500 }}>{preset.label.replace(" (via LiteLLM)", "")}</span>
                <RowInfoTooltip model={preset.default_model} envValues={preset.env_values} />
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
