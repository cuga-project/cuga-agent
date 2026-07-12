import React, { useState, useEffect } from "react";
import { HelpCircle, X } from "lucide-react";
import { GuidedTour, TourStep } from "./GuidedTour";
import "./AdvancedTourButton.css";

interface AdvancedTourButtonProps {
  onStartTour?: () => void;
}

export function AdvancedTourButton({ onStartTour }: AdvancedTourButtonProps) {
  const [isTourActive, setIsTourActive] = useState(false);
  const [showMenu, setShowMenu] = useState(false);
  const [currentTourSteps, setCurrentTourSteps] = useState<TourStep[]>([]);
  const [showHint, setShowHint] = useState(true);

  // Hide hint after a few seconds or when user interacts
  useEffect(() => {
    const timer = setTimeout(() => setShowHint(false), 8000);
    return () => clearTimeout(timer);
  }, []);

  const welcomeTourSteps: TourStep[] = [
    {
      target: ".welcome-title",
      title: "Welcome to CUGA!",
      content: "CUGA is an intelligent digital agent that autonomously executes complex tasks through multi-agent orchestration, API integration, and code generation.",
      placement: "bottom",
      highlightPadding: 12,
    },
    {
      target: "#main-input_field",
      title: "Chat Input",
      content: "Type your requests here. You can ask CUGA to manage contacts, read files, send emails, or perform any complex task.",
      placement: "top",
      highlightPadding: 10,
    },
    {
      target: "#main-input_field",
      title: "File Tagging with @",
      content: "Type @ followed by a file name to tag files in your message. This allows CUGA to access and work with specific files from your workspace.",
      placement: "top",
      highlightPadding: 10,
    },
    {
      target: ".example-utterances-widget",
      title: "Try Example Queries",
      content: "Click any of these example queries to get started quickly. These demonstrate the types of tasks CUGA can handle.",
      placement: "top",
      highlightPadding: 12,
    },
    {
      target: ".welcome-features",
      title: "Key Features",
      content: "CUGA offers multi-agent coordination, secure code execution, API integration, and smart memory to handle complex workflows.",
      placement: "top",
      highlightPadding: 12,
    },
  ];

  const chatTourSteps: TourStep[] = [
    {
      target: ".custom-chat-header",
      title: "Chat Header",
      content: "See your active conversation with CUGA here. Use the restart button to begin a new conversation.",
      placement: "bottom",
      highlightPadding: 10,
    },
    {
      target: ".custom-chat-messages",
      title: "Agent Responses",
      content: "CUGA's responses appear here, showing its reasoning, tool usage, and results in an interactive card format.",
      placement: "top",
      highlightPadding: 10,
    },
    {
      target: ".chat-input-container",
      title: "File Tagging with @",
      content: "Type @ in the chat input to see file autocomplete. This lets you reference specific files from your workspace in your messages.",
      placement: "top",
      highlightPadding: 10,
      beforeShow: () => {
        const input = document.querySelector("#main-input_field") as HTMLElement;
        if (input) input.focus();
      },
    },
    {
      target: ".left-sidebar, .sidebar-toggle-btn",
      title: "Conversations & Variables",
      content: "Track your conversation history and view variables that CUGA has created or extracted during your interactions.",
      placement: "right",
      highlightPadding: 10,
      beforeShow: () => {
        const sidebar = document.querySelector(".left-sidebar");
        const btn = document.querySelector(".sidebar-toggle-btn") as HTMLButtonElement;
        if (!sidebar && btn) {
          btn.click();
          setTimeout(() => {}, 300);
        }
      },
    },
  ];

  const fullTourSteps: TourStep[] = [
    ...chatTourSteps,
    {
      target: ".chat-send-btn",
      title: "Ready to Start!",
      content: "You're all set! Try sending a message to CUGA and see the magic happen. Remember to use @ to tag files and explore all the features.",
      placement: "top",
      highlightPadding: 10,
    },
  ];

  const startTour = (steps: TourStep[]) => {
    setCurrentTourSteps(steps);
    setShowMenu(false);
    setIsTourActive(true);
    if (onStartTour) onStartTour();
  };

  const handleTourComplete = () => {
    setIsTourActive(false);
    setCurrentTourSteps([]);
  };

  const handleTourSkip = () => {
    setIsTourActive(false);
    setCurrentTourSteps([]);
  };

  return (
    <>
      <button
        className="advanced-tour-button"
        onClick={() => {
          setShowMenu(!showMenu);
          setShowHint(false);
        }}
        title="Help & Tours"
      >
        <HelpCircle size={20} />
      </button>

      {showHint && !showMenu && (
        <div className="tour-hint" onClick={() => setShowHint(false)}>
          <span className="tour-hint-text">👋 Click here for a guided tour!</span>
          <div className="tour-hint-arrow" />
        </div>
      )}

      {showMenu && (
        <>
          <div className="tour-menu-overlay" onClick={() => setShowMenu(false)} />
          <div className="tour-menu">
            <div className="tour-menu-header">
              <h3>Guided Tours</h3>
              <button className="tour-menu-close" onClick={() => setShowMenu(false)}>
                <X size={18} />
              </button>
            </div>
            <div className="tour-menu-content">
              <button
                className="tour-menu-item tour-menu-item-featured"
                onClick={() => startTour(fullTourSteps)}
              >
                <div className="tour-menu-item-icon">🚀</div>
                <div className="tour-menu-item-text">
                  <div className="tour-menu-item-title">Complete Tour</div>
                  <div className="tour-menu-item-desc">Full walkthrough of all features</div>
                </div>
              </button>

              <button
                className="tour-menu-item"
                onClick={() => startTour(chatTourSteps)}
              >
                <div className="tour-menu-item-icon">💬</div>
                <div className="tour-menu-item-text">
                  <div className="tour-menu-item-title">Chat Features</div>
                  <div className="tour-menu-item-desc">Messages, responses & file tagging</div>
                </div>
              </button>
            </div>
          </div>
        </>
      )}

      <GuidedTour
        steps={currentTourSteps}
        isActive={isTourActive}
        onComplete={handleTourComplete}
        onSkip={handleTourSkip}
      />
    </>
  );
}

