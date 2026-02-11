import React, { useState, useRef, useEffect, useCallback } from "react";
import { Send, RotateCcw, Bot, User, FileText, Database, Code, Terminal, Cpu, Globe, Settings, ChevronRight } from "lucide-react";
import CardManager from "./CardManager";
import { StopButton } from "./floating/stop_button";
import { fetchStreamingData } from "./StreamingWorkflow";
import { randomUUID } from "./uuid";
import { DebugPanel } from "./DebugPanel";
import { FollowupSuggestions } from "./FollowupSuggestions";
import { exampleUtterances } from "./exampleUtterances";
import "./CustomChat.css";

interface Message {
  id: string;
  text: string;
  isUser: boolean;
  timestamp: number;
  isCardResponse?: boolean;
  chatInstance?: ChatInstance;
}

// Minimal ChatInstance interface compatible with existing code
interface ChatInstance {
  messaging: {
    addMessage: (message: any) => Promise<void>;
    addMessageChunk?: (chunk: any) => void;
  };
  on?: (options: { type: string; handler: (event: any) => void }) => void;
}

interface CustomChatProps {
  onVariablesUpdate?: (variables: Record<string, any>, history: Array<any>) => void;
  onFileAutocompleteOpen?: () => void;
  onFileHover?: (filePath: string | null) => void;
  onMessageSent?: (message: string) => void;
  onChatStarted?: (started: boolean) => void;
  onThreadIdChange?: (threadId: string) => void;
  initialChatStarted?: boolean;
}

export function CustomChat({ onVariablesUpdate, onFileAutocompleteOpen, onFileHover, onMessageSent, onChatStarted, onThreadIdChange, initialChatStarted = false }: CustomChatProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isProcessing, setIsProcessing] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLDivElement>(null);
  const currentChatInstanceRef = useRef<ChatInstance | null>(null);
  const [showFileAutocomplete, setShowFileAutocomplete] = useState(false);
  const [autocompleteQuery, setAutocompleteQuery] = useState("");
  const [allFiles, setAllFiles] = useState<Array<{ name: string; path: string }>>([]);
  const [filteredFiles, setFilteredFiles] = useState<Array<{ name: string; path: string }>>([]);
  const [selectedFileIndex, setSelectedFileIndex] = useState(0);
  const [selectedFiles, setSelectedFiles] = useState<Array<{ name: string; path: string; id: string }>>([]);
  const [threadId, setThreadId] = useState<string>("");
  const threadIdRef = useRef<string>("");
  const [showExampleUtterances, setShowExampleUtterances] = useState(true);
  const [hasStartedChat, setHasStartedChat] = useState(initialChatStarted);
  const [followupSuggestions, setFollowupSuggestions] = useState<string[]>([]);
  const [lastUserQuery, setLastUserQuery] = useState<string>("");
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set(['contacts.txt']));

  // Notify parent when chat starts
  useEffect(() => {
    if (onChatStarted) {
      onChatStarted(hasStartedChat);
    }
  }, [hasStartedChat, onChatStarted]);

  // Initialize threadId on mount
  useEffect(() => {
    const newThreadId = randomUUID();
    setThreadId(newThreadId);
    threadIdRef.current = newThreadId;
    if (onThreadIdChange) {
      onThreadIdChange(newThreadId);
    }
  }, [onThreadIdChange]);

  // Create a simple chat instance interface
  const createChatInstance = useCallback((): ChatInstance => {
    return {
      messaging: {
        addMessage: async () => {
          // Handle message addition if needed
        },
      },
    };
  }, []);

  useEffect(() => {
    if (!currentChatInstanceRef.current) {
      currentChatInstanceRef.current = createChatInstance();
    }
  }, [createChatInstance]);

  // Listen for variables updates from CardManager
  useEffect(() => {
    const handleVariablesUpdate = ((event: CustomEvent) => {
      console.log('[CustomChat] Received variablesUpdate event:', event.detail);
      const { variables, history } = event.detail;
      console.log('[CustomChat] Variables keys:', Object.keys(variables));
      console.log('[CustomChat] History length:', history.length);
      if (onVariablesUpdate) {
        console.log('[CustomChat] Calling onVariablesUpdate callback');
        onVariablesUpdate(variables, history);
      } else {
        console.warn('[CustomChat] onVariablesUpdate callback is not defined!');
      }
    }) as EventListener;

    if (typeof window !== "undefined") {
      console.log('[CustomChat] Setting up variablesUpdate event listener');
      window.addEventListener('variablesUpdate', handleVariablesUpdate);
      return () => {
        console.log('[CustomChat] Cleaning up variablesUpdate event listener');
        window.removeEventListener('variablesUpdate', handleVariablesUpdate);
      };
    }
  }, []);

  // Listen for final answer completion from CardManager
  useEffect(() => {
    const handleFinalAnswerComplete = (() => {
      console.log('[CustomChat] Received finalAnswerComplete event');
      
      // Generate followup suggestions based on the last query
      if (lastUserQuery) {
        const suggestions = generateFollowupSuggestions(lastUserQuery);
        setFollowupSuggestions(suggestions);
      }
    }) as EventListener;

    if (typeof window !== "undefined") {
      console.log('[CustomChat] Setting up finalAnswerComplete event listener');
      window.addEventListener('finalAnswerComplete', handleFinalAnswerComplete);
      return () => {
        console.log('[CustomChat] Cleaning up finalAnswerComplete event listener');
        window.removeEventListener('finalAnswerComplete', handleFinalAnswerComplete);
      };
    }
  }, [lastUserQuery]);

  // Function to generate contextual followup suggestions
  const generateFollowupSuggestions = (query: string): string[] => {
    const lowerQuery = query.toLowerCase();
    
    // Exact match for the demo workflow
    if (lowerQuery.includes('from contacts.txt show me which users belong to the crm system')) {
      return [
        "show me details of first one",
        "Show me details of Sarah"
      ];
    }
    
    // Second level followups after showing details of a user/contact
    if (lowerQuery.includes('show me details of') || lowerQuery.includes('details of sarah') || lowerQuery.includes('details of first one')) {
      return [
        "How many employees work at her company's account",
        "Which percentile is her account's revenue across all accounts?"
      ];
    }

    // Default general suggestions (disabled)
    return [];
  };

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Remove the auto-welcome message effect since we have a dedicated welcome screen

  // Load workspace files using shared service with enforced throttling
  useEffect(() => {
    const loadFiles = async () => {
      try {
        const { workspaceService } = await import('./workspaceService');
        const data = await workspaceService.getWorkspaceTree();
        const files = extractFiles(data.tree || []);
        setAllFiles(files);
      } catch (error) {
        console.error('Error loading files:', error);
      }
    };

    loadFiles();
    const interval = setInterval(loadFiles, 15000);
    return () => clearInterval(interval);
  }, []);

  // Filter files based on query
  useEffect(() => {
    if (!showFileAutocomplete) {
      setFilteredFiles([]);
      return;
    }

    if (autocompleteQuery === '') {
      setFilteredFiles(allFiles.slice(0, 10));
    } else {
      const lowerQuery = autocompleteQuery.toLowerCase();
      const filtered = allFiles.filter(file => {
        const nameMatch = file.name.toLowerCase().includes(lowerQuery);
        const pathMatch = file.path.toLowerCase().includes(lowerQuery);
        return nameMatch || pathMatch;
      }).slice(0, 10);
      setFilteredFiles(filtered);
    }
    setSelectedFileIndex(0);
  }, [showFileAutocomplete, autocompleteQuery, allFiles]);

  // Highlight file when selection changes via keyboard navigation
  useEffect(() => {
    if (showFileAutocomplete && filteredFiles.length > 0 && selectedFileIndex >= 0 && selectedFileIndex < filteredFiles.length) {
      onFileHover?.(filteredFiles[selectedFileIndex].path);
    } else if (!showFileAutocomplete) {
      onFileHover?.(null);
    }
  }, [selectedFileIndex, showFileAutocomplete, filteredFiles, onFileHover]);

  const extractFiles = (nodes: any[]): Array<{ name: string; path: string }> => {
    const files: Array<{ name: string; path: string }> = [];
    for (const node of nodes) {
      if (node.type === "file") {
        files.push({ name: node.name, path: node.path });
      } else if (node.children) {
        files.push(...extractFiles(node.children));
      }
    }
    return files;
  };


  const handleSend = async () => {
    if (!inputRef.current) return;

    const text = inputRef.current.textContent?.trim() || '';
    if (!text || isProcessing) return;

    // Convert file reference elements back to ./path format for backend processing
    let processedText = text;
    const fileElements = inputRef.current.querySelectorAll('.inline-file-reference');

    fileElements.forEach((element) => {
      const filePath = element.getAttribute('data-file-path');
      const fileName = element.getAttribute('data-file-name');
      if (filePath && fileName) {
        // Replace the element's text content with the dot path
        processedText = processedText.replace(element.textContent || '', `./${filePath}`);
      }
    });

    // Create display HTML for the message (keep the styled file references)
    const displayHTML = inputRef.current.innerHTML;

    // Add user message with styled HTML
    const userMessage: Message = {
      id: `msg-${Date.now()}`,
      text: displayHTML, // Store the HTML for proper rendering
      isUser: true,
      timestamp: Date.now(),
    };

    setMessages((prev) => [...prev, userMessage]);

    // Mark that chat has started
    setHasStartedChat(true);

    // Save the query for followup suggestions
    setLastUserQuery(processedText);

    // Clear any existing followup suggestions
    setFollowupSuggestions([]);

    // Notify parent component that a message was sent
    if (onMessageSent) {
      onMessageSent(processedText);
    }

    // Clear the input
    inputRef.current.innerHTML = '';
    setInputValue("");
    setSelectedFiles([]);
    setShowExampleUtterances(false);
    setIsProcessing(true);

    // Create a new chat instance for this message
    const newChatInstance = createChatInstance();
    currentChatInstanceRef.current = newChatInstance;

    // Add bot card response message
    const botMessage: Message = {
      id: `bot-${Date.now()}`,
      text: "",
      isUser: false,
      timestamp: Date.now(),
      isCardResponse: true,
      chatInstance: newChatInstance,
    };

    setMessages((prev) => [...prev, botMessage]);

    try {
      // Ensure threadId is set (use ref to get latest value, fallback to state)
      const currentThreadId = threadIdRef.current || threadId;
      if (!currentThreadId) {
        // If still empty, generate one now
        const newThreadId = randomUUID();
        setThreadId(newThreadId);
        threadIdRef.current = newThreadId;
        console.log('[CustomChat] Generated new threadId:', newThreadId);
        // Notify parent of new thread ID
        if (onThreadIdChange) {
          onThreadIdChange(newThreadId);
        }
      }
      const finalThreadId = threadIdRef.current || threadId;
      console.log('[CustomChat] Sending message with threadId:', finalThreadId);
      // Call the streaming workflow with processed text (bracket format converted to ./path)
      await fetchStreamingData(newChatInstance as any, processedText, undefined, finalThreadId);
    } catch (error) {
      console.error("Error sending message:", error);
    } finally {
      setIsProcessing(false);
    }
  };

  const handleRestart = async () => {
    // Reset backend
    const newThreadId = randomUUID();
    setThreadId(newThreadId);
    threadIdRef.current = newThreadId;
    
    // Notify parent of new thread ID
    if (onThreadIdChange) {
      onThreadIdChange(newThreadId);
    }
    
    try {
      await fetch('/reset', {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'X-Thread-ID': newThreadId
        },
      });
    } catch (error) {
      console.error("Error calling reset endpoint:", error);
    }

    // Clear messages and reset to welcome screen
    setMessages([]);
    setHasStartedChat(false);
    setIsProcessing(false);
    setInputValue("");
    setShowExampleUtterances(true);
    setFollowupSuggestions([]);
    setLastUserQuery("");
    
    // Create a fresh chat instance
    currentChatInstanceRef.current = createChatInstance();
  };

  const handleContentEditableInput = (e: React.FormEvent<HTMLDivElement>) => {
    const target = e.currentTarget;
    const text = target.textContent || '';

    setInputValue(text);
    
    // Hide examples when user starts typing, show when empty
    if (text.trim().length > 0) {
      setShowExampleUtterances(false);
    } else {
      setShowExampleUtterances(true);
    }

    // Check for @ trigger
    const lastAtIndex = text.lastIndexOf('@');
    if (lastAtIndex !== -1) {
      const charBeforeAt = lastAtIndex > 0 ? text[lastAtIndex - 1] : ' ';
      const isValidTrigger = lastAtIndex === 0 || /\s/.test(charBeforeAt);

      if (isValidTrigger) {
        const textAfterAt = text.substring(lastAtIndex + 1);
        const searchTerm = textAfterAt.split(/\s/)[0];
        setAutocompleteQuery(searchTerm);
        setShowFileAutocomplete(true);
        onFileAutocompleteOpen?.();
      } else {
        setShowFileAutocomplete(false);
      }
    } else {
      setShowFileAutocomplete(false);
    }

    // Extract file references from the HTML content
    const foundFiles: Array<{ name: string; path: string; id: string }> = [];
    const fileElements = target.querySelectorAll('.inline-file-reference');

    fileElements.forEach((element) => {
      const filePath = element.getAttribute('data-file-path');
      const fileName = element.getAttribute('data-file-name');
      if (filePath && fileName) {
        const existingFile = selectedFiles.find(f => f.path === filePath);
        const id = existingFile?.id || `file-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        foundFiles.push({ name: fileName, path: filePath, id });
      }
    });

    setSelectedFiles(foundFiles);

    // Auto-resize
    target.style.height = 'auto';
    target.style.height = `${Math.min(target.scrollHeight, 120)}px`;
  };

  const handleContentEditableClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;

    // Check if clicked element is a remove button
    if (target.classList.contains('file-chip-remove')) {
      e.preventDefault();
      e.stopPropagation();

      // Find the parent file reference element
      const fileChip = target.closest('.inline-file-reference');
      if (fileChip && inputRef.current) {
        // Remove the file chip from the DOM
        fileChip.remove();

        // Update the input and selected files
        handleContentEditableInput({ currentTarget: inputRef.current } as any);

        // Focus back to the input
        inputRef.current.focus();
      }
      return;
    }

    // Check if clicked within a file chip (but not on remove button)
    const fileChip = target.closest('.inline-file-reference');
    if (fileChip) {
      e.preventDefault();
      e.stopPropagation();

      // Focus the input but position cursor appropriately
      if (inputRef.current) {
        inputRef.current.focus();

        // Try to place cursor after the chip
        const range = document.createRange();
        const selection = window.getSelection();

        // Find the next text node or position after the chip
        let nextNode = fileChip.nextSibling;
        if (nextNode && nextNode.nodeType === Node.TEXT_NODE) {
          range.setStart(nextNode, 0);
          range.setEnd(nextNode, 0);
        } else {
          // Create a text node after the chip if none exists
          const textNode = document.createTextNode('');
          fileChip.parentNode?.insertBefore(textNode, nextNode);
          range.setStart(textNode, 0);
          range.setEnd(textNode, 0);
        }

        selection?.removeAllRanges();
        selection?.addRange(range);
      }
    }
  };


  const handleFileSelect = (filePath: string) => {
    if (!inputRef.current) return;

    const selectedFile = allFiles.find(f => f.path === filePath);
    if (!selectedFile) return;

    // Find the @ trigger using the current selection/cursor position
    const selection = window.getSelection();
    const range = selection?.getRangeAt(0);

    if (!range) return;

    // Create the file reference element
    const fileElement = document.createElement('span');
    fileElement.className = 'inline-file-reference';
    fileElement.setAttribute('data-file-path', filePath);
    fileElement.setAttribute('data-file-name', selectedFile.name);
    fileElement.setAttribute('contentEditable', 'false');
    fileElement.innerHTML = `<svg class="file-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"></path><polyline points="14,2 14,8 20,8"></polyline></svg><span class="file-name">${selectedFile.name}</span><button class="file-chip-remove" type="button" aria-label="Remove file">×</button>`;

    // Find and replace the @ trigger and search term
    const text = inputRef.current.textContent || '';
    const lastAtIndex = text.lastIndexOf('@');
    if (lastAtIndex === -1) return;

    const textAfterAt = text.substring(lastAtIndex + 1);
    const searchTerm = textAfterAt.split(/\s/)[0];

    // Find the text nodes containing the @ and search term
    const treeWalker = document.createTreeWalker(
      inputRef.current,
      NodeFilter.SHOW_TEXT,
      null
    );

    let foundAtNode: Text | null = null;
    let atOffset = -1;

    let currentNode;
    while (currentNode = treeWalker.nextNode()) {
      const nodeText = currentNode.textContent || '';
      const atIndex = nodeText.indexOf('@');

      if (atIndex !== -1) {
        foundAtNode = currentNode as Text;
        atOffset = atIndex;
        break;
      }
    }

    if (foundAtNode && atOffset !== -1) {
      // Calculate the range for @ and search term
      const searchTermEnd = atOffset + 1 + searchTerm.length;

      // Create a range to replace the @ and search term
      const replaceRange = document.createRange();
      replaceRange.setStart(foundAtNode, atOffset);
      replaceRange.setEnd(foundAtNode, Math.min(searchTermEnd, foundAtNode.length));

      // Replace the range with the file element
      replaceRange.deleteContents();
      replaceRange.insertNode(fileElement);

      // Add a space after the file element
      const spaceNode = document.createTextNode(' ');
      replaceRange.setStartAfter(fileElement);
      replaceRange.insertNode(spaceNode);

      // Position cursor after the space
      replaceRange.setStartAfter(spaceNode);
      replaceRange.setEndAfter(spaceNode);

      selection?.removeAllRanges();
      selection?.addRange(replaceRange);
    }

    setShowFileAutocomplete(false);

    // Update selected files
    handleContentEditableInput({ currentTarget: inputRef.current } as any);

    // Ensure focus remains
    inputRef.current.focus();
  };

  const handlePaste = (e: React.ClipboardEvent<HTMLDivElement>) => {
    e.preventDefault();

    // Get plain text from clipboard
    const text = e.clipboardData.getData('text/plain');

    // Insert the plain text at cursor position
    const selection = window.getSelection();
    const range = selection?.getRangeAt(0);

    if (range && inputRef.current) {
      range.deleteContents();
      const textNode = document.createTextNode(text);
      range.insertNode(textNode);

      // Move cursor to end of inserted text
      range.setStartAfter(textNode);
      range.setEndAfter(textNode);
      selection?.removeAllRanges();
      selection?.addRange(range);

      // Trigger input handler to update state
      handleContentEditableInput({ currentTarget: inputRef.current } as any);
    }
  };

  const handleExampleClick = (utterance: string) => {
    if (!inputRef.current) return;
    
    // Set the input content to the example utterance
    inputRef.current.textContent = utterance;
    setInputValue(utterance);
    setShowExampleUtterances(false);
    
    // Focus the input and scroll it into view
    inputRef.current.focus();
    inputRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    
    // Trigger input handler to update state
    handleContentEditableInput({ currentTarget: inputRef.current } as any);
    
    // Small delay to ensure the input is visible, then scroll to it
    setTimeout(() => {
      inputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 100);
  };

  const handleFollowupClick = (suggestion: string) => {
    if (!inputRef.current) return;
    
    // Set the input content to the suggestion
    inputRef.current.textContent = suggestion;
    setInputValue(suggestion);
    
    // Clear the followup suggestions
    setFollowupSuggestions([]);
    
    // Focus the input
    inputRef.current.focus();
    
    // Trigger input handler to update state
    handleContentEditableInput({ currentTarget: inputRef.current } as any);
    
    // Auto-submit the followup question
    setTimeout(() => {
      handleSend();
    }, 100);
  };

  const toggleFileExpand = (fileName: string) => {
    setExpandedFiles(prev => {
      const newSet = new Set(prev);
      if (newSet.has(fileName)) {
        newSet.delete(fileName);
      } else {
        newSet.add(fileName);
      }
      return newSet;
    });
  };

  const handleInputFocus = () => {
    if (!inputValue.trim()) {
      setShowExampleUtterances(true);
    }
  };

  const handleInputBlur = () => {
    // Don't hide examples on blur in advanced mode - keep them visible
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLDivElement>) => {
    // Check if cursor is inside a file chip
    const selection = window.getSelection();
    const range = selection?.getRangeAt(0);
    let isInsideChip = false;

    if (range) {
      let node: Node | null = range.commonAncestorContainer;
      // If it's a text node, check parent
      if (node.nodeType === Node.TEXT_NODE) {
        node = node.parentNode;
      }

      // Walk up the DOM to check if we're inside a file chip
      while (node && node !== e.currentTarget) {
        if (node instanceof HTMLElement && node.classList.contains('inline-file-reference')) {
          isInsideChip = true;
          break;
        }
        node = node.parentNode;
      }
    }

    // Prevent editing within file chips
    if (isInsideChip) {
      // Allow navigation keys and special keys
      const allowedKeys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'];
      const controlKeys = ['Backspace', 'Delete'];

      if (!allowedKeys.includes(e.key) && !controlKeys.includes(e.key) && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        return;
      }

      // Handle backspace/delete within chips - remove the entire chip
      if (e.key === 'Backspace' || e.key === 'Delete') {
        e.preventDefault();
        let chipElement: Element | null = null;

        if (range?.commonAncestorContainer?.parentNode instanceof HTMLElement) {
          chipElement = range.commonAncestorContainer.parentNode.closest('.inline-file-reference');
        }
        if (!chipElement && range?.startContainer?.parentNode instanceof HTMLElement) {
          chipElement = range.startContainer.parentNode.closest('.inline-file-reference');
        }

        if (chipElement && inputRef.current) {
          chipElement.remove();
          handleContentEditableInput({ currentTarget: inputRef.current } as any);
          inputRef.current.focus();
        }
        return;
      }
    }

    if (showFileAutocomplete && filteredFiles.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedFileIndex((prev) => (prev + 1) % filteredFiles.length);
        return;
      }

      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedFileIndex((prev) => (prev - 1 + filteredFiles.length) % filteredFiles.length);
        return;
      }

      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        handleFileSelect(filteredFiles[selectedFileIndex].path);
        return;
      }

      if (e.key === 'Escape') {
        e.preventDefault();
        setShowFileAutocomplete(false);
        return;
      }
    }

    // Handle Enter key: Send on Enter, new line on Shift+Enter
    if (e.key === "Enter") {
      if (e.shiftKey) {
        // Allow new line with Shift+Enter
        return;
      } else {
        // Send message on Enter without Shift
        e.preventDefault();
        handleSend();
      }
    }
  };

  return (
    <div className="custom-chat-container">
      {hasStartedChat && (
        <div className="custom-chat-header">
          <div className="chat-header-left">
            <Bot size={20} />
            <span className="chat-header-title">CUGA Agent</span>
          </div>
          <button
            className="chat-restart-btn"
            onClick={handleRestart}
            title="Restart conversation"
          >
            <RotateCcw size={16} />
            <span>Restart</span>
          </button>
        </div>
      )}

      {!hasStartedChat ? (
        <div className="welcome-screen">
          {/* Main Navigation Header - Welcome Mode Only */}
          <header className="main-nav-header">
            <div className="nav-container">
              <div className="nav-brand">
                <img
                  src="https://avatars.githubusercontent.com/u/230847519?s=100&v=4"
                  alt="CUGA"
                  className="nav-logo"
                />
                <span className="nav-brand-text">CUGA Agent</span>
              </div>
              <nav className="nav-links">
                <a href="https://docs.cuga.dev" target="_blank" rel="noopener noreferrer" className="nav-link">
                  Docs
                </a>
                <a href="https://cuga.dev" target="_blank" rel="noopener noreferrer" className="nav-link">
                  Site
                </a>
                <a href="https://github.com/cuga-project/cuga-agent" target="_blank" rel="noopener noreferrer" className="nav-link">
                  GitHub
                </a>
                <a href="https://discord.gg/UhNVTggG" target="_blank" rel="noopener noreferrer" className="nav-link">
                  Community
                </a>
                <a href="https://github.com/cuga-project/cuga-agent/issues/new" target="_blank" rel="noopener noreferrer" className="nav-link nav-link-feedback">
                  Give Feedback
                </a>
              </nav>
            </div>
          </header>

          <div className="welcome-top-section">
            <div className="welcome-left-column">
            <div className="welcome-content">
              <div className="welcome-header">
                <h1 className="welcome-title">Experience CUGA Agent</h1>
                <p className="mission-text">
                  Intelligent task automation through multi-agent orchestration, API integration, and code generation on enterprise demo applications.
                </p>
              </div>

              <div className="demo-apps-section">
                <div className="section-header">
                  <h2 className="section-title">Connected Apps and Tools for This Demo</h2>
                </div>

                <div className="demo-apps-grid">
                  <div className="demo-app-card crm-card">
                    <div className="demo-app-icon">
                      <Database size={32} />
                    </div>
                    <div className="demo-app-card-content">
                      <h3 className="demo-app-name">CRM System</h3>
                      <p className="demo-app-tools">20 Tools Available</p>
                      <div className="demo-app-examples">
                        <span className="demo-app-tag">Get Accounts</span>
                        <span className="demo-app-tag">Get Contacts</span>
                        <span className="demo-app-tag">Get Leads</span>
                        <span className="demo-app-tag">+17 more</span>
                      </div>
                      <p className="demo-app-description">
                        Manage customers, accounts, contacts, and deals with full CRUD operations
                      </p>
                    </div>
                  </div>

                  <div className="demo-app-card filesystem-card filesystem-card-expanded">
                    <div className="demo-app-icon">
                      <FileText size={32} />
                    </div>
                    <div className="demo-app-card-content">
                      <h3 className="demo-app-name">Workspace Files</h3>
                      <p className="demo-app-tools">File Management</p>
                      <div className="demo-app-examples">
                        <span className="demo-app-tag">Read File</span>
                      </div>
                      <p className="demo-app-description">
                        Read files from the cuga_workspace directory
                      </p>
                      
                      <div className="workspace-files-preview">
                        <div className="workspace-file-item">
                          <div 
                            className="workspace-file-header clickable"
                            onClick={() => toggleFileExpand('contacts.txt')}
                          >
                            <ChevronRight 
                              size={14} 
                              className={`workspace-file-chevron ${expandedFiles.has('contacts.txt') ? 'expanded' : ''}`}
                            />
                            <FileText size={14} />
                            <span className="workspace-file-name">contacts.txt</span>
                            <span className="workspace-file-badge">7 contacts</span>
                          </div>
                          {expandedFiles.has('contacts.txt') && (
                            <div className="workspace-file-content">
                              <code>sarah.bell@gammadeltainc.partners.org</code>
                              <code>sharon.jimenez@upsiloncorp.innovation.org</code>
                              <code>ruth.ross@sigmasystems.operations.com</code>
                              <span className="workspace-file-more">+4 more...</span>
                            </div>
                          )}
                        </div>
                        
                        <div className="workspace-files-more">
                          and 3 more files...
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="welcome-right-column">
            <div className="get-started-container">
              <div className="github-section-right">
                  <a
                    href="https://github.com/cuga-project/cuga-agent"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="github-button-sidebar"
                  >
                    🌟 Star us on GitHub
                  </a>
              </div>

              <div className="get-started-section">
                <div className="section-header">
                  <h2 className="section-title">Get Started</h2>
                  <p className="section-subtitle">Try one of these examples or type your own request</p>
                </div>

                {showExampleUtterances && !inputValue.trim() && (
                  <div className="example-utterances-widget">
                    <div className="example-utterances-list">
                      {exampleUtterances.map((utterance, index) => (
                        <button
                          key={index}
                          className="example-utterance-chip"
                          onMouseDown={(e) => {
                            e.preventDefault();
                            handleExampleClick(utterance.text);
                          }}
                          type="button"
                        >
                          <div className="example-utterance-text">{utterance.text}</div>
                          <div className="example-utterance-reason">{utterance.reason}</div>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <div className="welcome-input-wrapper">
                {!hasStartedChat && (
                  <div className="welcome-logo input-logo">
                    <img
                      src="https://avatars.githubusercontent.com/u/230847519?s=100&v=4"
                      alt="CUGA Logo"
                      className="welcome-logo-image"
                    />
                  </div>
                )}
                <div className="chat-input-container-welcome">
                  <div className="textarea-wrapper">
                    <div
                      ref={inputRef}
                      id="main-input_field"
                      className="chat-input"
                      contentEditable={!isProcessing}
                      onInput={handleContentEditableInput}
                      onClick={handleContentEditableClick}
                      onKeyDown={handleKeyPress}
                      onPaste={handlePaste}
                      onFocus={handleInputFocus}
                      onBlur={handleInputBlur}
                      data-placeholder="Type your message... (@ for files, Shift+Enter for new line)"
                      style={{
                        minHeight: "44px",
                        maxHeight: "120px",
                        overflowY: "auto",
                      }}
                    />
                  </div>
                  <StopButton location="inline" />
                  <button
                    className="chat-send-btn"
                    onClick={handleSend}
                    disabled={!inputValue.trim() || isProcessing}
                    title="Send message"
                  >
                    <Send size={18} />
                  </button>
                </div>
              </div>
            </div>
          </div>
          </div>

          <div className="welcome-features-section">
            <div className="section-header">
              <h2 className="section-title">Key Capabilities</h2>
              <p className="section-subtitle">Powerful features that make CUGA an intelligent automation platform</p>
            </div>

            <div className="welcome-features">
              <div className="feature-card">
                <div className="feature-icon multi-agent-icon">
                  <Bot size={32} />
                </div>
                <h3 className="feature-title">Multi-Agent System</h3>
                <p className="feature-description">CUGA orchestrates specialized agents for planning, coding & execution</p>
              </div>

              <div className="feature-card">
                <div className="feature-icon code-exec-icon">
                  <Terminal size={32} />
                </div>
                <h3 className="feature-title">Code Execution</h3>
                <p className="feature-description">CUGA writes and runs Python code in secure sandbox</p>
              </div>

              <div className="feature-card">
                <div className="feature-icon api-icon">
                  <Code size={32} />
                </div>
                <h3 className="feature-title">API Integration</h3>
                <p className="feature-description">Users can connect any OpenAPI or MCP server instantly</p>
              </div>

              <div className="feature-card">
                <div className="feature-icon memory-icon">
                  <Database size={32} />
                </div>
                <h3 className="feature-title">Human in the Loop</h3>
                <p className="feature-description">Users can ask followup questions on variables in memory and previous conversations</p>
              </div>

              <div className="feature-card">
                <div className="feature-icon model-flex-icon">
                  <Cpu size={32} />
                </div>
                <h3 className="feature-title">Model Flexibility</h3>
                <p className="feature-description">CUGA works with small models and open source models like GPT OSS 120B and Llama 4</p>
              </div>

              <div className="feature-card">
                <div className="feature-icon web-api-icon">
                  <Globe size={32} />
                </div>
                <h3 className="feature-title">Web & API Tasks</h3>
                <p className="feature-description">CUGA executes both web and API tasks seamlessly</p>
              </div>

              <div className="feature-card">
                <div className="feature-icon reasoning-icon">
                  <Settings size={32} />
                </div>
                <h3 className="feature-title">Reasoning Modes</h3>
                <p className="feature-description">Users can configure reasoning modes: lite, balanced</p>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="custom-chat-messages">
          {messages.map((message) => (
            <div
              key={message.id}
              className={`message ${message.isUser ? "message-user" : "message-bot"}`}
            >
              <div className="message-avatar">
                {message.isUser ? (
                  <User size={18} />
                ) : (
                  <img 
                    src="https://avatars.githubusercontent.com/u/230847519?s=48&v=4" 
                    alt="Bot Avatar"
                    className="bot-avatar-image"
                  />
                )}
              </div>
              {message.isCardResponse && message.chatInstance ? (
                <div className="message-content message-card-content">
                  <CardManager 
                    chatInstance={message.chatInstance as any} 
                    threadId={threadIdRef.current || threadId}
                  />
                </div>
              ) : (
                <div
                  className="message-content"
                  dangerouslySetInnerHTML={{ __html: message.text }}
                />
              )}
            </div>
          ))}

          <div ref={messagesEndRef} />
        </div>
      )}

      {/* Input area only shown when chat has started */}
      {hasStartedChat && (
        <div className="custom-chat-input-area">
          {/* Followup suggestions */}
          {followupSuggestions.length > 0 && !isProcessing && (
            <FollowupSuggestions
              suggestions={followupSuggestions}
              onSuggestionClick={handleFollowupClick}
            />
          )}

          <div className="chat-input-wrapper">
            {!hasStartedChat && (
              <div className="welcome-logo input-logo">
                <img
                  src="https://avatars.githubusercontent.com/u/230847519?s=100&v=4"
                  alt="CUGA Logo"
                  className="welcome-logo-image"
                />
              </div>
            )}
            <div className="chat-input-container-chat">
            <div className="textarea-wrapper">
              <div
                ref={inputRef}
                id="main-input_field"
                className="chat-input"
                contentEditable={!isProcessing}
                onInput={handleContentEditableInput}
                onClick={handleContentEditableClick}
                onKeyDown={handleKeyPress}
                onPaste={handlePaste}
                onFocus={handleInputFocus}
                onBlur={handleInputBlur}
                data-placeholder="Type your message... (@ for files, Shift+Enter for new line)"
                style={{
                  minHeight: "44px",
                  maxHeight: "120px",
                  overflowY: "auto",
                }}
              />
            </div>
            <StopButton location="inline" />
            <button
              className="chat-send-btn"
              onClick={handleSend}
              disabled={!inputValue.trim() || isProcessing}
              title="Send message"
            >
              <Send size={18} />
            </button>
          </div>
          </div>

          {showFileAutocomplete && filteredFiles.length > 0 && (
            <div className="simple-file-autocomplete">
              <div className="simple-file-autocomplete-header">
                <span>Workspace Files</span>
                <span className="file-count">{filteredFiles.length}</span>
              </div>
              <div className="simple-file-autocomplete-list">
                {filteredFiles.map((file, index) => (
                  <div
                    key={file.path}
                    className={`simple-file-autocomplete-item ${index === selectedFileIndex ? 'selected' : ''}`}
                    onClick={() => handleFileSelect(file.path)}
                    onMouseEnter={() => {
                      setSelectedFileIndex(index);
                      onFileHover?.(filteredFiles[index].path);
                    }}
                    onMouseLeave={() => onFileHover?.(null)}
                  >
                    <FileText size={16} className="file-icon" />
                    <div className="file-info">
                      <span className="file-name">{file.name}</span>
                      <span className="file-path">./{file.path}</span>
                    </div>
                  </div>
                ))}
              </div>
              <div className="simple-file-autocomplete-footer">
                <span className="hint">↑↓ navigate • Enter/Tab select • Esc close</span>
              </div>
            </div>
          )}

          {/* Show feature cards only on welcome screen, AFTER input */}
          {!hasStartedChat && (
            <div className="welcome-features-section">
              <div className="section-header">
                <h2 className="section-title">Key Capabilities</h2>
                <p className="section-subtitle">Powerful features that make CUGA an intelligent automation platform</p>
              </div>

              <div className="welcome-features">
                <div className="feature-card">
                  <div className="feature-icon multi-agent-icon">
                    <Bot size={32} />
                  </div>
                  <h3 className="feature-title">Multi-Agent System</h3>
                  <p className="feature-description">CUGA orchestrates specialized agents for planning, coding & execution</p>
                </div>

                <div className="feature-card">
                  <div className="feature-icon code-exec-icon">
                    <Terminal size={32} />
                  </div>
                  <h3 className="feature-title">Code Execution</h3>
                  <p className="feature-description">CUGA writes and runs Python code in secure sandbox</p>
                </div>

                <div className="feature-card">
                  <div className="feature-icon api-icon">
                    <Code size={32} />
                  </div>
                  <h3 className="feature-title">API Integration</h3>
                  <p className="feature-description">Users can connect any OpenAPI or MCP server instantly</p>
                </div>

                <div className="feature-card">
                  <div className="feature-icon memory-icon">
                    <Database size={32} />
                  </div>
                  <h3 className="feature-title">Smart Memory</h3>
                  <p className="feature-description">CUGA tracks variables and data across conversations</p>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
      <DebugPanel threadId={threadIdRef.current || threadId || ""} />
    </div>
  );
}

