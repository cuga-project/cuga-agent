"use strict";
(self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] = self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] || []).push([["main-agentic_chat_src_CardManager_c"],{

/***/ "../agentic_chat/src/CardManager.css":
/*!*******************************************!*\
  !*** ../agentic_chat/src/CardManager.css ***!
  \*******************************************/
/***/ (function(__unused_webpack_module, __unused_webpack___webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/injectStylesIntoStyleTag.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/injectStylesIntoStyleTag.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleDomAPI.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleDomAPI.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertBySelector.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertBySelector.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/setAttributesWithoutAttributes.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/setAttributesWithoutAttributes.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertStyleElement.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertStyleElement.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleTagTransform.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleTagTransform.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_CardManager_css__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! !!../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!./CardManager.css */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/CardManager.css");

      
      
      
      
      
      
      
      
      

var options = {};

options.styleTagTransform = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default());
options.setAttributes = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default());
options.insert = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default().bind(null, "head");
options.domAPI = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default());
options.insertStyleElement = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default());

var update = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default()(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_CardManager_css__WEBPACK_IMPORTED_MODULE_6__["default"], options);




       /* unused harmony default export */ var __WEBPACK_DEFAULT_EXPORT__ = (_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_CardManager_css__WEBPACK_IMPORTED_MODULE_6__["default"] && _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_CardManager_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals ? _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_CardManager_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals : undefined);


/***/ }),

/***/ "../agentic_chat/src/CardManager.tsx":
/*!*******************************************!*\
  !*** ../agentic_chat/src/CardManager.tsx ***!
  \*******************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var marked__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! marked */ "../node_modules/.pnpm/marked@16.3.0/node_modules/marked/lib/marked.esm.js");
/* harmony import */ var _CardManager_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./CardManager.css */ "../agentic_chat/src/CardManager.css");
/* harmony import */ var _CustomResponseStyles_css__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ./CustomResponseStyles.css */ "../agentic_chat/src/CustomResponseStyles.css");
/* harmony import */ var _task_status_component__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! ./task_status_component */ "../agentic_chat/src/task_status_component.tsx");
/* harmony import */ var _action_status_component__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! ./action_status_component */ "../agentic_chat/src/action_status_component.tsx");
/* harmony import */ var _coder_agent_output__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! ./coder_agent_output */ "../agentic_chat/src/coder_agent_output.tsx");
/* harmony import */ var _app_analyzer_component__WEBPACK_IMPORTED_MODULE_7__ = __webpack_require__(/*! ./app_analyzer_component */ "../agentic_chat/src/app_analyzer_component.tsx");
/* harmony import */ var _task_decomposition__WEBPACK_IMPORTED_MODULE_8__ = __webpack_require__(/*! ./task_decomposition */ "../agentic_chat/src/task_decomposition.tsx");
/* harmony import */ var _shortlister__WEBPACK_IMPORTED_MODULE_9__ = __webpack_require__(/*! ./shortlister */ "../agentic_chat/src/shortlister.tsx");
/* harmony import */ var _generic_component__WEBPACK_IMPORTED_MODULE_10__ = __webpack_require__(/*! ./generic_component */ "../agentic_chat/src/generic_component.tsx");
/* harmony import */ var _action_agent__WEBPACK_IMPORTED_MODULE_11__ = __webpack_require__(/*! ./action_agent */ "../agentic_chat/src/action_agent.tsx");
/* harmony import */ var _qa_agent__WEBPACK_IMPORTED_MODULE_12__ = __webpack_require__(/*! ./qa_agent */ "../agentic_chat/src/qa_agent.tsx");
/* harmony import */ var _Followup__WEBPACK_IMPORTED_MODULE_13__ = __webpack_require__(/*! ./Followup */ "../agentic_chat/src/Followup.tsx");
/* harmony import */ var _StreamingWorkflow__WEBPACK_IMPORTED_MODULE_14__ = __webpack_require__(/*! ./StreamingWorkflow */ "../agentic_chat/src/StreamingWorkflow.ts");
/* harmony import */ var _ToolReview__WEBPACK_IMPORTED_MODULE_15__ = __webpack_require__(/*! ./ToolReview */ "../agentic_chat/src/ToolReview.tsx");
/* harmony import */ var _PolicyBlockComponent__WEBPACK_IMPORTED_MODULE_16__ = __webpack_require__(/*! ./PolicyBlockComponent */ "../agentic_chat/src/PolicyBlockComponent.tsx");
/* harmony import */ var _PolicyPlaybookComponent__WEBPACK_IMPORTED_MODULE_17__ = __webpack_require__(/*! ./PolicyPlaybookComponent */ "../agentic_chat/src/PolicyPlaybookComponent.tsx");



// Simple ChatInstance interface (no Carbon dependency)



// Import components from CustomResponseExample














// Color constant for highlighting important information
const HIGHLIGHT_COLOR = "#4e00ec";
const getMemoriesCount = memories => {
  return Object.values(memories).reduce((total, facts) => {
    return total + (Array.isArray(facts) ? facts.length : 0);
  }, 0);
};

// Extend the global interface typing to include the new loader API

const CardManager = ({
  chatInstance,
  threadId
}) => {
  const [currentSteps, setCurrentSteps] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [currentCardId, setCurrentCardId] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [isProcessingComplete, setIsProcessingComplete] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [showDetails, setShowDetails] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({});
  const [isReasoningCollapsed, setIsReasoningCollapsed] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [hasFinalAnswer, setHasFinalAnswer] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [currentStepIndex, setCurrentStepIndex] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(0);
  const [isStopped, setIsStopped] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [viewMode, setViewMode] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("inplace");
  const [globalVariables, setGlobalVariables] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({});
  const [globalMemories, setGlobalMemories] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({});
  const [variablesHistory, setVariablesHistory] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [selectedAnswerId, setSelectedAnswerId] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [expandedCodePreviews, setExpandedCodePreviews] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({});
  // Loader for next step within this card is derived from processing state
  const cardRef = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)(null);
  const stepRefs = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)({});

  // Function to mark a step as completed
  const markStepCompleted = (0,react__WEBPACK_IMPORTED_MODULE_0__.useCallback)(stepId => {
    setCurrentSteps(prev => prev.map(step => step.id === stepId ? {
      ...step,
      completed: true
    } : step));
  }, []);

  // Initialize global interface

  // No cross-card loader logic needed; loader will be shown within the card while processing

  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    if (typeof window !== "undefined") {
      console.log("Setting up global aiSystemInterface");
      window.aiSystemInterface = {
        addStep: (title, content) => {
          console.log("🎯 addStep called:", title, content);
          console.log("🎯 Content type:", typeof content);
          console.log("🎯 Current steps before adding:", currentSteps.length);

          // If content is JSON string, try to parse and log it
          if (typeof content === "string" && (content.startsWith("{") || content.startsWith("["))) {
            try {
              const parsed = JSON.parse(content);
              console.log("🎯 Parsed content:", parsed);
              console.log("🎯 Has variables:", !!parsed.variables);
              console.log("🎯 Has memories:", !!parsed.memories);
              console.log("🎯 Variables keys:", parsed.variables ? Object.keys(parsed.variables) : []);
              console.log("🎯 Memories keys:", parsed.memories ? Object.keys(parsed.memories) : []);
            } catch (e) {
              console.log("🎯 Failed to parse content as JSON");
            }
          }
          const newStep = {
            id: `step-${Date.now()}-${Math.random()}`,
            title,
            content,
            expanded: true,
            isNew: true,
            timestamp: Date.now()
          };
          setCurrentSteps(prev => {
            console.log("🎯 setCurrentSteps called with prev length:", prev.length);
            // If this is the first step, start a new card
            if (prev.length === 0) {
              const newCardId = `card-${Date.now()}`;
              setCurrentCardId(newCardId);
              console.log("🎯 First step - creating new card:", newCardId);
              return [newStep];
            }
            // Otherwise, add to current card
            console.log("🎯 Adding to existing card");
            return [...prev, newStep];
          });

          // Handle in-place card switching vs append mode
          if (viewMode === "inplace") {
            if (currentSteps.length > 0) {
              setCurrentStepIndex(prev => prev + 1);
            } else {
              setCurrentStepIndex(0);
            }
          }

          // Auto-expand "Waiting for your input" components and collapse reasoning
          if (title === "SuggestHumanActions") {
            setShowDetails(prev => ({
              ...prev,
              [newStep.id]: true
            }));
            // Collapse reasoning process when user action is needed
            setIsReasoningCollapsed(true);
          }

          // Check if this is a final answer step (only Answer, not FinalAnswerAgent)
          if (title === "Answer") {
            console.log("🎯 Final answer detected, triggering reasoning collapse");
            setHasFinalAnswer(true);
            // Collapse reasoning immediately when final answer arrives
            setIsReasoningCollapsed(true);
            // Show details by default for final answer
            setShowDetails(prev => ({
              ...prev,
              [newStep.id]: true
            }));

            // Emit event to notify parent that final answer is complete
            setTimeout(() => {
              const event = new CustomEvent("finalAnswerComplete", {
                detail: {
                  stepId: newStep.id
                }
              });
              window.dispatchEvent(event);
              console.log("🎯 Emitted finalAnswerComplete event");
            }, 500);
          }
        },
        // No external loader toggle needed for within-card loading
        getAllSteps: () => currentSteps,
        stopProcessing: () => {
          setIsStopped(true);
          setIsProcessingComplete(true);
          setIsReasoningCollapsed(true);
          setShowDetails({});
          setExpandedCodePreviews({});
        },
        isProcessingStopped: () => isProcessingComplete,
        setProcessingComplete: isComplete => {
          setIsProcessingComplete(isComplete);
        },
        forceReset: () => {
          setCurrentSteps([]);
          setIsProcessingComplete(false);
          setCurrentCardId(null);
          setIsReasoningCollapsed(false);
          setHasFinalAnswer(false);
          setCurrentStepIndex(0);
          setIsStopped(false);
          setShowDetails({});
          setExpandedCodePreviews({});
          stepRefs.current = {};
          // Note: variablesHistory is preserved across conversations
        },
        hasStepWithTitle: title => {
          return currentSteps.some(step => step.title === title);
        }
      };
    }
  }, [currentSteps, currentCardId, isProcessingComplete, viewMode]);

  // Auto-scroll to latest step
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    if (currentSteps.length > 0) {
      const timeoutId = setTimeout(() => {
        const latestStep = currentSteps[currentSteps.length - 1];
        const latestStepRef = stepRefs.current[latestStep.id];
        if (latestStepRef) {
          latestStepRef.scrollIntoView({
            behavior: "smooth",
            block: "center"
          });
        } else if (cardRef.current) {
          // Fallback to container scroll if step ref not found
          cardRef.current.scrollIntoView({
            behavior: "smooth",
            block: "center"
          });
        }
      }, 100);
      return () => clearTimeout(timeoutId);
    }
  }, [currentSteps.length]);

  // Cleanup step refs on unmount
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    return () => {
      stepRefs.current = {};
    };
  }, []);

  // Extract variables from final answer steps and track by turn
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    console.log("[Variables Debug] Processing steps, total:", currentSteps.length);
    const newHistory = [];
    let turnNumber = 0;
    currentSteps.forEach(step => {
      console.log("[Variables Debug] Step:", step.title, "Type:", typeof step.content);

      // Only process Answer or FinalAnswerAgent steps
      if (step.title !== "Answer" && step.title !== "FinalAnswerAgent") {
        return;
      }
      console.log("[Variables Debug] Processing Answer/FinalAnswerAgent step");
      try {
        let parsedContent;
        let variables = {};
        let memories = {};
        if (typeof step.content === "string") {
          try {
            parsedContent = JSON.parse(step.content);
            console.log("[Variables Debug] Parsed JSON content:", parsedContent);

            // Check if we have variables in the parsed content
            if (parsedContent.data !== undefined && parsedContent.variables) {
              variables = parsedContent.variables;
              console.log("[Variables Debug] Found variables in data:", variables);
            } else if (parsedContent.variables) {
              variables = parsedContent.variables;
              console.log("[Variables Debug] Found variables directly:", variables);
            }
            if (parsedContent.memories && typeof parsedContent.memories === "object" && !Array.isArray(parsedContent.memories)) {
              memories = parsedContent.memories;
              console.log("[Variables Debug] Found memories:", memories);
            }
          } catch (e) {
            console.log("[Variables Debug] Failed to parse JSON:", e);
          }
        } else if (step.content && typeof step.content === "object") {
          const contentWithState = step.content;
          if (contentWithState.variables) {
            variables = contentWithState.variables;
            console.log("[Variables Debug] Found variables in object:", variables);
          }
          if (contentWithState.memories && typeof contentWithState.memories === "object" && !Array.isArray(contentWithState.memories)) {
            memories = contentWithState.memories;
            console.log("[Variables Debug] Found memories in object:", memories);
          }
        }
        const memoriesCount = getMemoriesCount(memories);

        // Only add to history if this step has variables or memories
        if (Object.keys(variables).length > 0 || memoriesCount > 0) {
          console.log("[Variables Debug] Adding to history with", Object.keys(variables).length, "variables");
          newHistory.push({
            id: step.id,
            title: `Turn ${turnNumber}`,
            timestamp: step.timestamp,
            variables: variables,
            memories: memories
          });
          turnNumber++;
        } else {
          console.log("[Variables Debug] No variables found in this step");
        }
      } catch (e) {
        console.log("[Variables Debug] Error processing step:", e);
      }
    });

    // Preserve previously captured history during in-flight runs and only merge
    // newly completed Answer/FinalAnswerAgent entries.
    setVariablesHistory(prev => {
      // No completed answers in this render cycle; keep existing sidebar data visible.
      if (newHistory.length === 0) {
        return prev;
      }
      const merged = [...prev];
      let hasChanges = false;
      newHistory.forEach(entry => {
        const existingIndex = merged.findIndex(item => item.id === entry.id);
        if (existingIndex === -1) {
          merged.push(entry);
          hasChanges = true;
          return;
        }
        const existing = merged[existingIndex];
        const entryChanged = existing.title !== entry.title || existing.timestamp !== entry.timestamp || JSON.stringify(existing.variables) !== JSON.stringify(entry.variables) || JSON.stringify(existing.memories) !== JSON.stringify(entry.memories);
        if (entryChanged) {
          merged[existingIndex] = entry;
          hasChanges = true;
        }
      });
      if (hasChanges) {
        console.log("Variables history updated: merged new entries", prev.length, "->", merged.length);
        return merged;
      }
      return prev;
    });

    // Update selectedAnswerId based on available history
    setSelectedAnswerId(currentSelectedId => {
      // If this cycle produced completed Answer/FinalAnswerAgent entries, select latest.
      if (newHistory.length > 0) {
        const latestEntryId = newHistory[newHistory.length - 1].id;
        if (currentSelectedId === latestEntryId) {
          return currentSelectedId;
        }
        console.log("Auto-selecting most recent turn:", newHistory[newHistory.length - 1].title);
        return latestEntryId;
      }

      // No new history from current steps, check if we have existing history
      // This happens when forceReset is called - we want to preserve selection
      if (variablesHistory.length > 0) {
        if (currentSelectedId && variablesHistory.find(e => e.id === currentSelectedId)) {
          // Keep current selection if it exists in existing history
          return currentSelectedId;
        }
        // Auto-select most recent from existing history
        console.log("Preserving selection from existing history:", variablesHistory[variablesHistory.length - 1].title);
        return variablesHistory[variablesHistory.length - 1].id;
      }

      // No history at all
      return null;
    });
  }, [currentSteps]);

  // Update globalVariables based on selected answer
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    if (selectedAnswerId) {
      const selected = variablesHistory.find(e => e.id === selectedAnswerId);
      if (selected) {
        setGlobalVariables(selected.variables);
        setGlobalMemories(selected.memories);
      }
    } else if (variablesHistory.length > 0) {
      // Default to most recent
      setGlobalVariables(variablesHistory[variablesHistory.length - 1].variables);
      setGlobalMemories(variablesHistory[variablesHistory.length - 1].memories);
    } else {
      setGlobalVariables({});
      setGlobalMemories({});
    }
  }, [selectedAnswerId, variablesHistory]);

  // Emit variables updates to App.tsx
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    const event = new CustomEvent("variablesUpdate", {
      detail: {
        variables: globalVariables,
        memories: globalMemories,
        history: variablesHistory
      }
    });
    window.dispatchEvent(event);
  }, [globalVariables, globalMemories, variablesHistory]);

  // Toggle code preview expansion
  const toggleCodePreview = (0,react__WEBPACK_IMPORTED_MODULE_0__.useCallback)(stepId => {
    setExpandedCodePreviews(prev => ({
      ...prev,
      [stepId]: !prev[stepId]
    }));
  }, []);

  // Helper function to check if a step should be rendered
  const shouldRenderStep = (0,react__WEBPACK_IMPORTED_MODULE_0__.useCallback)(step => {
    if (step.title === "simple_text") {
      return true;
    }

    // Parse content for description
    let parsedContent;
    try {
      if (typeof step.content === "string") {
        try {
          parsedContent = JSON.parse(step.content);
          const keys = Object.keys(parsedContent);
          if (keys.length === 1 && keys[0] === "data") {
            parsedContent = parsedContent.data;
          }
        } catch (e) {
          parsedContent = step.content;
        }
      } else {
        parsedContent = step.content;
      }
    } catch (error) {
      parsedContent = step.content;
    }
    const description = getCaseDescription(step.id, step.title, parsedContent);
    return description !== null;
  }, []);

  // Function to generate natural language descriptions for each case
  const getCaseDescription = (stepId, stepTitle, parsedContent) => {
    switch (stepTitle) {
      case "PlanControllerAgent":
        if (parsedContent.subtasks_progress && parsedContent.next_subtask) {
          const completed = parsedContent.subtasks_progress.filter(status => status === "completed").length;
          const total = parsedContent.subtasks_progress.length;
          if (total === 0) {
            return `I'm managing the overall task progress. There's <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">one next task</span>. ${parsedContent.conclude_task ? "The task is ready to be concluded." : `Next up: <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${parsedContent.next_subtask}</span>`}`;
          }
          return `I'm managing the overall task progress. Currently <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${completed} out of ${total} subtasks</span> are completed. ${parsedContent.conclude_task ? "The task is ready to be concluded." : `Next up: <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${parsedContent.next_subtask}</span>`}`;
        }
        return "I'm analyzing the task structure and planning the execution approach.";
      case "TaskDecompositionAgent":
        const taskCount = parsedContent.task_decomposition?.length || 0;
        return `I've broken down your request into <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${taskCount} manageable steps</span>. Each step is designed to work with specific applications and accomplish a specific part of your overall goal.`;
      case "APIPlannerAgent":
        if (parsedContent.action && (parsedContent.action_input_coder_agent || parsedContent.action_input_shortlisting_agent || parsedContent.action_input_conclude_task)) {
          const actionType = parsedContent.action;
          if (actionType === "CoderAgent") {
            return `I'm preparing to write code for you. The task involves: <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${parsedContent.action_input_coder_agent?.task_description || "Code generation task"}</span>`;
          } else if (actionType === "ApiShortlistingAgent") {
            const taskDesc = parsedContent.action_input_shortlisting_agent?.task_description;
            if (taskDesc) {
              const preview = taskDesc.length > 60 ? taskDesc.substring(0, 60) + "..." : taskDesc;
              return `I'm analyzing available APIs, <span style="color:${HIGHLIGHT_COLOR}; font-weight:500;">${preview}</span>`;
            }
            return `I'm analyzing available APIs to find the best options for your request. This will help me understand what tools are available to accomplish your task.`;
          } else if (actionType === "ConcludeTask") {
            const taskDesc = parsedContent.action_input_conclude_task?.final_response;
            if (taskDesc) {
              const preview = taskDesc.length > 60 ? taskDesc.substring(0, 60) + "..." : taskDesc;
              return `I'm ready to provide you with the final answer based on all the work completed so far. <span style="color:${HIGHLIGHT_COLOR}; font-weight:500;">${preview}</span>`;
            }
            return `I'm ready to provide you with the final answer based on all the work completed so far.`;
          }
        }
        return "I'm reflecting on the code and planning the next steps in the workflow.";
      case "Policy":
        // Handle all policy events with unified display
        if (parsedContent && parsedContent.type === "policy") {
          const policyName = parsedContent.policy_name || "Policy";
          const policyType = parsedContent.policy_type || "unknown";
          const isBlocked = parsedContent.policy_blocked || false;
          const icon = isBlocked ? "🛑" : policyType === "playbook" ? "📖" : policyType === "tool_guide" ? "🔧" : policyType === "tool_approval" ? "✋" : "📋";
          const color = isBlocked ? "#ff6b6b" : "#3b82f6";
          const action = isBlocked ? "Blocked" : policyType === "playbook" ? "Activated" : policyType === "tool_guide" ? "Enriched" : policyType === "tool_approval" ? "Requires Approval" : "Active";
          return `<span style="color: ${color}; font-weight: 600;">${icon} Policy ${action}</span> - <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${policyName}</span> (${policyType})`;
        }
        return "Policy enforcement in progress...";
      case "PolicyBlock":
        // Legacy support
        if (parsedContent && parsedContent.type === "policy_block") {
          const policyName = parsedContent.metadata?.policy_name || "Security Policy";
          return `<span style="color: #ff6b6b; font-weight: 600;">🛡️ Intent Blocked</span> - Your request was blocked by <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${policyName}</span> for security reasons.`;
        }
        return "Policy enforcement in progress...";
      case "PolicyPlaybook":
        // Legacy support
        if (parsedContent && parsedContent.type === "policy_playbook") {
          const playbookName = parsedContent.metadata?.policy_name || "Workflow Playbook";
          return `<span style="color: #3b82f6; font-weight: 600;">📖 Playbook Activated</span> - Following <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${playbookName}</span> to guide you through this process.`;
        }
        return "A security policy has been applied to your request.";
      case "CodeAgent":
        // Check if we have meaningful content
        const hasCode = parsedContent.code && parsedContent.code.trim().length > 0;
        const hasOutput = parsedContent.execution_output && parsedContent.execution_output.trim().length > 0;
        if (hasCode || hasOutput) {
          // Handle case where we have code
          const codeLines = hasCode ? parsedContent.code.split("\n").length : 0;
          const allCodeLines = hasCode ? parsedContent.code.split("\n") : [];
          const isExpanded = expandedCodePreviews[stepId];
          const maxPreviewLines = 4;
          const codePreviewLines = isExpanded ? allCodeLines : allCodeLines.slice(0, maxPreviewLines);
          const hasMoreLines = codeLines > maxPreviewLines;
          return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, hasCode && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, parsedContent.execution_output ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "I've generated and executed", " ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
            style: {
              color: HIGHLIGHT_COLOR,
              fontWeight: 600
            }
          }, codeLines, " lines of code"), ". Code preview:") : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "I've generated", " ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
            style: {
              color: HIGHLIGHT_COLOR,
              fontWeight: 600
            }
          }, codeLines, " lines of code"), " to accomplish your request. Preview:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
            style: {
              color: "#6366f1",
              fontFamily: "monospace",
              background: "#eef2ff",
              padding: "8px 10px",
              borderRadius: "4px",
              fontSize: "11px",
              lineHeight: "1.4",
              marginTop: "6px",
              borderLeft: "3px solid #6366f1",
              position: "relative",
              overflowX: "auto",
              maxWidth: "100%"
            }
          }, codePreviewLines.map((line, idx) => {
            return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
              key: idx,
              style: {
                whiteSpace: "pre"
              }
            }, line || "\u00A0");
          }), !isExpanded && hasMoreLines && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
            style: {
              color: "#94a3b8"
            }
          }, "..."), hasMoreLines && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
            onClick: e => {
              e.stopPropagation();
              toggleCodePreview(stepId);
            },
            style: {
              position: "absolute",
              right: "8px",
              bottom: "8px",
              background: "#6366f1",
              color: "white",
              border: "none",
              borderRadius: "3px",
              padding: "2px 8px",
              fontSize: "10px",
              cursor: "pointer",
              fontFamily: "sans-serif"
            },
            onMouseOver: e => e.currentTarget.style.background = "#4f46e5",
            onMouseOut: e => e.currentTarget.style.background = "#6366f1"
          }, isExpanded ? "▲ Less" : "▼ More"))), !hasCode && parsedContent.execution_output && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Code execution completed. Output:"), parsedContent.execution_output && (() => {
            const output = parsedContent.execution_output.trim();
            const outputLines = output.split("\n");
            const isOutputExpanded = expandedCodePreviews[`${stepId}_output`];
            const maxPreviewLines = 3;
            const previewLines = isOutputExpanded ? outputLines : outputLines.slice(0, maxPreviewLines);
            const hasMoreOutput = outputLines.length > maxPreviewLines || output.length > 300;
            return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
              style: {
                marginTop: "8px"
              }
            }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
              style: {
                fontSize: "12px",
                color: "#059669",
                fontWeight: 500,
                marginBottom: "4px"
              }
            }, "Execution Output:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
              style: {
                color: "#065f46",
                fontFamily: "monospace",
                background: "#f0fdf4",
                padding: "8px 10px",
                borderRadius: "4px",
                fontSize: "11px",
                lineHeight: "1.4",
                borderLeft: "3px solid #10b981",
                position: "relative",
                maxHeight: isOutputExpanded ? "none" : "150px",
                overflow: isOutputExpanded ? "auto" : "hidden",
                overflowX: "auto",
                maxWidth: "100%"
              }
            }, previewLines.map((line, idx) => {
              return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                key: idx,
                style: {
                  whiteSpace: "pre"
                }
              }, line || "\u00A0");
            }), !isOutputExpanded && hasMoreOutput && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
              style: {
                color: "#94a3b8"
              }
            }, "..."), hasMoreOutput && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
              onClick: e => {
                e.stopPropagation();
                toggleCodePreview(`${stepId}_output`);
              },
              style: {
                position: "absolute",
                right: "8px",
                bottom: "8px",
                background: "#10b981",
                color: "white",
                border: "none",
                borderRadius: "3px",
                padding: "2px 8px",
                fontSize: "10px",
                cursor: "pointer",
                fontFamily: "sans-serif"
              },
              onMouseOver: e => e.currentTarget.style.background = "#059669",
              onMouseOut: e => e.currentTarget.style.background = "#10b981"
            }, isOutputExpanded ? "▲ Less" : "▼ More")));
          })());
        }
        // Return null to skip rendering empty CodeAgent events
        return null;
      case "CodeAgent_Reasoning":
        // Text response from LLM (no code)
        if (typeof parsedContent === "string" && parsedContent) {
          const isExpanded = expandedCodePreviews[stepId];
          const maxPreviewLength = 200;
          const hasMoreContent = parsedContent.length > maxPreviewLength;
          const displayContent = isExpanded ? parsedContent : parsedContent.substring(0, maxPreviewLength);
          return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "I'm reasoning about your request:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
            style: {
              color: "#475569",
              fontStyle: "italic",
              background: "#f8fafc",
              padding: "8px 10px",
              borderRadius: "4px",
              fontSize: "12px",
              lineHeight: "1.5",
              marginTop: "6px",
              borderLeft: "3px solid #94a3b8",
              position: "relative",
              whiteSpace: "pre-wrap",
              overflowX: "auto",
              maxWidth: "100%"
            }
          }, displayContent, !isExpanded && hasMoreContent && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
            style: {
              color: "#94a3b8"
            }
          }, "..."), hasMoreContent && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
            onClick: e => {
              e.stopPropagation();
              toggleCodePreview(stepId);
            },
            style: {
              position: "absolute",
              right: "8px",
              bottom: "8px",
              background: "#64748b",
              color: "white",
              border: "none",
              borderRadius: "3px",
              padding: "2px 8px",
              fontSize: "10px",
              cursor: "pointer",
              fontFamily: "sans-serif"
            },
            onMouseOver: e => e.currentTarget.style.background = "#475569",
            onMouseOut: e => e.currentTarget.style.background = "#64748b"
          }, isExpanded ? "▲ Less" : "▼ More")));
        }
        // Return null to skip rendering empty CodeAgent_Reasoning events
        return null;
      case "ShortlisterAgent":
        if (parsedContent.result) {
          const apiCount = parsedContent.result.length;
          const topResult = parsedContent.result[0];
          const topScore = topResult?.relevance_score || 0;
          const apiName = topResult?.name || topResult?.title || "Unknown API";
          const truncatedName = apiName.length > 30 ? apiName.substring(0, 30) + "..." : apiName;
          return `I've analyzed and shortlisted <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${apiCount} relevant APIs</span> for your request. The top match is <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${truncatedName}</span> with a <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${Math.round(topScore * 100)}% relevance score</span>.`;
        }
        return "I'm analyzing available APIs to find the most relevant ones for your request.";
      case "TaskAnalyzerAgent":
        if (parsedContent && Array.isArray(parsedContent)) {
          const appNames = parsedContent.map(app => `<span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${app.name}</span>`).join(", ");
          return `I've identified <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${parsedContent.length} integrated applications</span> that can help with your request: ${appNames}. These apps are ready to be used in the workflow.`;
        }
        return "I'm analyzing the available applications to understand what tools we can use.";
      case "PlannerAgent":
        return `I'm planning the next action in the workflow. This involves determining the best approach to continue working on your request.`;
      case "QaAgent":
        if (parsedContent.name && parsedContent.answer) {
          return `I've analyzed the question "<span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${parsedContent.name}</span>" and provided a comprehensive answer with <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${parsedContent.answer.split(" ").length} words</span>.`;
        }
        return "I'm processing a question and preparing a detailed answer.";
      case "FinalAnswerAgent":
        if (parsedContent.final_answer) {
          return `I've completed your request and prepared the final answer.`;
        }
        return "I'm preparing the final answer to your request.";
      case "ReuseAgent":
        if (typeof parsedContent === "string") return parsedContent.split("\n")[0];
        return "Save and reuse operation completed.";
      case "SuggestHumanActions":
        if (parsedContent.action_id) {
          return "I'm waiting for your input to continue. Please review the suggested action and let me know how you'd like to proceed.";
        }
        return "I'm preparing suggestions for your next action.";
      case "APICodePlannerAgent":
        const contentPreview = typeof parsedContent === "string" ? parsedContent : JSON.stringify(parsedContent);
        const preview = contentPreview.length > 80 ? contentPreview.substring(0, 80) + "..." : contentPreview;
        return `I've generated a plan for the coding agent to follow. Plan preview: <span style="color:${HIGHLIGHT_COLOR}; font-weight:500;">${preview}</span>`;
      default:
        return "";
    }
  };

  // Memoized function to render the appropriate component based on step title and content
  const renderStepContent = (0,react__WEBPACK_IMPORTED_MODULE_0__.useCallback)((step, allSteps) => {
    try {
      let parsedContent;
      if (typeof step.content === "string") {
        try {
          parsedContent = JSON.parse(step.content);
          const keys = Object.keys(parsedContent);
          console.log(`[${step.title}] Raw parsed content:`, parsedContent);
          console.log(`[${step.title}] Has data:`, parsedContent.data !== undefined);
          console.log(`[${step.title}] Has variables:`, !!parsedContent.variables);

          // Check if we have variables in the parsed content
          if (parsedContent.data !== undefined && parsedContent.variables) {
            console.log(`[${step.title}] Processing with variables...`);

            // Parse data if it's a JSON string
            let dataValue = parsedContent.data;
            let extractedPolicies = parsedContent.active_policies || [];
            if (typeof dataValue === "string") {
              try {
                const parsedData = JSON.parse(dataValue);
                // Check if the parsed data is a policy object
                if (parsedData && typeof parsedData === "object" && parsedData.type === "policy") {
                  extractedPolicies = [parsedData];
                  // Use the content as final_answer if it's a policy
                  dataValue = parsedData.content || parsedData.response_content || dataValue;
                }
              } catch (e) {
                // Not JSON, keep as string
              }
            } else if (dataValue && typeof dataValue === "object" && dataValue.type === "policy") {
              // Data is already a policy object
              extractedPolicies = [dataValue];
              dataValue = dataValue.content || dataValue.response_content || "";
            }

            // For Answer step with variables: treat data as final_answer
            if (step.title === "Answer" || step.title === "FinalAnswerAgent") {
              parsedContent = {
                final_answer: dataValue,
                variables: parsedContent.variables,
                active_policies: extractedPolicies
              };
              console.log(`[${step.title}] Converted to final_answer format:`, parsedContent);
            } else if (typeof parsedContent.data === "object" && !Array.isArray(parsedContent.data)) {
              // Keep both data and variables if data is an object
              parsedContent = {
                ...parsedContent.data,
                variables: parsedContent.variables,
                active_policies: extractedPolicies
              };
            } else {
              // If data is not an object, keep as is with variables
              parsedContent = {
                data: dataValue,
                variables: parsedContent.variables,
                active_policies: extractedPolicies
              };
            }
          } else if (keys.length === 1 && keys[0] === "data") {
            // Only data, no variables - check if data is a policy JSON string
            let dataValue = parsedContent.data;
            let extractedPolicies = [];
            if (typeof dataValue === "string") {
              try {
                const parsedData = JSON.parse(dataValue);
                if (parsedData && typeof parsedData === "object" && parsedData.type === "policy") {
                  extractedPolicies = [parsedData];
                  // For playbook, the content is the guide, not the final answer
                  // The final answer should come from elsewhere or be empty
                  const isPlaybook = parsedData.policy_type === "playbook";
                  parsedContent = {
                    final_answer: isPlaybook ? "" : parsedData.content || parsedData.response_content || "",
                    active_policies: extractedPolicies
                  };
                } else {
                  parsedContent = dataValue;
                }
              } catch (e) {
                parsedContent = dataValue;
              }
            } else if (dataValue && typeof dataValue === "object" && dataValue.type === "policy") {
              extractedPolicies = [dataValue];
              const isPlaybook = dataValue.policy_type === "playbook";
              parsedContent = {
                final_answer: isPlaybook ? "" : dataValue.content || dataValue.response_content || "",
                active_policies: extractedPolicies
              };
            } else {
              parsedContent = dataValue;
            }
          } else if (parsedContent.active_policies) {
            // Preserve active_policies even if no variables
            parsedContent = {
              ...parsedContent,
              active_policies: parsedContent.active_policies
            };
          }
        } catch (e) {
          parsedContent = step.content; // fallback
        }
      } else {
        parsedContent = step.content; // already an object
      }
      let outputElements = [];
      // Only render ToolCallFlowDisplay for non-SuggestHumanActions steps
      // SuggestHumanActions uses FollowupAction component which handles its own display
      if (step.title !== "SuggestHumanActions" && parsedContent && parsedContent.additional_data && parsedContent.additional_data.tool) {
        const newElem = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_ToolReview__WEBPACK_IMPORTED_MODULE_15__["default"], {
          toolData: parsedContent.additional_data.tool
        });
        outputElements.push(newElem);
      }
      let mainElement = null;
      switch (step.title) {
        case "PlanControllerAgent":
          if (parsedContent.subtasks_progress && parsedContent.next_subtask) {
            mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_task_status_component__WEBPACK_IMPORTED_MODULE_4__["default"], {
              taskData: parsedContent
            });
          }
          break;
        case "TaskDecompositionAgent":
          mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_task_decomposition__WEBPACK_IMPORTED_MODULE_8__["default"], {
            decompositionData: parsedContent
          });
          break;
        case "APIPlannerAgent":
          if (parsedContent.action && (parsedContent.action_input_coder_agent || parsedContent.action_input_shortlisting_agent || parsedContent.action_input_conclude_task)) {
            mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_action_status_component__WEBPACK_IMPORTED_MODULE_5__["default"], {
              actionData: parsedContent
            });
          } else {
            mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_generic_component__WEBPACK_IMPORTED_MODULE_10__["default"], {
              title: "Code Reflection",
              content: parsedContent
            });
          }
          break;
        case "CodeAgent":
          if (parsedContent.code || parsedContent.execution_output) {
            mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_coder_agent_output__WEBPACK_IMPORTED_MODULE_6__["default"], {
              coderData: parsedContent
            });
          }
          break;
        case "Policy":
          // Handle all policy events with unified JSON display
          if (parsedContent && parsedContent.type === "policy") {
            const policyType = parsedContent.policy_type || "unknown";
            const policyName = parsedContent.policy_name || "Unknown Policy";
            const isBlocked = parsedContent.policy_blocked || false;
            mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
              style: {
                padding: "16px",
                backgroundColor: isBlocked ? "#fee" : "#e3f2fd",
                border: `2px solid ${isBlocked ? "#d32f2f" : "#2196f3"}`,
                borderRadius: "8px",
                marginTop: "8px"
              }
            }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
              style: {
                display: "flex",
                alignItems: "center",
                gap: "8px",
                marginBottom: "12px"
              }
            }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
              style: {
                fontSize: "24px"
              }
            }, isBlocked ? "🛑" : policyType === "playbook" ? "📖" : policyType === "tool_guide" ? "🔧" : policyType === "tool_approval" ? "✋" : "📋"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
              style: {
                margin: 0,
                color: isBlocked ? "#d32f2f" : "#1976d2"
              }
            }, isBlocked ? "Policy Blocked" : "Policy Active", ": ", policyName)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
              style: {
                backgroundColor: "#fff",
                padding: "12px",
                borderRadius: "4px",
                fontSize: "13px",
                fontFamily: "monospace",
                maxHeight: "400px",
                overflow: "auto"
              }
            }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("pre", {
              style: {
                margin: 0,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word"
              }
            }, JSON.stringify(parsedContent, null, 2))));
          }
          break;
        case "PolicyBlock":
          // Legacy support - redirect to Policy
          if (parsedContent && parsedContent.type === "policy_block") {
            mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_PolicyBlockComponent__WEBPACK_IMPORTED_MODULE_16__["default"], {
              data: parsedContent
            });
          }
          break;
        case "PolicyPlaybook":
          // Legacy support - redirect to Policy
          if (parsedContent && parsedContent.type === "policy_playbook") {
            mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_PolicyPlaybookComponent__WEBPACK_IMPORTED_MODULE_17__["default"], {
              data: parsedContent
            });
          }
          break;
        case "CodeAgent_Reasoning":
          // Display reasoning text in a clean format
          if (typeof parsedContent === "string" || parsedContent) {
            const textContent = typeof parsedContent === "string" ? parsedContent : JSON.stringify(parsedContent, null, 2);
            mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
              style: {
                fontSize: "14px",
                lineHeight: "1.6",
                color: "#475569",
                padding: "12px",
                backgroundColor: "#f8fafc",
                borderRadius: "6px",
                border: "1px solid #e2e8f0",
                fontStyle: "italic"
              },
              dangerouslySetInnerHTML: {
                __html: (0,marked__WEBPACK_IMPORTED_MODULE_1__.marked)(textContent)
              }
            });
          }
          break;
        case "ShortlisterAgent":
          if (parsedContent) {
            mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_shortlister__WEBPACK_IMPORTED_MODULE_9__["default"], {
              shortlisterData: parsedContent
            });
          }
          break;
        case "WaitForResponse":
          return null;
        case "TaskAnalyzerAgent":
          if (parsedContent && Array.isArray(parsedContent)) {
            mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_app_analyzer_component__WEBPACK_IMPORTED_MODULE_7__["default"], {
              appData: parsedContent
            });
          }
          break;
        case "PlannerAgent":
          if (parsedContent) {
            mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_action_agent__WEBPACK_IMPORTED_MODULE_11__["default"], {
              agentData: parsedContent
            });
          }
          break;
        case "simple_text_box":
          if (parsedContent) {
            mainElement = parsedContent;
          }
          break;
        case "QaAgent":
          if (parsedContent) {
            mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_qa_agent__WEBPACK_IMPORTED_MODULE_12__["default"], {
              qaData: parsedContent
            });
          }
          break;
        case "Answer":
        case "FinalAnswerAgent":
          if (parsedContent) {
            console.log("Answer/FinalAnswerAgent - parsedContent:", parsedContent);

            // Check if data field contains a policy object - if so, extract answer and policies
            let answerText = parsedContent.final_answer || (typeof parsedContent === "string" ? parsedContent : null);
            let activePolicies = parsedContent.active_policies || [];
            if (parsedContent.data && typeof parsedContent.data === "object" && parsedContent.data.type === "policy") {
              // Data is a policy object - extract policies and check for answer
              const policyData = parsedContent.data;
              activePolicies = [policyData];

              // For playbook, answer comes separately, but for other policies, content might be the answer
              if (policyData.policy_type !== "playbook" && !answerText) {
                answerText = policyData.content || policyData.metadata?.response_content || null;
              }
            }

            // Find playbook policy if any
            const playbookPolicy = activePolicies.find(policy => policy.policy_type === "playbook");

            // Extract playbook guide content
            let playbookGuideContent = "";
            if (playbookPolicy) {
              playbookGuideContent = playbookPolicy.metadata?.playbook_content || playbookPolicy.metadata?.playbook_guidance || "";
              if (!playbookGuideContent && playbookPolicy.content) {
                const content = playbookPolicy.content;
                const cleanedContent = content.replace(/^## 📖[^\n]*\n\n?/, "");
                playbookGuideContent = cleanedContent;
              }
            }

            // For Answer events with playbook policy, ALWAYS look for FinalAnswerAgent step to get the actual final answer
            // The FinalAnswerAgent's final_answer should take precedence over any other answer text
            // IMPORTANT: For playbook policies, the answer should NEVER be the playbook guide content
            if (step.title === "Answer" && playbookPolicy && allSteps) {
              console.log("Answer - Playbook policy detected, looking for FinalAnswerAgent step in", allSteps.length, "steps");
              const finalAnswerStep = allSteps.find(s => s.title === "FinalAnswerAgent");
              if (finalAnswerStep) {
                console.log("Answer - Found FinalAnswerAgent step:", finalAnswerStep.id);
                try {
                  let finalAnswerContent;
                  if (typeof finalAnswerStep.content === "string") {
                    finalAnswerContent = JSON.parse(finalAnswerStep.content);
                  } else {
                    finalAnswerContent = finalAnswerStep.content;
                  }
                  console.log("Answer - FinalAnswerAgent parsed content:", finalAnswerContent);
                  const finalAnswerFromAgent = finalAnswerContent.final_answer || null;
                  if (finalAnswerFromAgent) {
                    // Prioritize FinalAnswerAgent's final_answer over any other answer text
                    answerText = finalAnswerFromAgent;
                    console.log("Answer - Using FinalAnswerAgent's final_answer:", answerText);
                  } else {
                    console.log("Answer - FinalAnswerAgent step found but no final_answer field");
                    // Don't use playbook content as answer - leave answerText as null/empty
                    answerText = null;
                  }
                } catch (e) {
                  console.log("Answer - Error parsing FinalAnswerAgent content:", e);
                  // Don't use playbook content as answer - leave answerText as null/empty
                  answerText = null;
                }
              } else {
                console.log("Answer - Playbook policy found but no FinalAnswerAgent step available yet");
                // Don't use playbook content as answer - leave answerText as null/empty
                answerText = null;
              }
            } else if (step.title === "Answer" && playbookPolicy) {
              // Playbook policy but no allSteps available - don't use playbook content as answer
              console.log("Answer - Playbook policy found but allSteps not available");
              answerText = null;
            }
            console.log("Answer/FinalAnswerAgent - answerText:", answerText);
            console.log("Answer/FinalAnswerAgent - activePolicies:", activePolicies);
            console.log("Answer/FinalAnswerAgent - playbookGuideContent:", playbookGuideContent);
            if (answerText) {
              let renderedContent;
              if (typeof answerText === "string") {
                // Check if content is in markdown HTML code block (```html ... ```)
                const htmlCodeBlockMatch = answerText.match(/^```html\s*\n([\s\S]*?)\n```$/);
                if (htmlCodeBlockMatch) {
                  // Extract HTML from code block and render as HTML
                  renderedContent = htmlCodeBlockMatch[1];
                } else if (/<[a-z][\s\S]*>/i.test(answerText)) {
                  // Direct HTML content
                  renderedContent = answerText;
                } else {
                  // Regular markdown content
                  renderedContent = (0,marked__WEBPACK_IMPORTED_MODULE_1__.marked)(answerText);
                }
              } else {
                renderedContent = (0,marked__WEBPACK_IMPORTED_MODULE_1__.marked)(String(answerText));
              }
              mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                style: {
                  fontSize: "14px",
                  lineHeight: "1.6",
                  color: "#1e293b",
                  marginBottom: playbookGuideContent || activePolicies.length > 0 ? "20px" : "0"
                },
                dangerouslySetInnerHTML: {
                  __html: renderedContent
                }
              }), playbookGuideContent && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                style: {
                  marginTop: "20px",
                  paddingTop: "20px",
                  borderTop: "1px solid #e5e7eb"
                }
              }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                style: {
                  fontSize: "14px",
                  fontWeight: 600,
                  color: "#0ea5e9",
                  marginBottom: "12px",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px"
                }
              }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
                style: {
                  fontSize: "18px"
                }
              }, "\uD83D\uDCD6"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Task Guide")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("details", {
                style: {
                  position: "relative"
                }
              }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("summary", {
                style: {
                  cursor: "pointer",
                  fontSize: "13px",
                  fontWeight: 500,
                  color: "#64748b",
                  padding: "6px 0",
                  userSelect: "none",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  listStyle: "none"
                }
              }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Steps:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
                style: {
                  marginLeft: "auto",
                  fontSize: "11px",
                  color: "#94a3b8"
                }
              }, "\u25BC Show steps")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                style: {
                  fontSize: "14px",
                  lineHeight: "1.6",
                  color: "#1e293b",
                  marginTop: "12px",
                  paddingTop: "12px",
                  borderTop: "1px solid #e5e7eb"
                }
              }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                dangerouslySetInnerHTML: {
                  __html: (0,marked__WEBPACK_IMPORTED_MODULE_1__.marked)(playbookGuideContent)
                }
              })))), activePolicies.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                style: {
                  marginTop: "20px",
                  paddingTop: "20px",
                  borderTop: "1px solid #e5e7eb"
                }
              }, activePolicies.map((policy, index) => {
                const policyMetadata = policy.metadata || {};
                const policyReasoning = policyMetadata.policy_reasoning || "";
                const policyType = policy.policy_type || "unknown";
                const policyName = policy.policy_name || "Unknown Policy";
                const isBlocked = policy.policy_blocked || false;
                if (!policyReasoning) return null;

                // More subtle design for intent_guard policies
                const isIntentGuard = policyType === "intent_guard";
                const backgroundColor = isIntentGuard ? "#f8fafc" : isBlocked ? "#fef2f2" : policyType === "tool_approval" ? "#fffbeb" : policyType === "playbook" ? "#eff6ff" : policyType === "tool_guide" ? "#f0fdf4" : policyType === "output_formatter" ? "#fef3f2" : "#f1f5f9";
                const borderColor = isIntentGuard ? "#e2e8f0" : isBlocked ? "#ef4444" : policyType === "tool_approval" ? "#f59e0b" : policyType === "playbook" ? "#3b82f6" : policyType === "tool_guide" ? "#10b981" : policyType === "output_formatter" ? "#f97316" : "#64748b";
                const icon = isIntentGuard ? "ℹ️" : policyType === "playbook" ? "📖" : policyType === "tool_guide" ? "🔧" : policyType === "tool_approval" ? "✋" : policyType === "output_formatter" ? "✨" : "📋";
                return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                  key: index,
                  style: {
                    marginTop: index > 0 ? "12px" : "0",
                    padding: isIntentGuard ? "12px 16px" : "16px",
                    backgroundColor: backgroundColor,
                    border: isIntentGuard ? `1px solid ${borderColor}` : `2px solid ${borderColor}`,
                    borderRadius: "8px",
                    boxShadow: isIntentGuard ? "none" : "0 1px 3px rgba(0, 0, 0, 0.1)"
                  }
                }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                  style: {
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    marginBottom: isIntentGuard ? "8px" : "12px"
                  }
                }, isBlocked ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                  style: {
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "6px",
                    backgroundColor: "#dc2626",
                    color: "#ffffff",
                    padding: "5px 12px",
                    borderRadius: "6px",
                    fontSize: "12px",
                    fontWeight: 700,
                    border: "none",
                    boxShadow: "0 1px 2px rgba(220, 38, 38, 0.3)",
                    textTransform: "uppercase",
                    letterSpacing: "0.5px"
                  }
                }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
                  style: {
                    width: "8px",
                    height: "8px",
                    borderRadius: "50%",
                    backgroundColor: "#ffffff",
                    display: "inline-block",
                    boxShadow: "0 0 0 2px #dc2626"
                  }
                }), "Blocked") : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
                  style: {
                    fontSize: isIntentGuard ? "16px" : "20px",
                    opacity: isIntentGuard ? 0.7 : 1
                  }
                }, icon), !isBlocked && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h4", {
                  style: {
                    margin: 0,
                    fontSize: "14px",
                    fontWeight: 600,
                    color: isIntentGuard ? "#64748b" : borderColor
                  }
                }, policyName), isBlocked && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h4", {
                  style: {
                    margin: 0,
                    fontSize: "14px",
                    fontWeight: 600,
                    color: "#dc2626"
                  }
                }, policyName), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
                  style: {
                    fontSize: "10px",
                    color: isIntentGuard ? "#94a3b8" : isBlocked ? "#991b1b" : "#64748b",
                    backgroundColor: isIntentGuard ? "#f1f5f9" : isBlocked ? "#fee2e2" : "#e5e7eb",
                    padding: "2px 8px",
                    borderRadius: "12px",
                    textTransform: "capitalize",
                    fontWeight: isIntentGuard ? 400 : 500
                  }
                }, policyType.replace("_", " "))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                  style: {
                    fontSize: "12px",
                    lineHeight: "1.6",
                    color: isIntentGuard ? "#64748b" : "#374151",
                    padding: isIntentGuard ? "8px 0" : "12px",
                    backgroundColor: isIntentGuard ? "transparent" : "#ffffff",
                    borderRadius: isIntentGuard ? "0" : "6px",
                    border: isIntentGuard ? "none" : "1px solid #e5e7eb",
                    fontFamily: "system-ui, -apple-system, sans-serif"
                  }
                }, !isIntentGuard && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                  style: {
                    fontWeight: 500,
                    color: "#6b7280",
                    marginBottom: "6px",
                    fontSize: "12px",
                    textTransform: "uppercase",
                    letterSpacing: "0.5px"
                  }
                }, "Policy Reasoning"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                  style: {
                    color: isIntentGuard ? "#64748b" : "#1f2937",
                    fontStyle: isIntentGuard ? "normal" : "normal"
                  }
                }, policyReasoning)));
              })));
            } else if (playbookGuideContent || activePolicies.length > 0) {
              // No answer text, but we have playbook or policies to show
              mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, playbookGuideContent && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                style: {
                  marginBottom: activePolicies.length > 0 ? "20px" : "0"
                }
              }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                style: {
                  fontSize: "14px",
                  fontWeight: 600,
                  color: "#0ea5e9",
                  marginBottom: "12px",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px"
                }
              }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
                style: {
                  fontSize: "18px"
                }
              }, "\uD83D\uDCD6"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Task Guide")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("details", {
                style: {
                  position: "relative"
                }
              }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("summary", {
                style: {
                  cursor: "pointer",
                  fontSize: "13px",
                  fontWeight: 500,
                  color: "#64748b",
                  padding: "6px 0",
                  userSelect: "none",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  listStyle: "none"
                }
              }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Steps:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
                style: {
                  marginLeft: "auto",
                  fontSize: "11px",
                  color: "#94a3b8"
                }
              }, "\u25BC Show steps")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                style: {
                  fontSize: "14px",
                  lineHeight: "1.6",
                  color: "#1e293b",
                  marginTop: "12px",
                  paddingTop: "12px",
                  borderTop: "1px solid #e5e7eb"
                }
              }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                dangerouslySetInnerHTML: {
                  __html: (0,marked__WEBPACK_IMPORTED_MODULE_1__.marked)(playbookGuideContent)
                }
              })))), activePolicies.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                style: {
                  marginTop: playbookGuideContent ? "20px" : "0",
                  paddingTop: playbookGuideContent ? "20px" : "0",
                  borderTop: playbookGuideContent ? "1px solid #e5e7eb" : "none"
                }
              }, activePolicies.map((policy, index) => {
                const policyMetadata = policy.metadata || {};
                const policyReasoning = policyMetadata.policy_reasoning || "";
                const policyType = policy.policy_type || "unknown";
                const policyName = policy.policy_name || "Unknown Policy";
                const isBlocked = policy.policy_blocked || false;
                if (!policyReasoning) return null;
                const isIntentGuard = policyType === "intent_guard";
                const backgroundColor = isIntentGuard ? "#f8fafc" : isBlocked ? "#fef2f2" : policyType === "tool_approval" ? "#fffbeb" : policyType === "playbook" ? "#eff6ff" : policyType === "tool_guide" ? "#f0fdf4" : policyType === "output_formatter" ? "#fef3f2" : "#f1f5f9";
                const borderColor = isIntentGuard ? "#e2e8f0" : isBlocked ? "#ef4444" : policyType === "tool_approval" ? "#f59e0b" : policyType === "playbook" ? "#3b82f6" : policyType === "tool_guide" ? "#10b981" : policyType === "output_formatter" ? "#f97316" : "#64748b";
                const icon = isIntentGuard ? "ℹ️" : policyType === "playbook" ? "📖" : policyType === "tool_guide" ? "🔧" : policyType === "tool_approval" ? "✋" : policyType === "output_formatter" ? "✨" : "📋";
                return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                  key: index,
                  style: {
                    marginTop: index > 0 ? "12px" : "0",
                    padding: isIntentGuard ? "12px 16px" : "16px",
                    backgroundColor: backgroundColor,
                    border: isIntentGuard ? `1px solid ${borderColor}` : `2px solid ${borderColor}`,
                    borderRadius: "8px",
                    boxShadow: isIntentGuard ? "none" : "0 1px 3px rgba(0, 0, 0, 0.1)"
                  }
                }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                  style: {
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    marginBottom: isIntentGuard ? "8px" : "12px"
                  }
                }, isBlocked ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                  style: {
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "6px",
                    backgroundColor: "#dc2626",
                    color: "#ffffff",
                    padding: "5px 12px",
                    borderRadius: "6px",
                    fontSize: "12px",
                    fontWeight: 700,
                    border: "none",
                    boxShadow: "0 1px 2px rgba(220, 38, 38, 0.3)",
                    textTransform: "uppercase",
                    letterSpacing: "0.5px"
                  }
                }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
                  style: {
                    width: "8px",
                    height: "8px",
                    borderRadius: "50%",
                    backgroundColor: "#ffffff",
                    display: "inline-block",
                    boxShadow: "0 0 0 2px #dc2626"
                  }
                }), "Blocked") : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
                  style: {
                    fontSize: isIntentGuard ? "16px" : "20px",
                    opacity: isIntentGuard ? 0.7 : 1
                  }
                }, icon), !isBlocked && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h4", {
                  style: {
                    margin: 0,
                    fontSize: "14px",
                    fontWeight: 600,
                    color: isIntentGuard ? "#64748b" : borderColor
                  }
                }, policyName), isBlocked && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h4", {
                  style: {
                    margin: 0,
                    fontSize: "14px",
                    fontWeight: 600,
                    color: "#dc2626"
                  }
                }, policyName), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
                  style: {
                    fontSize: "10px",
                    color: isIntentGuard ? "#94a3b8" : isBlocked ? "#991b1b" : "#64748b",
                    backgroundColor: isIntentGuard ? "#f1f5f9" : isBlocked ? "#fee2e2" : "#e5e7eb",
                    padding: "2px 8px",
                    borderRadius: "12px",
                    textTransform: "capitalize",
                    fontWeight: isIntentGuard ? 400 : 500
                  }
                }, policyType.replace("_", " "))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                  style: {
                    fontSize: "12px",
                    lineHeight: "1.6",
                    color: isIntentGuard ? "#64748b" : "#374151",
                    padding: isIntentGuard ? "8px 0" : "12px",
                    backgroundColor: isIntentGuard ? "transparent" : "#ffffff",
                    borderRadius: isIntentGuard ? "0" : "6px",
                    border: isIntentGuard ? "none" : "1px solid #e5e7eb",
                    fontFamily: "system-ui, -apple-system, sans-serif"
                  }
                }, !isIntentGuard && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                  style: {
                    fontWeight: 500,
                    color: "#6b7280",
                    marginBottom: "6px",
                    fontSize: "12px",
                    textTransform: "uppercase",
                    letterSpacing: "0.5px"
                  }
                }, "Policy Reasoning"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
                  style: {
                    color: isIntentGuard ? "#64748b" : "#1f2937",
                    fontStyle: isIntentGuard ? "normal" : "normal"
                  }
                }, policyReasoning)));
              })));
            }
          }
          break;
        case "SuggestHumanActions":
          if (parsedContent && parsedContent.action_id) {
            console.log("[SuggestHumanActions] Rendering FollowupAction with:", parsedContent);
            mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_Followup__WEBPACK_IMPORTED_MODULE_13__.FollowupAction, {
              followupAction: parsedContent,
              callback: async d => {
                console.log("📤 Sending approval response:", d);
                console.log("📤 Using threadId:", threadId);
                // Mark this step as completed before proceeding
                markStepCompleted(step.id);
                await (0,_StreamingWorkflow__WEBPACK_IMPORTED_MODULE_14__.fetchStreamingData)(chatInstance, "", d, threadId);
              }
            });
          } else {
            console.error("[SuggestHumanActions] Invalid parsedContent:", parsedContent);
            mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
              className: "text-red-500 p-4 border border-red-300 rounded"
            }, "Error: Invalid action data received");
          }
          break;
        default:
          const isJSONLike = parsedContent !== null && (typeof parsedContent === "object" || Array.isArray(parsedContent)) && !(parsedContent instanceof Date) && !(parsedContent instanceof RegExp);
          if (isJSONLike) {
            parsedContent = JSON.stringify(parsedContent, null, 2);
            parsedContent = `\`\`\`json\n${parsedContent}\n\`\`\``;
          }
          if (!parsedContent) {
            parsedContent = "";
          }
          mainElement = /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_generic_component__WEBPACK_IMPORTED_MODULE_10__["default"], {
            title: step.title,
            content: parsedContent
          });
      }

      // Add main element to outputElements if it exists
      if (mainElement) {
        outputElements.push(mainElement);
      }
      return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, outputElements);
    } catch (error) {
      console.log(`Failed to parse JSON for step ${step.title}:`, error);
      return null;
    }
  }, [chatInstance, markStepCompleted, currentSteps]);

  // Memoized button click handler
  const handleToggleDetails = (0,react__WEBPACK_IMPORTED_MODULE_0__.useCallback)(stepId => {
    console.log("Button clicked for step:", stepId, "Current state:", showDetails[stepId]);
    setShowDetails(prev => ({
      ...prev,
      [stepId]: !prev[stepId]
    }));
  }, [showDetails]);

  // Handle reasoning collapse toggle
  const handleToggleReasoning = (0,react__WEBPACK_IMPORTED_MODULE_0__.useCallback)(() => {
    setIsReasoningCollapsed(prev => !prev);
  }, []);
  const mapStepTitle = (stepTitle, parsedContent) => {
    // Handle CodeAgent dynamically based on execution output
    if (stepTitle === "CodeAgent" && parsedContent) {
      const hasExecutionOutput = parsedContent.execution_output && parsedContent.execution_output.trim().length > 0;
      return hasExecutionOutput ? "Executed Code" : "Generated Code";
    }
    const titleMap = {
      TaskDecompositionAgent: "Decomposed task into steps",
      TaskAnalyzerAgent: "Analyzed available applications",
      PlanControllerAgent: "Controlled task execution",
      SuggestHumanActions: /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        style: {
          display: "flex",
          alignItems: "center",
          gap: "8px"
        }
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "w-4 h-4 border-2 border-blue-200 border-t-blue-500 rounded-full animate-spin"
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Waiting for your input")),
      APIPlannerAgent: "Planned API actions",
      APICodePlannerAgent: "Planned steps for coding agent",
      CodeAgent_Reasoning: "Reasoning about approach",
      ShortlisterAgent: "Shortlisted relevant APIs",
      QaAgent: "Answered question",
      FinalAnswerAgent: "Completed final answer",
      Answer: "Answer"
    };
    return titleMap[stepTitle] || stepTitle;
  };
  console.log("CardManager render - currentSteps:", currentSteps.length, "isProcessingComplete:", isProcessingComplete);

  // Check if there's an error step
  const hasErrorStep = currentSteps.some(step => step.title === "Error");

  // Separate final answer steps and active user action steps from reasoning steps
  // Only show Answer events as final answers (not FinalAnswerAgent)
  const finalAnswerSteps = currentSteps.filter(step => {
    return step.title === "Answer" && shouldRenderStep(step);
  });

  // Show SuggestHumanActions as active if it's not marked as completed
  const userActionSteps = currentSteps.filter(step => {
    const isUserAction = step.title === "SuggestHumanActions" && !step.completed;
    return isUserAction && shouldRenderStep(step);
  });

  // Include completed SuggestHumanActions in reasoning steps, excluding empty ones
  // FinalAnswerAgent goes in reasoning steps (collapsed), only Answer is shown as final answer
  const reasoningSteps = currentSteps.filter(step => {
    const isNotFinalOrUserAction = step.title !== "Answer" && !(step.title === "SuggestHumanActions" && !step.completed);

    // Also exclude steps that shouldn't be rendered (empty CodeAgent events, etc.)
    return isNotFinalOrUserAction && shouldRenderStep(step);
  });

  // Get current step to display (before final answer or user action)
  const currentStep = currentSteps[currentStepIndex];
  const isShowingCurrentStep = !isStopped && viewMode === "inplace" && !hasFinalAnswer && userActionSteps.length === 0 && currentStep;
  const isLoading = !isStopped && currentSteps.length > 0 && !isProcessingComplete && !hasFinalAnswer && userActionSteps.length === 0 && !hasErrorStep;

  // Helper function to render a single step card
  const renderStepCard = (step, isCurrentStep = false) => {
    // Parse content for description - match the logic in renderStepContent
    let parsedContent;
    try {
      if (typeof step.content === "string") {
        try {
          parsedContent = JSON.parse(step.content);
          const keys = Object.keys(parsedContent);

          // Check if we have variables in the parsed content (matching renderStepContent logic)
          if (parsedContent.data !== undefined && parsedContent.variables) {
            // Parse data if it's a JSON string
            let dataValue = parsedContent.data;
            let extractedPolicies = parsedContent.active_policies || [];
            if (typeof dataValue === "string") {
              try {
                const parsedData = JSON.parse(dataValue);
                // Check if the parsed data is a policy object
                if (parsedData && typeof parsedData === "object" && parsedData.type === "policy") {
                  extractedPolicies = [parsedData];
                  // Use the content as final_answer if it's a policy
                  dataValue = parsedData.content || parsedData.response_content || dataValue;
                }
              } catch (e) {
                // Not JSON, keep as string
              }
            } else if (dataValue && typeof dataValue === "object" && dataValue.type === "policy") {
              // Data is already a policy object
              extractedPolicies = [dataValue];
              dataValue = dataValue.content || dataValue.response_content || "";
            }

            // For Answer step with variables: treat data as final_answer
            if (step.title === "Answer" || step.title === "FinalAnswerAgent") {
              parsedContent = {
                final_answer: dataValue,
                variables: parsedContent.variables,
                active_policies: extractedPolicies
              };
            } else if (typeof parsedContent.data === "object" && !Array.isArray(parsedContent.data)) {
              // Keep both data and variables if data is an object
              parsedContent = {
                ...parsedContent.data,
                variables: parsedContent.variables,
                active_policies: extractedPolicies
              };
            } else {
              // If data is not an object, keep as is with variables
              parsedContent = {
                data: dataValue,
                variables: parsedContent.variables,
                active_policies: extractedPolicies
              };
            }
          } else if (keys.length === 1 && keys[0] === "data") {
            // Only data, no variables - check if data is a policy JSON string
            let dataValue = parsedContent.data;
            let extractedPolicies = [];
            if (typeof dataValue === "string") {
              try {
                const parsedData = JSON.parse(dataValue);
                if (parsedData && typeof parsedData === "object" && parsedData.type === "policy") {
                  extractedPolicies = [parsedData];
                  parsedContent = {
                    final_answer: parsedData.content || parsedData.response_content || "",
                    active_policies: extractedPolicies
                  };
                } else {
                  parsedContent = dataValue;
                }
              } catch (e) {
                parsedContent = dataValue;
              }
            } else if (dataValue && typeof dataValue === "object" && dataValue.type === "policy") {
              extractedPolicies = [dataValue];
              parsedContent = {
                final_answer: dataValue.content || dataValue.response_content || "",
                active_policies: extractedPolicies
              };
            } else {
              parsedContent = dataValue;
            }
          }
        } catch (e) {
          parsedContent = step.content;
        }
      } else {
        parsedContent = step.content;
      }
    } catch (error) {
      parsedContent = step.content;
    }
    if (step.title === "simple_text") {
      return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        key: step.id,
        style: {
          marginBottom: "10px"
        }
      }, step.content);
    }

    // Get description for rendering
    const description = getCaseDescription(step.id, step.title, parsedContent);

    // Skip rendering if description is null (e.g., empty CodeAgent events)
    if (description === null) {
      return null;
    }

    // Only render component content if details are shown
    const componentContent = showDetails[step.id] ? renderStepContent(step, currentSteps) : null;
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      key: step.id,
      ref: el => {
        stepRefs.current[step.id] = el;
      },
      className: `component-container ${step.isNew ? "new-component" : ""} ${isCurrentStep ? "current-step" : ""}`,
      style: {
        marginBottom: "16px",
        padding: "12px",
        paddingTop: "28px",
        backgroundColor: "#ffffff",
        borderRadius: "6px",
        border: "1px solid #e2e8f0",
        boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)",
        position: "relative"
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        marginBottom: "12px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between"
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
      style: {
        fontSize: "14px",
        fontWeight: "500",
        color: "#475569",
        margin: "0",
        display: "flex",
        alignItems: "center",
        gap: "6px"
      }
    }, mapStepTitle(step.title, parsedContent))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        marginBottom: "12px"
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        margin: "0",
        fontSize: "13px",
        color: "#64748b",
        lineHeight: "1.4"
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().isValidElement(description) ? description : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      dangerouslySetInnerHTML: {
        __html: description
      }
    }))), componentContent && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, componentContent), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
      onClick: () => handleToggleDetails(step.id),
      style: {
        position: "absolute",
        right: "8px",
        top: "8px",
        display: "flex",
        alignItems: "center",
        gap: "6px",
        background: "transparent",
        border: "1px solid #e5e7eb",
        borderRadius: "12px",
        padding: "4px 8px",
        fontSize: "11px",
        color: showDetails[step.id] ? "#3b82f6" : "#64748b",
        cursor: "pointer"
      },
      onMouseOver: e => {
        e.currentTarget.style.backgroundColor = "#f8fafc";
      },
      onMouseOut: e => {
        e.currentTarget.style.backgroundColor = "transparent";
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      style: {
        display: "inline-block",
        transform: showDetails[step.id] ? "rotate(180deg)" : "rotate(0deg)",
        transition: "transform 0.2s ease",
        fontSize: "12px"
      }
    }, "\u25BC"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "details")));
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("style", null, `
        .components-container details summary::-webkit-details-marker {
          display: none;
        }
        .components-container details summary::marker {
          display: none;
        }
      `), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "components-container",
    ref: cardRef
  }, !isStopped && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      justifyContent: "flex-end",
      marginBottom: "6px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "6px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    style: {
      fontSize: "11px",
      color: "#64748b"
    }
  }, "View:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: () => setViewMode("inplace"),
    style: {
      padding: "2px 6px",
      backgroundColor: viewMode === "inplace" ? "#2563eb" : "transparent",
      color: viewMode === "inplace" ? "#ffffff" : "#64748b",
      border: "1px solid #e5e7eb",
      borderRadius: "3px",
      fontSize: "10px",
      fontWeight: 500,
      cursor: "pointer"
    }
  }, "In-place"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: () => setViewMode("append"),
    style: {
      padding: "2px 6px",
      backgroundColor: viewMode === "append" ? "#2563eb" : "transparent",
      color: viewMode === "append" ? "#ffffff" : "#64748b",
      border: "1px solid #e5e7eb",
      borderRadius: "3px",
      fontSize: "10px",
      fontWeight: 500,
      cursor: "pointer"
    }
  }, "Append"))), !isStopped && viewMode === "append" && currentSteps.length > 0 && (hasFinalAnswer ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, reasoningSteps.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      marginBottom: "16px",
      padding: "12px",
      backgroundColor: "#f8fafc",
      borderRadius: "8px",
      border: "1px solid #e2e8f0",
      boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      cursor: "pointer",
      userSelect: "none"
    },
    onClick: handleToggleReasoning
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
    style: {
      fontSize: "16px",
      fontWeight: "600",
      color: "#374151",
      margin: "0",
      display: "flex",
      alignItems: "center",
      gap: "8px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    style: {
      transform: isReasoningCollapsed ? "rotate(-90deg)" : "rotate(0deg)",
      transition: "transform 0.3s ease",
      fontSize: "14px"
    }
  }, "\u25BC"), "Reasoning Process", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    style: {
      fontSize: "12px",
      fontWeight: "400",
      color: "#6b7280",
      backgroundColor: "#e5e7eb",
      padding: "2px 8px",
      borderRadius: "12px"
    }
  }, reasoningSteps.length, " steps")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      fontSize: "12px",
      color: "#6b7280",
      fontStyle: "italic"
    }
  }, isReasoningCollapsed ? "Click to expand" : "Click to collapse")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      maxHeight: isReasoningCollapsed ? "0" : "10000px",
      overflow: "hidden",
      transition: "max-height 0.5s ease-in-out, opacity 0.3s ease-in-out",
      opacity: isReasoningCollapsed ? 0 : 1
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      marginTop: "12px"
    }
  }, reasoningSteps.map(step => renderStepCard(step, false))))), finalAnswerSteps.map(step => renderStepCard(step, false))) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, currentSteps.map(step => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: step.id
  }, renderStepCard(step, false))))), isStopped && currentSteps.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      marginBottom: "16px",
      padding: "12px",
      backgroundColor: "#f8fafc",
      borderRadius: "8px",
      border: "1px solid #e2e8f0",
      boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      cursor: "pointer",
      userSelect: "none"
    },
    onClick: handleToggleReasoning
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
    style: {
      fontSize: "16px",
      fontWeight: "600",
      color: "#374151",
      margin: "0",
      display: "flex",
      alignItems: "center",
      gap: "8px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    style: {
      transform: isReasoningCollapsed ? "rotate(-90deg)" : "rotate(0deg)",
      transition: "transform 0.3s ease",
      fontSize: "14px"
    }
  }, "\u25BC"), "Reasoning Process", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    style: {
      fontSize: "12px",
      fontWeight: "400",
      color: "#6b7280",
      backgroundColor: "#e5e7eb",
      padding: "2px 8px",
      borderRadius: "12px"
    }
  }, currentSteps.length, " steps")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      fontSize: "12px",
      color: "#6b7280",
      fontStyle: "italic"
    }
  }, isReasoningCollapsed ? "Click to expand" : "Click to collapse")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      maxHeight: isReasoningCollapsed ? "0" : "10000px",
      overflow: "hidden",
      transition: "max-height 0.5s ease-in-out, opacity 0.3s ease-in-out",
      opacity: isReasoningCollapsed ? 0 : 1
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      marginTop: "12px"
    }
  }, currentSteps.map(step => renderStepCard(step, false))))), isStopped && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      marginTop: "8px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      marginBottom: "16px",
      padding: "12px",
      backgroundColor: "#ffffff",
      borderRadius: "6px",
      border: "1px solid #e2e8f0",
      boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      marginBottom: "12px",
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
    style: {
      fontSize: "14px",
      fontWeight: "500",
      color: "#475569",
      margin: "0",
      display: "flex",
      alignItems: "center",
      gap: "6px"
    }
  }, "Task Interrupted")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    style: {
      margin: "0",
      fontSize: "13px",
      color: "#64748b",
      lineHeight: "1.4"
    }
  }, "The task was stopped by the user.")))), !isStopped && viewMode === "inplace" && (hasFinalAnswer || userActionSteps.length > 0) && reasoningSteps.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      marginBottom: "16px",
      padding: "12px",
      backgroundColor: "#f8fafc",
      borderRadius: "8px",
      border: "1px solid #e2e8f0",
      boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      cursor: "pointer",
      userSelect: "none"
    },
    onClick: handleToggleReasoning
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
    style: {
      fontSize: "16px",
      fontWeight: "600",
      color: "#374151",
      margin: "0",
      display: "flex",
      alignItems: "center",
      gap: "8px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    style: {
      transform: isReasoningCollapsed ? "rotate(-90deg)" : "rotate(0deg)",
      transition: "transform 0.3s ease",
      fontSize: "14px"
    }
  }, "\u25BC"), "Reasoning Process", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    style: {
      fontSize: "12px",
      fontWeight: "400",
      color: "#6b7280",
      backgroundColor: "#e5e7eb",
      padding: "2px 8px",
      borderRadius: "12px"
    }
  }, reasoningSteps.length, " steps")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      fontSize: "12px",
      color: "#6b7280",
      fontStyle: "italic"
    }
  }, isReasoningCollapsed ? "Click to expand" : "Click to collapse")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      maxHeight: isReasoningCollapsed ? "0" : "10000px",
      overflow: "hidden",
      transition: "max-height 0.5s ease-in-out, opacity 0.3s ease-in-out",
      opacity: isReasoningCollapsed ? 0 : 1
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      marginTop: "12px"
    }
  }, reasoningSteps.map(step => renderStepCard(step, false))))), !isStopped && viewMode === "inplace" && isShowingCurrentStep && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: `current-step-container ${isLoading ? "loading-border" : ""}`,
    style: {
      position: "relative",
      minHeight: "200px"
    }
  }, renderStepCard(currentStep, true)), !isStopped && viewMode === "inplace" && finalAnswerSteps.map(step => renderStepCard(step, false)), !isStopped && viewMode === "inplace" && userActionSteps.map(step => renderStepCard(step, false)), !isStopped && viewMode === "inplace" && currentSteps.length > 0 && !isProcessingComplete && !hasFinalAnswer && userActionSteps.length === 0 && !hasErrorStep && !isShowingCurrentStep && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      marginTop: "8px",
      marginBottom: "2px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      fontSize: "10px",
      color: "#94a3b8",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      marginBottom: "4px",
      userSelect: "none"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "CUGA is thinking..")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      height: "4px",
      position: "relative",
      overflow: "hidden",
      background: "#eef2ff",
      borderRadius: "9999px",
      boxShadow: "inset 0 0 0 1px #e5e7eb"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      position: "absolute",
      left: 0,
      top: 0,
      bottom: 0,
      width: "28%",
      background: "linear-gradient(90deg, #a78bfa 0%, #6366f1 100%)",
      borderRadius: "9999px",
      animation: "cugaShimmer 1.7s infinite",
      boxShadow: "0 0 6px rgba(99,102,241,0.25)"
    }
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("style", null, `
              @keyframes cugaShimmer {
                0% { transform: translateX(-100%); }
                100% { transform: translateX(300%); }
              }
            `))));
};
/* harmony default export */ __webpack_exports__["default"] = (CardManager);

/***/ }),

/***/ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/CardManager.css":
/*!**************************************************************************************************************************************!*\
  !*** ../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/CardManager.css ***!
  \**************************************************************************************************************************************/
/***/ (function(module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__);
// Imports


var ___CSS_LOADER_EXPORT___ = _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default()((_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default()));
// Module
___CSS_LOADER_EXPORT___.push([module.id, "/* Card Manager Styles */\n.card-manager {\n  background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);\n  border-radius: 12px;\n  border: 1px solid #cbd5e1;\n  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);\n  margin: 16px 0;\n  overflow: hidden;\n  transition: all 0.3s ease;\n  position: relative;\n}\n\n.card-manager.animating {\n  transform: scale(1.02);\n  box-shadow: 0 8px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);\n}\n\n.card-header {\n  background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);\n  color: white;\n  padding: 16px 20px;\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  position: relative;\n  overflow: hidden;\n}\n\n.card-header::before {\n  content: '';\n  position: absolute;\n  top: -50%;\n  right: -50%;\n  width: 100%;\n  height: 200%;\n  background: linear-gradient(45deg, transparent, rgba(255, 255, 255, 0.1), transparent);\n  transform: rotate(45deg);\n  animation: shimmer 3s infinite;\n}\n\n@keyframes shimmer {\n  0% { transform: translateX(-100%) rotate(45deg); }\n  100% { transform: translateX(100%) rotate(45deg); }\n}\n\n.card-title h3 {\n  margin: 0;\n  font-size: 18px;\n  font-weight: 600;\n  display: flex;\n  align-items: center;\n  gap: 8px;\n}\n\n.card-title h3::before {\n  content: '🤖';\n  font-size: 20px;\n}\n\n.step-counter {\n  font-size: 12px;\n  opacity: 0.9;\n  margin-top: 2px;\n  font-weight: 400;\n}\n\n.card-actions {\n  display: flex;\n  gap: 8px;\n  align-items: center;\n}\n\n.history-button {\n  background: rgba(255, 255, 255, 0.2);\n  border: 1px solid rgba(255, 255, 255, 0.3);\n  color: white;\n  padding: 6px 12px;\n  border-radius: 6px;\n  font-size: 12px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s ease;\n  backdrop-filter: blur(10px);\n}\n\n.history-button:hover {\n  background: rgba(255, 255, 255, 0.3);\n  transform: translateY(-1px);\n}\n\n.card-content {\n  padding: 20px;\n  background: white;\n  min-height: 100px;\n}\n\n.step-item {\n  margin-bottom: 16px;\n  opacity: 0;\n  transform: translateY(20px);\n  animation: slideInUp 0.5s ease forwards;\n}\n\n.step-item.new-step {\n  animation: slideInUp 0.5s ease forwards, highlightPulse 2s ease 0.5s;\n}\n\n@keyframes slideInUp {\n  from {\n    opacity: 0;\n    transform: translateY(20px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n@keyframes highlightPulse {\n  0%, 100% {\n    box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.4);\n  }\n  50% {\n    box-shadow: 0 0 0 8px rgba(59, 130, 246, 0.1);\n  }\n}\n\n.card-footer {\n  background: linear-gradient(135deg, #10b981 0%, #059669 100%);\n  color: white;\n  padding: 16px 20px;\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  border-top: 1px solid #d1fae5;\n}\n\n.completion-message {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  font-weight: 600;\n  font-size: 14px;\n}\n\n.new-query-button {\n  background: rgba(255, 255, 255, 0.2);\n  border: 1px solid rgba(255, 255, 255, 0.3);\n  color: white;\n  padding: 8px 16px;\n  border-radius: 6px;\n  font-size: 12px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s ease;\n  backdrop-filter: blur(10px);\n}\n\n.new-query-button:hover {\n  background: rgba(255, 255, 255, 0.3);\n  transform: translateY(-1px);\n}\n\n/* History Modal Styles */\n.history-modal-overlay {\n  position: fixed;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  background: rgba(0, 0, 0, 0.5);\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  z-index: 1000;\n  backdrop-filter: blur(4px);\n  animation: fadeIn 0.3s ease;\n}\n\n@keyframes fadeIn {\n  from { opacity: 0; }\n  to { opacity: 1; }\n}\n\n.history-modal {\n  background: white;\n  border-radius: 12px;\n  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);\n  max-width: 600px;\n  width: 90%;\n  max-height: 80vh;\n  overflow: hidden;\n  animation: slideInModal 0.3s ease;\n}\n\n@keyframes slideInModal {\n  from {\n    opacity: 0;\n    transform: scale(0.9) translateY(-20px);\n  }\n  to {\n    opacity: 1;\n    transform: scale(1) translateY(0);\n  }\n}\n\n.history-header {\n  background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);\n  color: white;\n  padding: 20px;\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n}\n\n.history-header h3 {\n  margin: 0;\n  font-size: 18px;\n  font-weight: 600;\n}\n\n.history-actions {\n  display: flex;\n  gap: 8px;\n  align-items: center;\n}\n\n.clear-history-button,\n.close-history-button {\n  background: rgba(255, 255, 255, 0.2);\n  border: 1px solid rgba(255, 255, 255, 0.3);\n  color: white;\n  padding: 6px 12px;\n  border-radius: 6px;\n  font-size: 12px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s ease;\n  backdrop-filter: blur(10px);\n}\n\n.clear-history-button:hover,\n.close-history-button:hover {\n  background: rgba(255, 255, 255, 0.3);\n}\n\n.close-history-button {\n  padding: 6px;\n  font-size: 16px;\n  line-height: 1;\n}\n\n.history-content {\n  padding: 20px;\n  max-height: 60vh;\n  overflow-y: auto;\n}\n\n.no-history {\n  text-align: center;\n  color: #6b7280;\n  font-style: italic;\n  padding: 40px 20px;\n}\n\n.history-card {\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  padding: 16px;\n  margin-bottom: 12px;\n  background: #f9fafb;\n  transition: all 0.2s ease;\n}\n\n.history-card:hover {\n  border-color: #3b82f6;\n  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);\n  transform: translateY(-1px);\n}\n\n.history-card-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  margin-bottom: 8px;\n}\n\n.history-card-title {\n  font-weight: 600;\n  color: #374151;\n  font-size: 14px;\n}\n\n.history-card-meta {\n  font-size: 12px;\n  color: #6b7280;\n}\n\n.history-card-preview {\n  margin-bottom: 12px;\n}\n\n.history-step-preview {\n  font-size: 12px;\n  color: #4b5563;\n  margin-bottom: 4px;\n  padding-left: 8px;\n  border-left: 2px solid #e5e7eb;\n}\n\n.history-step-more {\n  font-size: 11px;\n  color: #9ca3af;\n  font-style: italic;\n  padding-left: 8px;\n  border-left: 2px solid #e5e7eb;\n}\n\n.restore-card-button {\n  background: #3b82f6;\n  color: white;\n  border: none;\n  padding: 6px 12px;\n  border-radius: 6px;\n  font-size: 12px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s ease;\n}\n\n.restore-card-button:hover {\n  background: #2563eb;\n  transform: translateY(-1px);\n}\n\n/* In-Place Card Transitions */\n.current-step-container {\n  position: relative;\n  overflow: hidden;\n  min-height: 200px;\n  transition: min-height 0.3s ease-in-out;\n}\n\n/* No container animation – instant switch */\n\n.component-container.current-step {\n  box-shadow: 0 4px 12px rgba(59, 130, 246, 0.15);\n  border-color: #3b82f6;\n  position: relative;\n  overflow: hidden;\n}\n\n/* Loading step with sliding border animation */\n/* Shared loading border lives on the persistent container so it continues across swaps */\n.current-step-container.loading-border::before {\n  content: '';\n  position: absolute;\n  top: 0;\n  left: -100%;\n  width: 100%;\n  height: 2px;\n  background: linear-gradient(90deg, transparent, #3b82f6, #06b6d4, transparent);\n  animation: borderSlide 2.5s ease-in-out infinite;\n  z-index: 1;\n}\n\n@keyframes borderSlide {\n  0% {\n    left: -100%;\n  }\n  100% {\n    left: 100%;\n  }\n}\n\n@keyframes borderSlideReverse {\n  0% {\n    right: -100%;\n  }\n  100% {\n    right: 100%;\n  }\n}\n\n/* No appear animation */\n\n/* Non-current steps rendered only in reasoning list; no fade */\n.component-container:not(.current-step) {}\n\n/* Reasoning Process Collapse Animation */\n.reasoning-section {\n  transition: all 0.5s ease-in-out;\n}\n\n.reasoning-content {\n  transition: max-height 0.5s ease-in-out, opacity 0.3s ease-in-out;\n  overflow: hidden;\n}\n\n.reasoning-content.collapsed {\n  max-height: 0;\n  opacity: 0;\n}\n\n.reasoning-content.expanded {\n  max-height: 2000px;\n  opacity: 1;\n}\n\n/* Step Fade Transitions */\n.step-fade-enter {\n  opacity: 0;\n  transform: translateY(20px);\n}\n\n.step-fade-enter-active {\n  opacity: 1;\n  transform: translateY(0);\n  transition: opacity 0.3s ease-in-out, transform 0.3s ease-in-out;\n}\n\n.step-fade-exit {\n  opacity: 1;\n  transform: translateY(0);\n}\n\n.step-fade-exit-active {\n  opacity: 0;\n  transform: translateY(-20px);\n  transition: opacity 0.3s ease-in-out, transform 0.3s ease-in-out;\n}\n\n/* Enhanced Card Hover Effects */\n.component-container:hover {\n  box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);\n  transition: box-shadow 0.2s ease;\n}\n\n.component-container.current-step:hover {\n  box-shadow: 0 8px 25px rgba(59, 130, 246, 0.2);\n}\n\n/* Smooth Loading Animation */\n.loading-shimmer {\n  background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);\n  background-size: 200% 100%;\n  animation: shimmer 1.5s infinite;\n}\n\n@keyframes shimmer {\n  0% {\n    background-position: -200% 0;\n  }\n  100% {\n    background-position: 200% 0;\n  }\n}\n\n/* Responsive Design */\n@media (max-width: 640px) {\n  .card-header {\n    flex-direction: column;\n    gap: 8px;\n    align-items: flex-start;\n  }\n  \n  .card-actions {\n    width: 100%;\n    justify-content: flex-end;\n  }\n  \n  .history-modal {\n    width: 95%;\n    margin: 20px;\n  }\n  \n  .card-footer {\n    flex-direction: column;\n    gap: 12px;\n    align-items: stretch;\n  }\n  \n  .new-query-button {\n    width: 100%;\n  }\n  \n  .current-step-container.loading-border::before,\n  .current-step-container.loading-border::after {\n    display: none;\n  }\n  \n  .current-step-container {\n    min-height: 150px;\n  }\n}\n", "",{"version":3,"sources":["webpack://./../agentic_chat/src/CardManager.css"],"names":[],"mappings":"AAAA,wBAAwB;AACxB;EACE,6DAA6D;EAC7D,mBAAmB;EACnB,yBAAyB;EACzB,iFAAiF;EACjF,cAAc;EACd,gBAAgB;EAChB,yBAAyB;EACzB,kBAAkB;AACpB;;AAEA;EACE,sBAAsB;EACtB,oFAAoF;AACtF;;AAEA;EACE,6DAA6D;EAC7D,YAAY;EACZ,kBAAkB;EAClB,aAAa;EACb,8BAA8B;EAC9B,mBAAmB;EACnB,kBAAkB;EAClB,gBAAgB;AAClB;;AAEA;EACE,WAAW;EACX,kBAAkB;EAClB,SAAS;EACT,WAAW;EACX,WAAW;EACX,YAAY;EACZ,sFAAsF;EACtF,wBAAwB;EACxB,8BAA8B;AAChC;;AAEA;EACE,KAAK,0CAA0C,EAAE;EACjD,OAAO,yCAAyC,EAAE;AACpD;;AAEA;EACE,SAAS;EACT,eAAe;EACf,gBAAgB;EAChB,aAAa;EACb,mBAAmB;EACnB,QAAQ;AACV;;AAEA;EACE,aAAa;EACb,eAAe;AACjB;;AAEA;EACE,eAAe;EACf,YAAY;EACZ,eAAe;EACf,gBAAgB;AAClB;;AAEA;EACE,aAAa;EACb,QAAQ;EACR,mBAAmB;AACrB;;AAEA;EACE,oCAAoC;EACpC,0CAA0C;EAC1C,YAAY;EACZ,iBAAiB;EACjB,kBAAkB;EAClB,eAAe;EACf,gBAAgB;EAChB,eAAe;EACf,yBAAyB;EACzB,2BAA2B;AAC7B;;AAEA;EACE,oCAAoC;EACpC,2BAA2B;AAC7B;;AAEA;EACE,aAAa;EACb,iBAAiB;EACjB,iBAAiB;AACnB;;AAEA;EACE,mBAAmB;EACnB,UAAU;EACV,2BAA2B;EAC3B,uCAAuC;AACzC;;AAEA;EACE,oEAAoE;AACtE;;AAEA;EACE;IACE,UAAU;IACV,2BAA2B;EAC7B;EACA;IACE,UAAU;IACV,wBAAwB;EAC1B;AACF;;AAEA;EACE;IACE,2CAA2C;EAC7C;EACA;IACE,6CAA6C;EAC/C;AACF;;AAEA;EACE,6DAA6D;EAC7D,YAAY;EACZ,kBAAkB;EAClB,aAAa;EACb,8BAA8B;EAC9B,mBAAmB;EACnB,6BAA6B;AAC/B;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,QAAQ;EACR,gBAAgB;EAChB,eAAe;AACjB;;AAEA;EACE,oCAAoC;EACpC,0CAA0C;EAC1C,YAAY;EACZ,iBAAiB;EACjB,kBAAkB;EAClB,eAAe;EACf,gBAAgB;EAChB,eAAe;EACf,yBAAyB;EACzB,2BAA2B;AAC7B;;AAEA;EACE,oCAAoC;EACpC,2BAA2B;AAC7B;;AAEA,yBAAyB;AACzB;EACE,eAAe;EACf,MAAM;EACN,OAAO;EACP,QAAQ;EACR,SAAS;EACT,8BAA8B;EAC9B,aAAa;EACb,mBAAmB;EACnB,uBAAuB;EACvB,aAAa;EACb,0BAA0B;EAC1B,2BAA2B;AAC7B;;AAEA;EACE,OAAO,UAAU,EAAE;EACnB,KAAK,UAAU,EAAE;AACnB;;AAEA;EACE,iBAAiB;EACjB,mBAAmB;EACnB,iDAAiD;EACjD,gBAAgB;EAChB,UAAU;EACV,gBAAgB;EAChB,gBAAgB;EAChB,iCAAiC;AACnC;;AAEA;EACE;IACE,UAAU;IACV,uCAAuC;EACzC;EACA;IACE,UAAU;IACV,iCAAiC;EACnC;AACF;;AAEA;EACE,6DAA6D;EAC7D,YAAY;EACZ,aAAa;EACb,aAAa;EACb,8BAA8B;EAC9B,mBAAmB;AACrB;;AAEA;EACE,SAAS;EACT,eAAe;EACf,gBAAgB;AAClB;;AAEA;EACE,aAAa;EACb,QAAQ;EACR,mBAAmB;AACrB;;AAEA;;EAEE,oCAAoC;EACpC,0CAA0C;EAC1C,YAAY;EACZ,iBAAiB;EACjB,kBAAkB;EAClB,eAAe;EACf,gBAAgB;EAChB,eAAe;EACf,yBAAyB;EACzB,2BAA2B;AAC7B;;AAEA;;EAEE,oCAAoC;AACtC;;AAEA;EACE,YAAY;EACZ,eAAe;EACf,cAAc;AAChB;;AAEA;EACE,aAAa;EACb,gBAAgB;EAChB,gBAAgB;AAClB;;AAEA;EACE,kBAAkB;EAClB,cAAc;EACd,kBAAkB;EAClB,kBAAkB;AACpB;;AAEA;EACE,yBAAyB;EACzB,kBAAkB;EAClB,aAAa;EACb,mBAAmB;EACnB,mBAAmB;EACnB,yBAAyB;AAC3B;;AAEA;EACE,qBAAqB;EACrB,6CAA6C;EAC7C,2BAA2B;AAC7B;;AAEA;EACE,aAAa;EACb,8BAA8B;EAC9B,mBAAmB;EACnB,kBAAkB;AACpB;;AAEA;EACE,gBAAgB;EAChB,cAAc;EACd,eAAe;AACjB;;AAEA;EACE,eAAe;EACf,cAAc;AAChB;;AAEA;EACE,mBAAmB;AACrB;;AAEA;EACE,eAAe;EACf,cAAc;EACd,kBAAkB;EAClB,iBAAiB;EACjB,8BAA8B;AAChC;;AAEA;EACE,eAAe;EACf,cAAc;EACd,kBAAkB;EAClB,iBAAiB;EACjB,8BAA8B;AAChC;;AAEA;EACE,mBAAmB;EACnB,YAAY;EACZ,YAAY;EACZ,iBAAiB;EACjB,kBAAkB;EAClB,eAAe;EACf,gBAAgB;EAChB,eAAe;EACf,yBAAyB;AAC3B;;AAEA;EACE,mBAAmB;EACnB,2BAA2B;AAC7B;;AAEA,8BAA8B;AAC9B;EACE,kBAAkB;EAClB,gBAAgB;EAChB,iBAAiB;EACjB,uCAAuC;AACzC;;AAEA,4CAA4C;;AAE5C;EACE,+CAA+C;EAC/C,qBAAqB;EACrB,kBAAkB;EAClB,gBAAgB;AAClB;;AAEA,+CAA+C;AAC/C,yFAAyF;AACzF;EACE,WAAW;EACX,kBAAkB;EAClB,MAAM;EACN,WAAW;EACX,WAAW;EACX,WAAW;EACX,8EAA8E;EAC9E,gDAAgD;EAChD,UAAU;AACZ;;AAEA;EACE;IACE,WAAW;EACb;EACA;IACE,UAAU;EACZ;AACF;;AAEA;EACE;IACE,YAAY;EACd;EACA;IACE,WAAW;EACb;AACF;;AAEA,wBAAwB;;AAExB,+DAA+D;AAC/D,yCAAyC;;AAEzC,yCAAyC;AACzC;EACE,gCAAgC;AAClC;;AAEA;EACE,iEAAiE;EACjE,gBAAgB;AAClB;;AAEA;EACE,aAAa;EACb,UAAU;AACZ;;AAEA;EACE,kBAAkB;EAClB,UAAU;AACZ;;AAEA,0BAA0B;AAC1B;EACE,UAAU;EACV,2BAA2B;AAC7B;;AAEA;EACE,UAAU;EACV,wBAAwB;EACxB,gEAAgE;AAClE;;AAEA;EACE,UAAU;EACV,wBAAwB;AAC1B;;AAEA;EACE,UAAU;EACV,4BAA4B;EAC5B,gEAAgE;AAClE;;AAEA,gCAAgC;AAChC;EACE,yCAAyC;EACzC,gCAAgC;AAClC;;AAEA;EACE,8CAA8C;AAChD;;AAEA,6BAA6B;AAC7B;EACE,yEAAyE;EACzE,0BAA0B;EAC1B,gCAAgC;AAClC;;AAEA;EACE;IACE,4BAA4B;EAC9B;EACA;IACE,2BAA2B;EAC7B;AACF;;AAEA,sBAAsB;AACtB;EACE;IACE,sBAAsB;IACtB,QAAQ;IACR,uBAAuB;EACzB;;EAEA;IACE,WAAW;IACX,yBAAyB;EAC3B;;EAEA;IACE,UAAU;IACV,YAAY;EACd;;EAEA;IACE,sBAAsB;IACtB,SAAS;IACT,oBAAoB;EACtB;;EAEA;IACE,WAAW;EACb;;EAEA;;IAEE,aAAa;EACf;;EAEA;IACE,iBAAiB;EACnB;AACF","sourcesContent":["/* Card Manager Styles */\n.card-manager {\n  background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);\n  border-radius: 12px;\n  border: 1px solid #cbd5e1;\n  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);\n  margin: 16px 0;\n  overflow: hidden;\n  transition: all 0.3s ease;\n  position: relative;\n}\n\n.card-manager.animating {\n  transform: scale(1.02);\n  box-shadow: 0 8px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);\n}\n\n.card-header {\n  background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);\n  color: white;\n  padding: 16px 20px;\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  position: relative;\n  overflow: hidden;\n}\n\n.card-header::before {\n  content: '';\n  position: absolute;\n  top: -50%;\n  right: -50%;\n  width: 100%;\n  height: 200%;\n  background: linear-gradient(45deg, transparent, rgba(255, 255, 255, 0.1), transparent);\n  transform: rotate(45deg);\n  animation: shimmer 3s infinite;\n}\n\n@keyframes shimmer {\n  0% { transform: translateX(-100%) rotate(45deg); }\n  100% { transform: translateX(100%) rotate(45deg); }\n}\n\n.card-title h3 {\n  margin: 0;\n  font-size: 18px;\n  font-weight: 600;\n  display: flex;\n  align-items: center;\n  gap: 8px;\n}\n\n.card-title h3::before {\n  content: '🤖';\n  font-size: 20px;\n}\n\n.step-counter {\n  font-size: 12px;\n  opacity: 0.9;\n  margin-top: 2px;\n  font-weight: 400;\n}\n\n.card-actions {\n  display: flex;\n  gap: 8px;\n  align-items: center;\n}\n\n.history-button {\n  background: rgba(255, 255, 255, 0.2);\n  border: 1px solid rgba(255, 255, 255, 0.3);\n  color: white;\n  padding: 6px 12px;\n  border-radius: 6px;\n  font-size: 12px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s ease;\n  backdrop-filter: blur(10px);\n}\n\n.history-button:hover {\n  background: rgba(255, 255, 255, 0.3);\n  transform: translateY(-1px);\n}\n\n.card-content {\n  padding: 20px;\n  background: white;\n  min-height: 100px;\n}\n\n.step-item {\n  margin-bottom: 16px;\n  opacity: 0;\n  transform: translateY(20px);\n  animation: slideInUp 0.5s ease forwards;\n}\n\n.step-item.new-step {\n  animation: slideInUp 0.5s ease forwards, highlightPulse 2s ease 0.5s;\n}\n\n@keyframes slideInUp {\n  from {\n    opacity: 0;\n    transform: translateY(20px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n@keyframes highlightPulse {\n  0%, 100% {\n    box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.4);\n  }\n  50% {\n    box-shadow: 0 0 0 8px rgba(59, 130, 246, 0.1);\n  }\n}\n\n.card-footer {\n  background: linear-gradient(135deg, #10b981 0%, #059669 100%);\n  color: white;\n  padding: 16px 20px;\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  border-top: 1px solid #d1fae5;\n}\n\n.completion-message {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  font-weight: 600;\n  font-size: 14px;\n}\n\n.new-query-button {\n  background: rgba(255, 255, 255, 0.2);\n  border: 1px solid rgba(255, 255, 255, 0.3);\n  color: white;\n  padding: 8px 16px;\n  border-radius: 6px;\n  font-size: 12px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s ease;\n  backdrop-filter: blur(10px);\n}\n\n.new-query-button:hover {\n  background: rgba(255, 255, 255, 0.3);\n  transform: translateY(-1px);\n}\n\n/* History Modal Styles */\n.history-modal-overlay {\n  position: fixed;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  background: rgba(0, 0, 0, 0.5);\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  z-index: 1000;\n  backdrop-filter: blur(4px);\n  animation: fadeIn 0.3s ease;\n}\n\n@keyframes fadeIn {\n  from { opacity: 0; }\n  to { opacity: 1; }\n}\n\n.history-modal {\n  background: white;\n  border-radius: 12px;\n  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);\n  max-width: 600px;\n  width: 90%;\n  max-height: 80vh;\n  overflow: hidden;\n  animation: slideInModal 0.3s ease;\n}\n\n@keyframes slideInModal {\n  from {\n    opacity: 0;\n    transform: scale(0.9) translateY(-20px);\n  }\n  to {\n    opacity: 1;\n    transform: scale(1) translateY(0);\n  }\n}\n\n.history-header {\n  background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);\n  color: white;\n  padding: 20px;\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n}\n\n.history-header h3 {\n  margin: 0;\n  font-size: 18px;\n  font-weight: 600;\n}\n\n.history-actions {\n  display: flex;\n  gap: 8px;\n  align-items: center;\n}\n\n.clear-history-button,\n.close-history-button {\n  background: rgba(255, 255, 255, 0.2);\n  border: 1px solid rgba(255, 255, 255, 0.3);\n  color: white;\n  padding: 6px 12px;\n  border-radius: 6px;\n  font-size: 12px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s ease;\n  backdrop-filter: blur(10px);\n}\n\n.clear-history-button:hover,\n.close-history-button:hover {\n  background: rgba(255, 255, 255, 0.3);\n}\n\n.close-history-button {\n  padding: 6px;\n  font-size: 16px;\n  line-height: 1;\n}\n\n.history-content {\n  padding: 20px;\n  max-height: 60vh;\n  overflow-y: auto;\n}\n\n.no-history {\n  text-align: center;\n  color: #6b7280;\n  font-style: italic;\n  padding: 40px 20px;\n}\n\n.history-card {\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  padding: 16px;\n  margin-bottom: 12px;\n  background: #f9fafb;\n  transition: all 0.2s ease;\n}\n\n.history-card:hover {\n  border-color: #3b82f6;\n  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);\n  transform: translateY(-1px);\n}\n\n.history-card-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  margin-bottom: 8px;\n}\n\n.history-card-title {\n  font-weight: 600;\n  color: #374151;\n  font-size: 14px;\n}\n\n.history-card-meta {\n  font-size: 12px;\n  color: #6b7280;\n}\n\n.history-card-preview {\n  margin-bottom: 12px;\n}\n\n.history-step-preview {\n  font-size: 12px;\n  color: #4b5563;\n  margin-bottom: 4px;\n  padding-left: 8px;\n  border-left: 2px solid #e5e7eb;\n}\n\n.history-step-more {\n  font-size: 11px;\n  color: #9ca3af;\n  font-style: italic;\n  padding-left: 8px;\n  border-left: 2px solid #e5e7eb;\n}\n\n.restore-card-button {\n  background: #3b82f6;\n  color: white;\n  border: none;\n  padding: 6px 12px;\n  border-radius: 6px;\n  font-size: 12px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s ease;\n}\n\n.restore-card-button:hover {\n  background: #2563eb;\n  transform: translateY(-1px);\n}\n\n/* In-Place Card Transitions */\n.current-step-container {\n  position: relative;\n  overflow: hidden;\n  min-height: 200px;\n  transition: min-height 0.3s ease-in-out;\n}\n\n/* No container animation – instant switch */\n\n.component-container.current-step {\n  box-shadow: 0 4px 12px rgba(59, 130, 246, 0.15);\n  border-color: #3b82f6;\n  position: relative;\n  overflow: hidden;\n}\n\n/* Loading step with sliding border animation */\n/* Shared loading border lives on the persistent container so it continues across swaps */\n.current-step-container.loading-border::before {\n  content: '';\n  position: absolute;\n  top: 0;\n  left: -100%;\n  width: 100%;\n  height: 2px;\n  background: linear-gradient(90deg, transparent, #3b82f6, #06b6d4, transparent);\n  animation: borderSlide 2.5s ease-in-out infinite;\n  z-index: 1;\n}\n\n@keyframes borderSlide {\n  0% {\n    left: -100%;\n  }\n  100% {\n    left: 100%;\n  }\n}\n\n@keyframes borderSlideReverse {\n  0% {\n    right: -100%;\n  }\n  100% {\n    right: 100%;\n  }\n}\n\n/* No appear animation */\n\n/* Non-current steps rendered only in reasoning list; no fade */\n.component-container:not(.current-step) {}\n\n/* Reasoning Process Collapse Animation */\n.reasoning-section {\n  transition: all 0.5s ease-in-out;\n}\n\n.reasoning-content {\n  transition: max-height 0.5s ease-in-out, opacity 0.3s ease-in-out;\n  overflow: hidden;\n}\n\n.reasoning-content.collapsed {\n  max-height: 0;\n  opacity: 0;\n}\n\n.reasoning-content.expanded {\n  max-height: 2000px;\n  opacity: 1;\n}\n\n/* Step Fade Transitions */\n.step-fade-enter {\n  opacity: 0;\n  transform: translateY(20px);\n}\n\n.step-fade-enter-active {\n  opacity: 1;\n  transform: translateY(0);\n  transition: opacity 0.3s ease-in-out, transform 0.3s ease-in-out;\n}\n\n.step-fade-exit {\n  opacity: 1;\n  transform: translateY(0);\n}\n\n.step-fade-exit-active {\n  opacity: 0;\n  transform: translateY(-20px);\n  transition: opacity 0.3s ease-in-out, transform 0.3s ease-in-out;\n}\n\n/* Enhanced Card Hover Effects */\n.component-container:hover {\n  box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);\n  transition: box-shadow 0.2s ease;\n}\n\n.component-container.current-step:hover {\n  box-shadow: 0 8px 25px rgba(59, 130, 246, 0.2);\n}\n\n/* Smooth Loading Animation */\n.loading-shimmer {\n  background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);\n  background-size: 200% 100%;\n  animation: shimmer 1.5s infinite;\n}\n\n@keyframes shimmer {\n  0% {\n    background-position: -200% 0;\n  }\n  100% {\n    background-position: 200% 0;\n  }\n}\n\n/* Responsive Design */\n@media (max-width: 640px) {\n  .card-header {\n    flex-direction: column;\n    gap: 8px;\n    align-items: flex-start;\n  }\n  \n  .card-actions {\n    width: 100%;\n    justify-content: flex-end;\n  }\n  \n  .history-modal {\n    width: 95%;\n    margin: 20px;\n  }\n  \n  .card-footer {\n    flex-direction: column;\n    gap: 12px;\n    align-items: stretch;\n  }\n  \n  .new-query-button {\n    width: 100%;\n  }\n  \n  .current-step-container.loading-border::before,\n  .current-step-container.loading-border::after {\n    display: none;\n  }\n  \n  .current-step-container {\n    min-height: 150px;\n  }\n}\n"],"sourceRoot":""}]);
// Exports
/* harmony default export */ __webpack_exports__["default"] = (___CSS_LOADER_EXPORT___);


/***/ })

}]);
//# sourceMappingURL=main-agentic_chat_src_CardManager_c.a91b9539e5cca7d366ae.js.map