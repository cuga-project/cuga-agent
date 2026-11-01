import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  Button, ComposedModal, ModalHeader, ModalBody, ModalFooter,
  TextInput, PasswordInput, ToastNotification,
} from "@carbon/react";

type NoticeKind = "info" | "success" | "error";
type DialogRequest =
  | { kind: "confirm"; message: string; actionLabel: string }
  | { kind: "prompt"; message: string; secret: boolean }; // pragma: allowlist secret -- Boolean input-mask flag.
interface Feedback {
  confirm: (message: string, actionLabel: string) => Promise<boolean>;
  prompt: (message: string, secret?: boolean, defaultValue?: string) => Promise<string | null>;
  notify: (message: string, kind?: NoticeKind) => void;
  isOpen: boolean;
}
const FeedbackContext = createContext<Feedback | null>(null);

export function CarbonFeedbackProvider({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation();
  const [request, setRequest] = useState<DialogRequest | null>(null);
  const [value, setValue] = useState("");
  const [notices, setNotices] = useState<Array<{ id: number; message: string; kind: NoticeKind }>>([]);
  const sequence = useRef(0);
  const resolveRef = useRef<((result: boolean | string | null) => void) | null>(null);
  const originRoute = useRef(pathname);
  const openerRef = useRef<HTMLElement | null>(null);

  const settle = useCallback((result: boolean | string | null) => {
    const resolve = resolveRef.current;
    if (!resolve) return;
    resolveRef.current = null;
    setRequest(null);
    resolve(result);
    openerRef.current?.focus();
  }, []);

  // A pending action belongs to the page that opened it.
  useEffect(() => {
    if (originRoute.current !== pathname) settle(null);
    originRoute.current = pathname;
  }, [pathname, settle]);
  useEffect(() => () => { resolveRef.current?.(null); resolveRef.current = null; }, []);

  const confirm = useCallback((message: string, actionLabel: string) => {
    if (resolveRef.current) return Promise.resolve(false);
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    return new Promise<boolean>((resolve) => {
      resolveRef.current = (result) => resolve(result === true);
      setRequest({ kind: "confirm", message, actionLabel });
    });
  }, []);
  const prompt = useCallback((message: string, secret = false, defaultValue = "") => {
    if (resolveRef.current) return Promise.resolve(null);
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    return new Promise<string | null>((resolve) => {
      resolveRef.current = (result) => resolve(typeof result === "string" ? result : null);
      setValue(defaultValue);
      setRequest({ kind: "prompt", message, secret });
    });
  }, []);
  const notify = useCallback((message: string, kind: NoticeKind = "info") => {
    const id = ++sequence.current;
    setNotices((current) => [...current.slice(-2), { id, message, kind }]);
  }, []);

  const submit = () => settle(request?.kind === "prompt" ? value : true);
  return (
    <FeedbackContext.Provider value={{ confirm, prompt, notify, isOpen: request !== null }}>
      {children}
      <ComposedModal aria-label={request?.kind === "confirm" ? "Confirm action" : "Enter value"}
        aria-describedby={request?.kind === "confirm" ? "carbon-feedback-message" : undefined}
        open={request !== null} size="sm" onClose={() => settle(null)}
        preventCloseOnClickOutside selectorPrimaryFocus={request?.kind === "prompt" ? "#carbon-feedback-value" : undefined}>
        <ModalHeader title={request?.kind === "confirm" ? "Confirm action" : "Enter value"} />
        <ModalBody>
          {request?.kind === "confirm" ? <p id="carbon-feedback-message">{request.message}</p> : request?.kind === "prompt" && (
            <form id="carbon-feedback-form" onSubmit={(event) => { event.preventDefault(); submit(); }}>
              {request.secret ? <PasswordInput id="carbon-feedback-value" labelText={request.message}
                value={value} onChange={(event) => setValue(event.target.value)} autoComplete="off" />
                : <TextInput id="carbon-feedback-value" labelText={request.message}
                  value={value} onChange={(event) => setValue(event.target.value)} autoComplete="off" />}
            </form>
          )}
        </ModalBody>
        <ModalFooter>
          <Button kind="secondary" onClick={() => settle(null)}>Cancel</Button>
          <Button kind={request?.kind === "confirm" ? "danger" : "primary"} onClick={submit}>
            {request?.kind === "confirm" ? request.actionLabel : "Continue"}
          </Button>
        </ModalFooter>
      </ComposedModal>
      <div className="cuga-feedback-notices" aria-label="Notifications">
        {notices.map((notice) => <ToastNotification key={notice.id} kind={notice.kind}
          title={notice.kind === "error" ? "Error" : notice.kind === "success" ? "Success" : "Information"}
          subtitle={notice.message} lowContrast timeout={10000}
          onClose={() => setNotices((current) => current.filter((item) => item.id !== notice.id))} />)}
      </div>
    </FeedbackContext.Provider>
  );
}

export function useCarbonFeedback(): Feedback {
  const feedback = useContext(FeedbackContext);
  if (!feedback) throw new Error("useCarbonFeedback must be used within CarbonFeedbackProvider");
  return feedback;
}
