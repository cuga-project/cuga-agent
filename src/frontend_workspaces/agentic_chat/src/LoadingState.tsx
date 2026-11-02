import React, { useCallback, useEffect, useRef } from "react";
import { Button, DataTableSkeleton, InlineLoading, SkeletonText, Tile } from "@carbon/react";
import "./LoadingState.css";

/** Keep the expected content geometry while data is fetched. Skeletons are
 * decorative; announce the region once rather than every animated line. */
export function ContentSkeleton({ label, kind = "list", rows = 3, showHeader = true }: {
  label: string;
  kind?: "list" | "cards" | "table" | "chat";
  rows?: number;
  showHeader?: boolean;
}) {
  return <div className={`cuga-content-skeleton cuga-content-skeleton--${kind}`} role="status" aria-label={label}>
    <span className="cds--visually-hidden">{label}</span>
    <div aria-hidden="true" className="cuga-content-skeleton__content">
      {kind === "table" ? <DataTableSkeleton rowCount={rows} columnCount={4} showHeader={false} showToolbar={false} />
        : kind === "chat" ? <>
          {showHeader && <div className="cuga-content-skeleton__chat-header"><SkeletonText width="12rem" /></div>}
          <div className="cuga-content-skeleton__chat-body"><SkeletonText heading width="65%" /><SkeletonText paragraph lineCount={2} /></div>
          <div className="cuga-content-skeleton__composer"><SkeletonText width="40%" /></div>
        </> : Array.from({ length: rows }, (_, index) => kind === "cards"
          ? <Tile key={index}><SkeletonText heading width="70%" /><SkeletonText paragraph lineCount={2} /><SkeletonText width="40%" /></Tile>
          : <div className="cuga-content-skeleton__row" key={index}><SkeletonText width="65%" /><SkeletonText width="40%" /></div>)}
    </div>
  </div>;
}

/** Carbon inline loading replaces the action visually while its original
 * button reserves space. The disabled button also prevents duplicate requests. */
export function AsyncButton({ busy, busyLabel, children, disabled, className, ...props }:
  Omit<React.ComponentProps<typeof Button>, "ref"> & { busy: boolean; busyLabel: string }) {
  const container = useRef<HTMLSpanElement>(null);
  const restoreFocus = useRef(false);
  const wasBusy = useRef(false);
  useEffect(() => {
    if (!busy && wasBusy.current && restoreFocus.current && document.activeElement === document.body) {
      container.current?.querySelector<HTMLButtonElement>("button")?.focus();
    }
    wasBusy.current = busy;
  }, [busy]);
  return <span ref={container} onFocusCapture={() => { restoreFocus.current = true; }}
    onBlurCapture={(event) => { if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node)) restoreFocus.current = false; }}
    className={`cuga-async-action ${busy ? "cuga-async-action--busy" : ""} ${className ?? ""}`}>
    <Button {...props} className={className} disabled={busy || disabled} aria-hidden={busy || undefined} tabIndex={busy ? -1 : props.tabIndex}>{children}</Button>
    {busy && <InlineLoading description={busyLabel} aria-live="polite" className="cuga-async-action__status" />}
  </span>;
}

/** Carbon installs its Escape listener when the modal opens. Keep pending state
 * current even when that listener retains the first close callback. */
export function useModalClose(busy: boolean, onClose: () => void) {
  const state = useRef({ busy, onClose });
  state.current = { busy, onClose };
  return useCallback(() => {
    if (state.current.busy) return false;
    state.current.onClose();
  }, []);
}
