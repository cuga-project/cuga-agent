import { useState, useEffect, useRef, useCallback } from "react";
import { FileText } from "lucide-react";
import React from "react";
import "./FileAutocomplete.css";

interface FileNode {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: FileNode[];
}

interface FileAutocompleteProps {
  onFileSelect: (filePath: string) => void;
  onAutocompleteOpen?: () => void;
  onFileHover?: (filePath: string | null) => void;
  disabled?: boolean;
  threadId?: string;
}

export function FileAutocomplete({ onFileSelect, onAutocompleteOpen, onFileHover, disabled = false, threadId }: FileAutocompleteProps) {
  const [allFiles, setAllFiles] = useState<Array<{ name: string; path: string }>>([]);
  const [suggestions, setSuggestions] = useState<Array<{ name: string; path: string }>>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const currentInputValueRef = useRef<string>('');
  const lastProcessedValueRef = useRef<string>('');
  const usedMockRef = useRef<boolean>(false);
  const suggestionsRef = useRef<HTMLDivElement>(null);
  const isProcessingRef = useRef<boolean>(false);
  const selectedItemRef = useRef<HTMLDivElement>(null);

  const getInputPosition = () => {
    const inputContainer = document.querySelector('.WACInputContainer');
    if (inputContainer) {
      const rect = inputContainer.getBoundingClientRect();
      return { top: rect.top, left: rect.left, width: rect.width };
    }

    const carbonChat = document.querySelector('cds-aichat-react');
    if (carbonChat) {
      const rect = carbonChat.getBoundingClientRect();
      return { top: rect.top, left: rect.left, width: rect.width };
    }

    const textarea = document.querySelector('.WAC__TextArea-textarea, textarea, input[type="text"]') as HTMLTextAreaElement | HTMLInputElement | null;
    if (textarea) {
      const rect = textarea.getBoundingClientRect();
      if (rect.top > 50 && rect.left > 0) {
        return { top: rect.top, left: rect.left, width: rect.width };
      }
    }

    return { 
      top: window.innerHeight - 100, 
      left: 20, 
      width: Math.min(600, window.innerWidth - 40) 
    };
  };

  const handleInputChange = (value: string) => {
    if (isProcessingRef.current || value === lastProcessedValueRef.current) {
      return;
    }

    isProcessingRef.current = true;
    lastProcessedValueRef.current = value;

    const lastAtIndex = value.lastIndexOf('@');

    if (lastAtIndex !== -1) {
      const charBeforeAt = lastAtIndex > 0 ? value[lastAtIndex - 1] : ' ';
      const isValidTrigger = lastAtIndex === 0 || /\s/.test(charBeforeAt);

      if (isValidTrigger) {
        const textAfterAt = value.substring(lastAtIndex + 1);
        const searchTerm = textAfterAt.split(/\s/)[0].trim();

        let filtered;
        if (searchTerm === '') {
          filtered = allFiles.slice(0, 10);
        } else {
          const lowerSearchTerm = searchTerm.toLowerCase();
          filtered = allFiles.filter(file => {
            const nameMatch = file.name.toLowerCase().includes(lowerSearchTerm);
            const pathMatch = file.path.toLowerCase().includes(lowerSearchTerm);
            return nameMatch || pathMatch;
          }).slice(0, 10);
        }

        if (filtered.length > 0) {
          setSuggestions(filtered);
          setSelectedIndex(0);
          setShowSuggestions(true);
          onAutocompleteOpen?.();

          const inputPos = getInputPosition();
          const dropdownHeight = Math.min(filtered.length * 42 + 60, 450);
          let top = inputPos.top - dropdownHeight - 8;
          
          if (top < 0) {
            top = inputPos.top + 60;
          }
          
          const pos = {
            top: Math.max(10, top),
            left: Math.max(10, inputPos.left + 50)
          };
          
          setPosition(pos);
        } else {
          setShowSuggestions(false);
        }
      } else {
        setShowSuggestions(false);
      }
    } else {
      setShowSuggestions(false);
    }

    requestAnimationFrame(() => {
      isProcessingRef.current = false;
    });
  };

  const handleFileSelect = useCallback((file: { name: string; path: string }) => {
    const textarea = document.getElementById('main-input_field') as HTMLTextAreaElement;
    if (!textarea) {
      return;
    }

    let currentValue = textarea.value;
    const lastAtIndex = currentValue.lastIndexOf('@');
    if (lastAtIndex === -1) {
      return;
    }

    const textAfterAt = currentValue.substring(lastAtIndex + 1);
    const searchTerm = textAfterAt.split(/\s/)[0];
    const textAfterSearchTerm = currentValue.substring(lastAtIndex + 1 + searchTerm.length);
    
    const insertedPath = file.path.startsWith("/") ? file.path : `./${file.path}`;
    const newValue = currentValue.substring(0, lastAtIndex) + insertedPath + textAfterSearchTerm;

    const nativeTextareaSetter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      'value'
    )?.set;

    if (nativeTextareaSetter) {
      nativeTextareaSetter.call(textarea, newValue);
    } else {
      textarea.value = newValue;
    }

    const inputEvent = new InputEvent('input', { 
      bubbles: true, 
      composed: true,
      inputType: 'insertText',
      data: newValue
    });
    textarea.dispatchEvent(inputEvent);

    textarea.focus();
    const cursorPosition = newValue.length;
    textarea.setSelectionRange(cursorPosition, cursorPosition);

    currentInputValueRef.current = newValue;
    lastProcessedValueRef.current = newValue;
    
    setShowSuggestions(false);
    onFileSelect(file.path);
  }, [onFileSelect]);

  useEffect(() => {
    if (disabled) {
      return;
    }

    loadWorkspaceFiles();
    const fileInterval = setInterval(loadWorkspaceFiles, 15000);

    // Listen to Carbon AI Chat events
    const setupCarbonListeners = () => {
      // Find the Carbon AI Chat component
      const carbonChat = document.querySelector('cds-aichat-react') as any;
      
      // Also check for custom chat textarea
      const customChatTextarea = document.getElementById('main-input_field');

      if (carbonChat || customChatTextarea) {
        // Listen for input events from Carbon chat
        const handleCarbonInput = (event: any) => {
          const target = event.target || event.currentTarget;

          const tryHandleValue = (value: string | null | undefined) => {
            if (typeof value === 'string') {
              currentInputValueRef.current = value;
              handleInputChange(value);
              return true;
            }
            return false;
          };

          if (tryHandleValue((target as any)?.value)) {
            return;
          }

          if (tryHandleValue(event.detail?.value)) {
            return;
          }

          if (typeof event.composedPath === 'function') {
            const path = event.composedPath();
            for (const el of path) {
              const node = el as any;
              if (
                node &&
                (node.tagName === 'TEXTAREA' ||
                  node.tagName === 'INPUT' ||
                  node.contentEditable === 'true')
              ) {
                if (tryHandleValue(node.value || node.textContent)) {
                  return;
                }
              }
            }
          }

          const active = document.activeElement as any;
          if (
            active &&
            (active.tagName === 'TEXTAREA' ||
              active.tagName === 'INPUT' ||
              active.contentEditable === 'true')
          ) {
            if (tryHandleValue(active.value || active.textContent)) {
              return;
            }
          }

          const textarea = document.querySelector(
            '.WAC__TextArea-textarea, textarea, input[type="text"], [contenteditable]'
          ) as any;
          if (textarea) {
            tryHandleValue(textarea.value || textarea.textContent);
          }

        };

        // Only add Carbon Chat listeners if it exists
        if (carbonChat) {
          carbonChat.addEventListener('input', handleCarbonInput);
          carbonChat.addEventListener('change', handleCarbonInput);
          carbonChat.addEventListener('input-change', handleCarbonInput);
          carbonChat.addEventListener('value-change', handleCarbonInput);
        }

        setTimeout(() => {
          const textareas = document.querySelectorAll('.WAC__TextArea-textarea, textarea, input[type="text"], [contenteditable]');
          textareas.forEach(textarea => {
            if (!textarea.hasAttribute('data-autocomplete-listener')) {
              textarea.setAttribute('data-autocomplete-listener', 'true');
              textarea.addEventListener('input', (e: any) => {
                // Don't stop propagation - let React handle it too
                const value = e.target?.value || e.target?.textContent || '';
                currentInputValueRef.current = value;
                handleInputChange(value);
              });
            }
          });
        }, 1000);

        const handleDocumentInput = (e: Event) => {
          const target = e.target as any;
          if (target && target.hasAttribute('data-autocomplete-listener')) {
            return;
          }
          if (target && (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT' || target.contentEditable === 'true')) {
            // Don't stop propagation - let other handlers process it too
            const value = target.value || target.textContent || '';
            currentInputValueRef.current = value;
            handleInputChange(value);
          }
        };

        document.addEventListener('input', handleDocumentInput, true);

        return () => {
          // Only remove Carbon Chat listeners if it existed
          if (carbonChat) {
            carbonChat.removeEventListener('input', handleCarbonInput);
            carbonChat.removeEventListener('change', handleCarbonInput);
            carbonChat.removeEventListener('input-change', handleCarbonInput);
            carbonChat.removeEventListener('value-change', handleCarbonInput);
          }
          document.removeEventListener('input', handleDocumentInput, true);
        };
      } else {
        setTimeout(setupCarbonListeners, 500);
      }
    };

    setupCarbonListeners();

    return () => {
      clearInterval(fileInterval);
    };
  }, [disabled, threadId]);
  

  useEffect(() => {
    if (disabled) {
      return;
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (!showSuggestions || suggestions.length === 0) {
        return;
      }

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        setSelectedIndex(prev => (prev + 1) % suggestions.length);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        setSelectedIndex(prev => (prev - 1 + suggestions.length) % suggestions.length);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        if (suggestions[selectedIndex]) {
          handleFileSelect(suggestions[selectedIndex]);
        }
      } else if (e.key === 'Tab') {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        if (suggestions[selectedIndex]) {
          handleFileSelect(suggestions[selectedIndex]);
        }
      } else if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        setShowSuggestions(false);
      }
    };

    document.addEventListener('keydown', handleKeyDown, true);

    return () => {
      document.removeEventListener('keydown', handleKeyDown, true);
    };
  }, [suggestions, selectedIndex, showSuggestions, handleFileSelect, disabled]);

  useEffect(() => {
    if (selectedItemRef.current) {
      selectedItemRef.current.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
        inline: 'nearest'
      });
    }
  }, [selectedIndex]);

  // Highlight file when selection changes via keyboard navigation
  useEffect(() => {
    if (showSuggestions && suggestions.length > 0 && selectedIndex >= 0 && selectedIndex < suggestions.length) {
      onFileHover?.(suggestions[selectedIndex].path);
    } else if (!showSuggestions) {
      onFileHover?.(null);
    }
  }, [selectedIndex, showSuggestions, suggestions, onFileHover]);

  const loadWorkspaceFiles = async () => {
    try {
      const { workspaceService } = await import('./workspaceService');
      const tid = threadId?.trim() || undefined;
      const data = await workspaceService.getWorkspaceTree(false, tid);
      const files = extractFiles(data.tree || []);
      setAllFiles(files);
    } catch (error) {
      if (!usedMockRef.current) {
        useMockData();
      }
    }
  };

  const useMockData = () => {
    const mockFiles = [
      { name: 'top_opportunities_arkansas.txt', path: 'cuga_workspace/top_opportunities_arkansas.txt' },
      { name: 'top_10_opportunities_arkansas.txt', path: 'cuga_workspace/top_10_opportunities_arkansas.txt' },
      { name: 'top_3_opportunities_arkansas.txt', path: 'cuga_workspace/top_3_opportunities_arkansas.txt' },
      { name: 'analysis_report.md', path: 'cuga_workspace/analysis_report.md' },
      { name: 'data_export.json', path: 'cuga_workspace/data_export.json' },
    ];
    usedMockRef.current = true;
    setAllFiles(mockFiles);
  };

  const extractFiles = (nodes: FileNode[]): Array<{ name: string; path: string }> => {
    const files: Array<{ name: string; path: string }> = [];
    
    for (const node of nodes) {
      if (node.type === "file") {
        files.push({
          name: node.name,
          path: node.path
        });
      } else if (node.children) {
        files.push(...extractFiles(node.children));
      }
    }
    
    return files;
  };

  return (
    <>

      {showSuggestions && suggestions.length > 0 && position && (
        <div 
          ref={suggestionsRef}
          className="file-autocomplete"
          style={{ 
            top: `${position.top}px`, 
            left: `${position.left}px`,
          }}
          data-debug-position={JSON.stringify(position)}
        >
          <div className="file-autocomplete-header">
            <span>Workspace Files</span>
            <span className="file-count">{suggestions.length}</span>
          </div>
          <div className="file-autocomplete-list">
            {suggestions.map((file, index) => (
              <div
                key={file.path}
                ref={index === selectedIndex ? selectedItemRef : null}
                className={`file-autocomplete-item ${index === selectedIndex ? 'selected' : ''}`}
                onClick={(e) => {
                  e.preventDefault();
                  handleFileSelect(file);
                }}
                onMouseEnter={() => {
                  setSelectedIndex(index);
                  onFileHover?.(file.path);
                }}
                onMouseLeave={() => onFileHover?.(null)}
              >
                <FileText size={16} className="file-icon" />
                <div className="file-info">
                  <span className="file-name">{file.name}</span>
                  <span className="file-path">{file.path.startsWith("/") ? file.path : `./${file.path}`}</span>
                </div>
              </div>
            ))}
          </div>
          <div className="file-autocomplete-footer">
            <span className="hint">↑↓ navigate • Enter/Tab select • Esc close</span>
          </div>
        </div>
      )}
    </>
  );
}

