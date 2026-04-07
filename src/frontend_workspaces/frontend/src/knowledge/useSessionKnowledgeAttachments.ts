import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  deleteKnowledgeDocument,
  deleteSessionKnowledgeDocument,
  listKnowledgeDocuments,
  listSessionKnowledgeDocuments,
  uploadKnowledgeDocuments,
  uploadSessionKnowledgeDocuments,
} from "../api";

export type KnowledgeAttachmentScope = "session" | "agent";

export interface KnowledgeAttachmentSnapshot {
  knowledge_filename: string;
  display_name: string;
  mime_type?: string;
  size_bytes?: number;
  scope: KnowledgeAttachmentScope;
}

export type SessionAttachmentSnapshot = KnowledgeAttachmentSnapshot;

interface SessionKnowledgeDocumentResponse {
  filename: string;
  chunk_count?: number;
  status?: string;
  ingested_at?: string;
}

interface SessionKnowledgeDocumentMeta {
  displayName: string;
  mimeType?: string;
  sizeBytes?: number;
}

interface SessionKnowledgeDocument extends KnowledgeAttachmentSnapshot {
  chunk_count?: number;
  status?: string;
  ingested_at?: string;
}

interface UploadTaskResponse {
  filename?: string;
  status?: string;
  file_tasks?: Record<string, { error?: string }>;
  tasks?: UploadTaskResponse[];
}

type UploadStatus = "uploading" | "error";

interface PendingUpload {
  id: string;
  file: File;
  displayName: string;
  mimeType?: string;
  sizeBytes?: number;
  status: UploadStatus;
  error?: string;
}

export interface SessionAttachmentItem {
  id: string;
  kind: "document" | "upload";
  status: "ready" | "uploading" | "error" | "deleting";
  knowledgeFilename?: string;
  displayName: string;
  mimeType?: string;
  sizeBytes?: number;
  chunkCount?: number;
  ingestedAt?: string;
  error?: string;
}

interface UseSessionKnowledgeAttachmentsOptions {
  threadId?: string | null;
  scope?: KnowledgeAttachmentScope;
  enabled?: boolean;
  sessionDocsVersion?: number;
  onSessionDocsChanged?: () => void;
  visibleDocumentMode?: "all" | "tracked";
}

const MIME_BY_EXTENSION: Record<string, string> = {
  csv: "text/csv",
  html: "text/html",
  json: "application/json",
  md: "text/markdown",
  pdf: "application/pdf",
  py: "text/x-python",
  txt: "text/plain",
  xml: "application/xml",
  yaml: "application/yaml",
  yml: "application/yaml",
};

function getExtension(filename: string): string {
  const parts = filename.toLowerCase().split(".");
  return parts.length > 1 ? parts[parts.length - 1] : "";
}

function guessMimeType(filename: string, fallback?: string): string | undefined {
  if (fallback) {
    return fallback;
  }
  return MIME_BY_EXTENSION[getExtension(filename)];
}

function toSessionAttachmentSnapshot(
  doc: SessionKnowledgeDocumentResponse,
  metadata?: SessionKnowledgeDocumentMeta,
  scope: KnowledgeAttachmentScope = "session",
): SessionKnowledgeDocument {
  return {
    knowledge_filename: doc.filename,
    display_name: metadata?.displayName ?? doc.filename,
    mime_type: guessMimeType(doc.filename, metadata?.mimeType),
    size_bytes: metadata?.sizeBytes,
    scope,
    chunk_count: doc.chunk_count,
    status: doc.status,
    ingested_at: doc.ingested_at,
  };
}

function createUploadId(file: File): string {
  return `${file.name}-${file.lastModified}-${Math.random().toString(36).slice(2, 10)}`;
}

function getUploadError(task: UploadTaskResponse | undefined): string {
  const fileInfo = task?.file_tasks ? Object.values(task.file_tasks)[0] : undefined;
  return fileInfo?.error || "Ingestion failed";
}

export function useSessionKnowledgeAttachments({
  threadId,
  scope = "session",
  enabled = true,
  sessionDocsVersion = 0,
  onSessionDocsChanged,
  visibleDocumentMode = "all",
}: UseSessionKnowledgeAttachmentsOptions) {
  const [documents, setDocuments] = useState<SessionKnowledgeDocument[]>([]);
  const [pendingUploads, setPendingUploads] = useState<PendingUpload[]>([]);
  const [deletingFilenames, setDeletingFilenames] = useState<Set<string>>(new Set());
  const [trackedDocumentFilenames, setTrackedDocumentFilenames] = useState<Set<string>>(new Set());
  const metadataByFilenameRef = useRef<Map<string, SessionKnowledgeDocumentMeta>>(new Map());
  const threadIdRef = useRef(threadId);
  threadIdRef.current = threadId;
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const isAvailable = enabled && (scope === "agent" || Boolean(threadId));

  useEffect(() => {
    setDocuments([]);
    setPendingUploads([]);
    setDeletingFilenames(new Set());
    setTrackedDocumentFilenames(new Set());
    metadataByFilenameRef.current = new Map();
  }, [enabled, scope, threadId]);

  const refreshDocuments = useCallback(async () => {
    if (!enabled) {
      setDocuments([]);
      return;
    }
    const tid = threadIdRef.current;
    const currentScope = scopeRef.current;
    if (currentScope === "session" && !tid) {
      setDocuments([]);
      return;
    }

    try {
      const response =
        currentScope === "agent"
          ? await listKnowledgeDocuments()
          : await listSessionKnowledgeDocuments(tid!);
      if (!response.ok) {
        setDocuments([]);
        return;
      }

      const data = await response.json();
      const nextDocuments = ((data.documents ?? []) as SessionKnowledgeDocumentResponse[]).map((doc) =>
        toSessionAttachmentSnapshot(
          doc,
          metadataByFilenameRef.current.get(doc.filename),
          currentScope,
        ),
      );
      setDocuments(nextDocuments);
      setTrackedDocumentFilenames((current) => {
        if (current.size === 0) {
          return current;
        }
        const available = new Set(nextDocuments.map((doc) => doc.knowledge_filename));
        const next = new Set(
          Array.from(current).filter((filename) => available.has(filename)),
        );
        return next.size === current.size ? current : next;
      });
    } catch (error) {
      console.error("Failed to refresh session knowledge documents:", error);
      setDocuments([]);
    }
  }, [enabled]);

  useEffect(() => {
    void refreshDocuments();
  }, [refreshDocuments, sessionDocsVersion, threadId, scope]);

  const uploadSingleFile = useCallback(
    async (upload: PendingUpload) => {
      const tid = threadIdRef.current;
      const currentScope = scopeRef.current;
      if (currentScope === "session" && !tid) {
        throw new Error("Session attachments are unavailable.");
      }

      setPendingUploads((current) =>
        current.map((item) =>
          item.id === upload.id ? { ...item, status: "uploading", error: undefined } : item,
        ),
      );

      const response =
        currentScope === "agent"
          ? await uploadKnowledgeDocuments([upload.file], true)
          : await uploadSessionKnowledgeDocuments(tid!, [upload.file], true);
      if (!response.ok) {
        const detail = await response.text().catch(() => "Upload failed");
        throw new Error(detail || "Upload failed");
      }

      const data = (await response.json().catch(() => ({}))) as UploadTaskResponse;
      const task = data?.tasks?.[0] ?? data;
      if (task?.status && task.status !== "completed") {
        throw new Error(getUploadError(task));
      }

      const canonicalFilename =
        typeof task?.filename === "string" && task.filename
          ? task.filename
          : upload.file.name;

      metadataByFilenameRef.current.set(canonicalFilename, {
        displayName: upload.displayName,
        mimeType: upload.mimeType,
        sizeBytes: upload.sizeBytes,
      });
      setDocuments((current) => {
        const nextDocument = toSessionAttachmentSnapshot(
          {
            filename: canonicalFilename,
            status: "completed",
          },
          {
            displayName: upload.displayName,
            mimeType: upload.mimeType,
            sizeBytes: upload.sizeBytes,
          },
          currentScope,
        );
        const withoutExisting = current.filter(
          (doc) => doc.knowledge_filename !== canonicalFilename,
        );
        return [...withoutExisting, nextDocument];
      });
      setTrackedDocumentFilenames((current) => new Set(current).add(canonicalFilename));

      setPendingUploads((current) => current.filter((item) => item.id !== upload.id));
      return canonicalFilename;
    },
    [],
  );

  const uploadFiles = useCallback(
    async (files: File[]) => {
      if (!isAvailable || files.length === 0) {
        return;
      }

      const uploads = files.map((file) => ({
        id: createUploadId(file),
        file,
        displayName: file.name,
        mimeType: guessMimeType(file.name, file.type),
        sizeBytes: file.size,
        status: "uploading" as const,
      }));

      setPendingUploads((current) => [...current, ...uploads]);

      const results = await Promise.allSettled(
        uploads.map(async (upload) => {
          try {
            await uploadSingleFile(upload);
            return true;
          } catch (error) {
            const message = error instanceof Error ? error.message : "Upload failed";
            setPendingUploads((current) =>
              current.map((item) =>
                item.id === upload.id ? { ...item, status: "error", error: message } : item,
              ),
            );
            return false;
          }
        }),
      );

      const uploadedAny = results.some(
        (result) => result.status === "fulfilled" && result.value === true,
      );
      await refreshDocuments();
      if (uploadedAny) {
        onSessionDocsChanged?.();
      }
    },
    [isAvailable, onSessionDocsChanged, refreshDocuments, uploadSingleFile],
  );

  const retryUpload = useCallback(
    async (uploadId: string) => {
      const upload = pendingUploads.find((item) => item.id === uploadId);
      if (!upload) {
        return;
      }

      try {
        await uploadSingleFile(upload);
        await refreshDocuments();
        onSessionDocsChanged?.();
      } catch (error) {
        const message = error instanceof Error ? error.message : "Upload failed";
        setPendingUploads((current) =>
          current.map((item) =>
            item.id === uploadId ? { ...item, status: "error", error: message } : item,
          ),
        );
      }
    },
    [onSessionDocsChanged, pendingUploads, refreshDocuments, uploadSingleFile],
  );

  const dismissUpload = useCallback((uploadId: string) => {
    setPendingUploads((current) => current.filter((item) => item.id !== uploadId));
  }, []);

  const deleteDocument = useCallback(
    async (knowledgeFilename: string) => {
      const tid = threadIdRef.current;
      const currentScope = scopeRef.current;
      if (currentScope === "session" && !tid) {
        return;
      }

      setDeletingFilenames((current) => new Set(current).add(knowledgeFilename));
      try {
        const response =
          currentScope === "agent"
            ? await deleteKnowledgeDocument(knowledgeFilename)
            : await deleteSessionKnowledgeDocument(tid!, knowledgeFilename);
        if (!response.ok) {
          const detail = await response.text().catch(() => "Delete failed");
          throw new Error(detail || "Delete failed");
        }

        metadataByFilenameRef.current.delete(knowledgeFilename);
        setTrackedDocumentFilenames((current) => {
          const next = new Set(current);
          next.delete(knowledgeFilename);
          return next;
        });
        setDocuments((current) =>
          current.filter((doc) => doc.knowledge_filename !== knowledgeFilename),
        );
        onSessionDocsChanged?.();
        await refreshDocuments();
      } finally {
        setDeletingFilenames((current) => {
          const next = new Set(current);
          next.delete(knowledgeFilename);
          return next;
        });
      }
    },
    [onSessionDocsChanged, refreshDocuments],
  );

  const attachmentItems = useMemo<SessionAttachmentItem[]>(() => {
    const uploads = pendingUploads.map((upload) => ({
      id: upload.id,
      kind: "upload" as const,
      status: upload.status,
      displayName: upload.displayName,
      mimeType: upload.mimeType,
      sizeBytes: upload.sizeBytes,
      error: upload.error,
    }));

    const visibleDocuments =
      visibleDocumentMode === "tracked"
        ? documents.filter((doc) => trackedDocumentFilenames.has(doc.knowledge_filename))
        : documents;

    const docs = visibleDocuments.map((doc) => ({
      id: doc.knowledge_filename,
      kind: "document" as const,
      status: deletingFilenames.has(doc.knowledge_filename) ? "deleting" as const : "ready" as const,
      knowledgeFilename: doc.knowledge_filename,
      displayName: doc.display_name,
      mimeType: doc.mime_type,
      sizeBytes: doc.size_bytes,
      chunkCount: doc.chunk_count,
      ingestedAt: doc.ingested_at,
    }));

    return [...uploads, ...docs];
  }, [deletingFilenames, documents, pendingUploads, trackedDocumentFilenames, visibleDocumentMode]);

  const createMessageAttachmentSnapshot = useCallback(
    (): KnowledgeAttachmentSnapshot[] =>
      documents.map((doc) => ({
        knowledge_filename: doc.knowledge_filename,
        display_name: doc.display_name,
        mime_type: doc.mime_type,
        size_bytes: doc.size_bytes,
        scope: doc.scope,
      })),
    [documents],
  );

  return {
    documents,
    attachmentItems,
    hasAnyDocuments: documents.length > 0,
    isAvailable,
    isUploading: pendingUploads.some((item) => item.status === "uploading"),
    uploadFiles,
    retryUpload,
    dismissUpload,
    deleteDocument,
    refreshDocuments,
    createMessageAttachmentSnapshot,
  };
}
