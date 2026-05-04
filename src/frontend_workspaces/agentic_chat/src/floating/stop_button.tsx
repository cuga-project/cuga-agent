// StopButton.tsx
import React, { useState, useEffect } from "react";
import { streamStateManager } from "../StreamManager";
import "../WriteableElementExample.css";
interface StopButtonProps {
  location?: "sidebar" | "inline";
}

export const StopButton: React.FC<StopButtonProps> = ({ location = "sidebar" }) => {
  const [isProcessing, setIsProcessing] = useState(() => streamStateManager.getIsProcessing());

  useEffect(() => {
    const unsubscribe = streamStateManager.subscribe(setIsProcessing);
    return unsubscribe;
  }, []);

  const handleStop = async () => {
    await streamStateManager.stopStream();
    if (typeof window !== "undefined" && (window as any).aiSystemInterface) {
      try {
        (window as any).aiSystemInterface.stopProcessing?.();
        (window as any).aiSystemInterface.setProcessingComplete?.(true);
      } catch (e) {
        // noop
      }
    }
  };

  if (!isProcessing) {
    return null;
  }

  const isInline = location === "inline";
  
  return (
    <div className="floating-controls-container">
      <button
        onClick={handleStop}
        className={isInline ? "stop-button-inline" : "stop-button-floating"}
        style={{
          color: isInline ? "white" : "black",
          border: isInline ? "none" : "#c6c6c6 solid 1px",
          backgroundColor: isInline ? "#ef4444" : "white",
          marginLeft: "auto",
          marginRight: "auto",
          opacity: isInline ? "1" : "0.6",
          fontWeight: "500",
          borderRadius: isInline ? "8px" : "4px",
          marginBottom: isInline ? "0" : "6px",
          padding: isInline ? "8px 12px" : "8px 16px",
          cursor: "pointer",
          fontSize: isInline ? "13px" : "14px",
          display: "flex",
          alignItems: "center",
          gap: "6px",
          transition: "all 0.2s ease",
          flexShrink: 0,
        }}
        onMouseOver={(e) => {
          if (isInline) {
            e.currentTarget.style.backgroundColor = "#dc2626";
            e.currentTarget.style.transform = "scale(1.05)";
          } else {
            e.currentTarget.style.backgroundColor = "black";
            e.currentTarget.style.color = "white";
            e.currentTarget.style.opacity = "1";
          }
        }}
        onMouseOut={(e) => {
          if (isInline) {
            e.currentTarget.style.backgroundColor = "#ef4444";
            e.currentTarget.style.transform = "scale(1)";
          } else {
            e.currentTarget.style.backgroundColor = "";
            e.currentTarget.style.color = "black";
            e.currentTarget.style.opacity = "0.6";
          }
        }}
      >
        {isInline ? "Stop" : "Stop Processing"}
      </button>
    </div>
  );
};
