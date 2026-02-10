"use strict";
(self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] = self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] || []).push([["main-agentic_chat_src_St"],{

/***/ "../agentic_chat/src/StatusBar.css":
/*!*****************************************!*\
  !*** ../agentic_chat/src/StatusBar.css ***!
  \*****************************************/
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
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_StatusBar_css__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! !!../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!./StatusBar.css */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/StatusBar.css");

      
      
      
      
      
      
      
      
      

var options = {};

options.styleTagTransform = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default());
options.setAttributes = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default());
options.insert = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default().bind(null, "head");
options.domAPI = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default());
options.insertStyleElement = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default());

var update = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default()(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_StatusBar_css__WEBPACK_IMPORTED_MODULE_6__["default"], options);




       /* unused harmony default export */ var __WEBPACK_DEFAULT_EXPORT__ = (_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_StatusBar_css__WEBPACK_IMPORTED_MODULE_6__["default"] && _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_StatusBar_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals ? _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_StatusBar_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals : undefined);


/***/ }),

/***/ "../agentic_chat/src/StatusBar.tsx":
/*!*****************************************!*\
  !*** ../agentic_chat/src/StatusBar.tsx ***!
  \*****************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   StatusBar: function() { return /* binding */ StatusBar; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");
/* harmony import */ var _exampleUtterances__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./exampleUtterances */ "../agentic_chat/src/exampleUtterances.ts");
/* harmony import */ var _StatusBar_css__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ./StatusBar.css */ "../agentic_chat/src/StatusBar.css");




function StatusBar({
  threadId
}) {
  const [tools, setTools] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [internalToolsCount, setInternalToolsCount] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({});
  const [mode, setMode] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("fast");
  const [agentMode, setAgentMode] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("supervisor");
  const [subAgents, setSubAgents] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [showToolsPopup, setShowToolsPopup] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [showAgentsPopup, setShowAgentsPopup] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [showAgentSelector, setShowAgentSelector] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [selectedAgent, setSelectedAgent] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [showMoreMenu, setShowMoreMenu] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [showExamplesPopup, setShowExamplesPopup] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [showModePopup, setShowModePopup] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [isInputEmpty, setIsInputEmpty] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(true);
  const [visibleItems, setVisibleItems] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(new Set(['tools', 'mode', 'agents', 'connection']));
  const statusBarRef = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)(null);
  const agentsPopupTimeoutRef = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)(null);
  const examplesPopupTimeoutRef = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)(null);
  const modePopupTimeoutRef = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)(null);

  // Log threadId changes for debugging
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    console.log('[StatusBar] threadId updated:', threadId);
  }, [threadId]);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    loadTools();
    loadSubAgents();
  }, []);

  // Cleanup timeouts on unmount
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    return () => {
      if (agentsPopupTimeoutRef.current) {
        clearTimeout(agentsPopupTimeoutRef.current);
      }
      if (examplesPopupTimeoutRef.current) {
        clearTimeout(examplesPopupTimeoutRef.current);
      }
      if (modePopupTimeoutRef.current) {
        clearTimeout(modePopupTimeoutRef.current);
      }
    };
  }, []);

  // Monitor input field to detect if it's empty
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    const checkInputEmpty = () => {
      const inputField = document.getElementById('main-input_field');
      if (inputField) {
        const isEmpty = !inputField.textContent?.trim();
        setIsInputEmpty(isEmpty);
      }
    };

    // Check initially
    checkInputEmpty();

    // Set up observer to watch for changes
    const inputField = document.getElementById('main-input_field');
    if (inputField) {
      const observer = new MutationObserver(checkInputEmpty);
      observer.observe(inputField, {
        characterData: true,
        childList: true,
        subtree: true
      });

      // Also listen for input events
      inputField.addEventListener('input', checkInputEmpty);
      return () => {
        observer.disconnect();
        inputField.removeEventListener('input', checkInputEmpty);
      };
    }
  }, []);

  // Responsive behavior - hide items when container is too narrow
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    const updateVisibleItems = () => {
      if (!statusBarRef.current) return;
      const containerWidth = statusBarRef.current.offsetWidth;
      const newVisibleItems = new Set();

      // Priority order: connection (always visible), tools, mode, agents
      if (containerWidth > 800) {
        newVisibleItems.add('tools');
        newVisibleItems.add('mode');
        newVisibleItems.add('agents');
      } else if (containerWidth > 600) {
        newVisibleItems.add('tools');
        newVisibleItems.add('mode');
      } else if (containerWidth > 400) {
        newVisibleItems.add('tools');
      }
      // Connection is always visible

      setVisibleItems(newVisibleItems);
    };
    updateVisibleItems();
    const resizeObserver = new ResizeObserver(updateVisibleItems);
    if (statusBarRef.current) {
      resizeObserver.observe(statusBarRef.current);
    }
    return () => {
      resizeObserver.disconnect();
    };
  }, []);
  const loadTools = async () => {
    try {
      const response = await fetch('/api/tools/status');
      if (response.ok) {
        const data = await response.json();
        setTools(data.tools || []);
        setInternalToolsCount(data.internalToolsCount || {});
      }
    } catch (error) {
      console.error("Error loading tools:", error);
    }
  };
  const loadSubAgents = async () => {
    try {
      const response = await fetch('/api/config/subagents');
      if (response.ok) {
        const data = await response.json();
        setSubAgents(data.subAgents || []);
        setAgentMode(data.mode || "supervisor");
        setSelectedAgent(data.selectedAgent || null);
      }
    } catch (error) {
      console.error("Error loading sub-agents:", error);
    }
  };
  const toggleMode = () => {
    // Mode switching disabled - requires local setup
    return;
  };
  const toggleAgentMode = () => {
    const newMode = agentMode === "supervisor" ? "single" : "supervisor";
    if (newMode === "single") {
      // Show agent selector when switching to single mode
      setShowAgentSelector(true);
    } else {
      // Clear selected agent when switching to supervisor mode
      setSelectedAgent(null);
      setAgentMode(newMode);
      // Send agent mode change to backend
      fetch('/api/config/agent-mode', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          mode: newMode,
          selectedAgent: null
        })
      }).catch(err => console.error("Failed to update agent mode:", err));
    }
  };
  const selectAgent = agentName => {
    setSelectedAgent(agentName);
    setAgentMode("single");
    setShowAgentSelector(false);
    // Send agent selection to backend
    fetch('/api/config/agent-mode', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        mode: "single",
        selectedAgent: agentName
      })
    }).catch(err => console.error("Failed to update agent mode:", err));
  };
  const cancelAgentSelection = () => {
    setShowAgentSelector(false);
    // Keep current mode if cancelled
  };
  const toggleAgentEnabled = agentName => {
    const updatedAgents = subAgents.map(agent => agent.name === agentName ? {
      ...agent,
      enabled: !agent.enabled
    } : agent);
    setSubAgents(updatedAgents);

    // Send update to backend
    fetch('/api/config/subagents', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        subAgents: updatedAgents,
        mode: agentMode,
        selectedAgent: selectedAgent
      })
    }).catch(err => console.error("Failed to update agent status:", err));
  };
  const handleAgentsMouseEnter = () => {
    // Clear any pending hide timeout
    if (agentsPopupTimeoutRef.current) {
      clearTimeout(agentsPopupTimeoutRef.current);
      agentsPopupTimeoutRef.current = null;
    }
    setShowAgentsPopup(true);
  };
  const handleAgentsMouseLeave = () => {
    // Delay hiding the popup to allow mouse movement to the popup
    agentsPopupTimeoutRef.current = setTimeout(() => {
      setShowAgentsPopup(false);
    }, 300); // 300ms delay
  };
  const handleAgentsPopupMouseEnter = () => {
    // Clear the hide timeout when mouse enters the popup
    if (agentsPopupTimeoutRef.current) {
      clearTimeout(agentsPopupTimeoutRef.current);
      agentsPopupTimeoutRef.current = null;
    }
  };
  const handleAgentsPopupMouseLeave = () => {
    // Hide the popup when mouse leaves the popup area
    setShowAgentsPopup(false);
  };
  const handleExamplesMouseEnter = () => {
    // Clear any pending hide timeout
    if (examplesPopupTimeoutRef.current) {
      clearTimeout(examplesPopupTimeoutRef.current);
      examplesPopupTimeoutRef.current = null;
    }
    setShowExamplesPopup(true);
  };
  const handleExamplesMouseLeave = () => {
    // Delay hiding the popup to allow mouse movement to the popup
    examplesPopupTimeoutRef.current = setTimeout(() => {
      setShowExamplesPopup(false);
    }, 5000); // 5000ms (5 seconds) delay
  };
  const handleExamplesPopupMouseEnter = () => {
    // Clear the hide timeout when mouse enters the popup
    if (examplesPopupTimeoutRef.current) {
      clearTimeout(examplesPopupTimeoutRef.current);
      examplesPopupTimeoutRef.current = null;
    }
  };
  const handleExamplesPopupMouseLeave = () => {
    // Hide the popup when mouse leaves the popup area
    setShowExamplesPopup(false);
  };
  const handleModeMouseEnter = () => {
    console.log('[StatusBar] Mode hover entered');
    // Clear any pending hide timeout
    if (modePopupTimeoutRef.current) {
      clearTimeout(modePopupTimeoutRef.current);
      modePopupTimeoutRef.current = null;
    }
    setShowModePopup(true);
    console.log('[StatusBar] showModePopup set to true');
  };
  const handleModeMouseLeave = () => {
    console.log('[StatusBar] Mode hover left');
    // Delay hiding the popup with longer delay
    modePopupTimeoutRef.current = setTimeout(() => {
      setShowModePopup(false);
      console.log('[StatusBar] showModePopup set to false');
    }, 500);
  };
  const handleModePopupMouseEnter = () => {
    console.log('[StatusBar] Mode popup hover entered');
    // Clear the hide timeout when mouse enters the popup
    if (modePopupTimeoutRef.current) {
      clearTimeout(modePopupTimeoutRef.current);
      modePopupTimeoutRef.current = null;
    }
  };
  const handleModePopupMouseLeave = () => {
    console.log('[StatusBar] Mode popup hover left');
    // Delay hiding with longer timeout for stability
    modePopupTimeoutRef.current = setTimeout(() => {
      setShowModePopup(false);
    }, 500);
  };
  const connectedTools = tools.filter(t => t.status === "connected");
  const errorTools = tools.filter(t => t.status === "error");
  const activeAgents = subAgents.filter(a => a.enabled);
  const getSelectedAgentInfo = () => {
    if (!selectedAgent) return null;
    return subAgents.find(a => a.name === selectedAgent);
  };
  const handleExampleClick = utterance => {
    // Send the utterance to the input field
    const inputField = document.getElementById('main-input_field');
    if (inputField) {
      inputField.textContent = utterance;
      inputField.focus();
      // Trigger input event to update parent component
      const event = new Event('input', {
        bubbles: true
      });
      inputField.dispatchEvent(event);
    }
    setShowExamplesPopup(false);
  };

  // Get overflow items for the More menu
  const getOverflowItems = () => {
    const overflowItems = [];
    if (!visibleItems.has('mode')) {
      overflowItems.push({
        id: 'mode',
        label: `Mode: ${mode === 'fast' ? 'Lite' : 'Balanced'}`,
        icon: /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Zap, {
          size: 14
        }),
        action: toggleMode
      });
    }
    if (!visibleItems.has('agents')) {
      overflowItems.push({
        id: 'agents',
        label: `${agentMode === 'supervisor' ? 'Supervisor' : 'Single'} (${agentMode === 'supervisor' ? activeAgents.length : selectedAgent ? getSelectedAgentInfo()?.name : 'None'})`,
        icon: agentMode === 'supervisor' ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Users, {
          size: 14
        }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.User, {
          size: 14
        }),
        action: () => setShowAgentsPopup(true)
      });
    }
    if (!visibleItems.has('tools')) {
      overflowItems.push({
        id: 'tools',
        label: `Tools: ${connectedTools.length}/${tools.length}`,
        icon: /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Wrench, {
          size: 14
        }),
        action: () => setShowToolsPopup(true)
      });
    }
    return overflowItems;
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, showAgentSelector && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-overlay",
    onClick: cancelAgentSelection
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal",
    onClick: e => e.stopPropagation(),
    style: {
      maxWidth: "500px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h2", null, "Select Agent to Talk With"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "config-modal-close",
    onClick: cancelAgentSelection
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    style: {
      fontSize: "20px"
    }
  }, "\xD7"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-content"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    style: {
      marginBottom: "16px",
      color: "#64748b",
      fontSize: "14px"
    }
  }, "Choose which agent you want to communicate with directly:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: "8px"
    }
  }, subAgents.filter(a => a.enabled).length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      padding: "24px",
      textAlign: "center",
      color: "#94a3b8",
      background: "#f8fafc",
      borderRadius: "8px"
    }
  }, "No active agents available. Enable agents in Sub Agents configuration.") : subAgents.filter(a => a.enabled).map(agent => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: agent.name,
    onClick: () => selectAgent(agent.name),
    style: {
      padding: "16px",
      background: "#f8fafc",
      border: "2px solid #e5e7eb",
      borderRadius: "8px",
      cursor: "pointer",
      transition: "all 0.2s"
    },
    onMouseEnter: e => {
      e.currentTarget.style.borderColor = "#667eea";
      e.currentTarget.style.background = "#f1f5f9";
    },
    onMouseLeave: e => {
      e.currentTarget.style.borderColor = "#e5e7eb";
      e.currentTarget.style.background = "#f8fafc";
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      fontWeight: 600,
      fontSize: "14px",
      color: "#1e293b",
      marginBottom: "4px"
    }
  }, agent.name), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      fontSize: "12px",
      color: "#64748b"
    }
  }, agent.role))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-footer"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "cancel-btn",
    onClick: cancelAgentSelection
  }, "Cancel")))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "status-bar",
    ref: statusBarRef
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "status-bar-left"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "status-bar-center"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: `status-item status-examples ${isInputEmpty ? 'animate-prompt' : ''}`,
    onMouseEnter: handleExamplesMouseEnter,
    onMouseLeave: handleExamplesMouseLeave
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Lightbulb, {
    size: 14,
    className: isInputEmpty ? 'lightbulb-glow' : ''
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "status-label"
  }, "Try these examples"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "status-badge"
  }, _exampleUtterances__WEBPACK_IMPORTED_MODULE_2__.exampleUtterances.length), showExamplesPopup && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "examples-popup",
    onMouseEnter: handleExamplesPopupMouseEnter,
    onMouseLeave: handleExamplesPopupMouseLeave
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "examples-popup-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Example Queries"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "examples-count"
  }, _exampleUtterances__WEBPACK_IMPORTED_MODULE_2__.exampleUtterances.length, " examples")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "examples-list"
  }, _exampleUtterances__WEBPACK_IMPORTED_MODULE_2__.exampleUtterances.map((utterance, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    className: "example-item",
    onClick: () => handleExampleClick(utterance.text)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Lightbulb, {
    size: 12,
    className: "example-icon"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "example-text"
  }, utterance.text)))))), visibleItems.has('tools') && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "status-item status-tools",
    onMouseEnter: () => setShowToolsPopup(true),
    onMouseLeave: () => setShowToolsPopup(false)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Wrench, {
    size: 14
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "status-label"
  }, "Tools"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "status-badge"
  }, connectedTools.length), errorTools.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.AlertCircle, {
    size: 12,
    className: "status-warning"
  }), showToolsPopup && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tools-popup"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tools-popup-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Connected Tools"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "tools-count"
  }, connectedTools.length, "/", tools.length)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tools-list"
  }, tools.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tools-empty"
  }, "No tools configured") : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, Object.entries(tools.reduce((acc, tool) => {
    if (!acc[tool.type]) {
      acc[tool.type] = {
        total: 0,
        connected: 0,
        tools: []
      };
    }
    acc[tool.type].total++;
    if (tool.status === 'connected') {
      acc[tool.type].connected++;
    }
    acc[tool.type].tools.push(tool);
    return acc;
  }, {})).map(([type, data]) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: type,
    className: "tool-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tool-group-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "tool-group-name"
  }, type), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tool-group-stats"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "tool-group-count"
  }, "Connected: ", data.connected, "/", data.total), internalToolsCount[type.toLowerCase()] !== undefined && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "tool-group-internal"
  }, "Internal: ", internalToolsCount[type.toLowerCase()], " tools"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tool-group-items"
  }, data.tools.map(tool => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: tool.name,
    className: `tool-item ${tool.status}`
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tool-status-indicator"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tool-info"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "tool-name"
  }, tool.name)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "tool-status-text"
  }, tool.status)))))))))), visibleItems.has('mode') && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "status-item status-mode",
    style: {
      position: 'relative',
      cursor: 'pointer'
    },
    onMouseEnter: handleModeMouseEnter,
    onMouseLeave: handleModeMouseLeave
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Zap, {
    size: 14
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mode-toggle"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: `mode-option ${mode === "fast" ? "active" : ""} disabled`,
    style: {
      cursor: 'not-allowed',
      opacity: 0.6
    }
  }, "Lite"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: `mode-option ${mode === "balanced" ? "active" : ""}`,
    style: {
      cursor: 'pointer'
    }
  }, "Balanced")), showModePopup && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tools-popup",
    onMouseEnter: handleModePopupMouseEnter,
    onMouseLeave: handleModePopupMouseLeave
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tools-popup-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "This feature works locally")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tools-list",
    style: {
      padding: '12px 14px'
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      marginBottom: '12px',
      color: '#64748b',
      fontSize: '13px',
      lineHeight: '1.5'
    }
  }, "Clone the repo to experience full features of CUGA:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("a", {
    href: "https://github.com/cuga-project/cuga-agent",
    target: "_blank",
    rel: "noopener noreferrer",
    style: {
      color: '#667eea',
      textDecoration: 'none',
      fontWeight: 500,
      fontSize: '13px',
      display: 'inline-flex',
      alignItems: 'center',
      gap: '6px',
      padding: '4px 0'
    },
    onMouseEnter: e => {
      e.currentTarget.style.textDecoration = 'underline';
    },
    onMouseLeave: e => {
      e.currentTarget.style.textDecoration = 'none';
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "github.com/cuga-project/cuga-agent"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("svg", {
    width: "12",
    height: "12",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("path", {
    d: "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("polyline", {
    points: "15 3 21 3 21 9"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("line", {
    x1: "10",
    y1: "14",
    x2: "21",
    y2: "3"
  })))))), visibleItems.has('agents') && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "status-item status-agents",
    onMouseEnter: handleAgentsMouseEnter,
    onMouseLeave: handleAgentsMouseLeave
  }, agentMode === "supervisor" ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Users, {
    size: 14
  }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.User, {
    size: 14
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mode-toggle"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: `mode-option ${agentMode === "supervisor" ? "active" : ""}`,
    onClick: e => {
      e.stopPropagation();
      if (agentMode !== "supervisor") {
        toggleAgentMode();
      }
    }
  }, "Supervisor"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: `mode-option ${agentMode === "single" ? "active" : ""} disabled`,
    title: "Single agent mode (Coming soon)"
  }, "Single")), agentMode === "supervisor" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "status-badge"
  }, activeAgents.length), showAgentsPopup && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "agents-popup",
    onMouseEnter: handleAgentsPopupMouseEnter,
    onMouseLeave: handleAgentsPopupMouseLeave
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "agents-popup-header"
  }, agentMode === "supervisor" ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Talking with All Agents"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "agents-count"
  }, activeAgents.length, " active")) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Direct Agent Communication"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "agents-count"
  }, "Single mode"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "agents-list"
  }, agentMode === "supervisor" ? subAgents.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "agents-empty"
  }, "No sub-agents configured") : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "agents-info-box"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "agents-info-label"
  }, "Available Sub-Agents (click to toggle):")), subAgents.map(agent => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: agent.name,
    className: `agent-item ${agent.enabled ? "enabled" : "disabled"}`,
    onClick: e => {
      e.stopPropagation();
      toggleAgentEnabled(agent.name);
    },
    style: {
      cursor: "pointer"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: agent.enabled,
    onChange: () => {},
    style: {
      cursor: "pointer",
      marginRight: "8px",
      width: "16px",
      height: "16px"
    },
    onClick: e => e.stopPropagation()
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "agent-status-indicator"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "agent-info"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "agent-name"
  }, agent.name), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "agent-role"
  }, agent.role)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "agent-status-text"
  }, agent.enabled ? "active" : "inactive")))) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "agents-info-box single-mode"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.User, {
    size: 32,
    className: "single-agent-icon"
  }), selectedAgent ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "single-agent-label"
  }, "Talking with: ", getSelectedAgentInfo()?.name), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "single-agent-description"
  }, "Role: ", getSelectedAgentInfo()?.role), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: e => {
      e.stopPropagation();
      setShowAgentSelector(true);
    },
    style: {
      marginTop: "8px",
      padding: "6px 12px",
      background: "#667eea",
      color: "white",
      border: "none",
      borderRadius: "6px",
      fontSize: "12px",
      cursor: "pointer"
    }
  }, "Change Agent")) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "single-agent-label"
  }, "Direct Agent Communication"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "single-agent-description"
  }, "Click to select which agent to talk with."), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: e => {
      e.stopPropagation();
      setShowAgentSelector(true);
    },
    style: {
      marginTop: "8px",
      padding: "6px 12px",
      background: "#667eea",
      color: "white",
      border: "none",
      borderRadius: "6px",
      fontSize: "12px",
      cursor: "pointer"
    }
  }, "Select Agent"))))))), getOverflowItems().length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "status-item status-more",
    onMouseEnter: () => setShowMoreMenu(true),
    onMouseLeave: () => setShowMoreMenu(false)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.MoreHorizontal, {
    size: 14
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "status-label"
  }, "More"), showMoreMenu && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "more-popup"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "more-popup-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "More Options")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "more-list"
  }, getOverflowItems().map(item => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: item.id,
    className: "more-item",
    onClick: e => {
      e.stopPropagation();
      item.action();
      setShowMoreMenu(false);
    }
  }, item.icon, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "more-item-label"
  }, item.label)))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "status-bar-right"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "status-item status-connection"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.CheckCircle2, {
    size: 14,
    className: "status-connected"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "status-label"
  }, "Connected")))));
}

/***/ }),

/***/ "../agentic_chat/src/StreamManager.tsx":
/*!*********************************************!*\
  !*** ../agentic_chat/src/StreamManager.tsx ***!
  \*********************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   streamStateManager: function() { return /* binding */ streamStateManager; }
/* harmony export */ });
// streamStateManager.ts

class StreamStateManager {
  isStreaming = false;
  listeners = new Set();
  currentAbortController = null;
  setStreaming(streaming) {
    this.isStreaming = streaming;
    console.log("listeners", this.listeners);
    this.listeners.forEach(listener => listener(streaming));
  }
  getIsStreaming() {
    return this.isStreaming;
  }
  subscribe(listener) {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
  setAbortController(controller) {
    this.currentAbortController = controller;
  }
  async stopStream() {
    if (this.currentAbortController) {
      this.currentAbortController.abort();
    }
    try {
      const response = await fetch(`${API_BASE_URL}/stop`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        }
      });
      if (!response.ok) {
        console.error("Failed to stop stream on server");
      }
    } catch (error) {
      console.error("Error stopping stream:", error);
    }
    this.setStreaming(false);
  }
}
const streamStateManager = new StreamStateManager();

/***/ }),

/***/ "../agentic_chat/src/StreamingWorkflow.ts":
/*!************************************************!*\
  !*** ../agentic_chat/src/StreamingWorkflow.ts ***!
  \************************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   fetchStreamingData: function() { return /* binding */ fetchStreamingData; }
/* harmony export */ });
/* unused harmony exports streamViaBackground, USE_FAKE_STREAM, FAKE_STREAM_FILE, FAKE_STREAM_DELAY */
/* harmony import */ var _microsoft_fetch_event_source__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! @microsoft/fetch-event-source */ "../node_modules/.pnpm/@microsoft+fetch-event-source@2.0.1/node_modules/@microsoft/fetch-event-source/lib/esm/index.js");
/* harmony import */ var _StreamManager__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./StreamManager */ "../agentic_chat/src/StreamManager.tsx");
/* harmony import */ var _constants__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./constants */ "../agentic_chat/src/constants.ts");




// When built without webpack DefinePlugin, `FAKE_STREAM` may not exist at runtime.
// Declare it for TypeScript and compute a safe value that won't throw if undefined.

const USE_FAKE_STREAM =  true ? !!false : 0;
const FAKE_STREAM_FILE = "/fake_data.json"; // Path to your JSON file
const FAKE_STREAM_DELAY = 1000; // Delay between fake stream events in milliseconds
// Unique timestamp generator for IDs
const generateTimestampId = () => {
  return Date.now().toString();
};
function renderPlan(planJson) {
  console.log("Current plan json", planJson);
  return planJson;
}
function getCurrentStep(event) {
  console.log("getCurrentStep received: ", event);
  switch (event.event) {
    case "__interrupt__":
      return;
    case "Stopped":
      // Handle the stopped event from the server
      if (window.aiSystemInterface) {
        window.aiSystemInterface.stopProcessing();
      }
      return renderPlan(event.data);
    default:
      return renderPlan(event.data);
  }
}
const simulateFakeStream = async (instance, query) => {
  console.log("Starting fake stream simulation with query:", query.substring(0, 50));

  // Create abort controller for this stream
  const abortController = new AbortController();
  _StreamManager__WEBPACK_IMPORTED_MODULE_1__.streamStateManager.setAbortController(abortController);
  let fullResponse = "";
  let workflowInitialized = false;
  let workflowId = "workflow_" + generateTimestampId();

  // Set streaming state AFTER setting abort controller
  _StreamManager__WEBPACK_IMPORTED_MODULE_1__.streamStateManager.setStreaming(true);
  try {
    // Check if already aborted before starting
    if (abortController.signal.aborted) {
      console.log("Stream aborted before starting");
      return fullResponse;
    }

    // Load the fake stream data from JSON file
    const response = await fetch(FAKE_STREAM_FILE, {
      signal: abortController.signal // Pass abort signal to fetch
    });
    if (!response.ok) {
      throw new Error(`Failed to load fake stream data: ${response.status} ${response.statusText}`);
    }
    const fakeStreamData = await response.json();
    if (!fakeStreamData.steps || !Array.isArray(fakeStreamData.steps)) {
      throw new Error("Invalid fake stream data format. Expected { steps: [{ name: string, data: any }] }");
    }
    workflowInitialized = true;

    // Card manager message is already created in customSendMessage, so we don't need to create another one here
    if (window.aiSystemInterface) {
      console.log("Card manager interface available for fake stream, skipping duplicate message creation");
    }

    // Use abortable delay for initial wait
    await abortableDelay(300, abortController.signal);

    // Process each step from the fake data
    for (let i = 0; i < fakeStreamData.steps.length; i++) {
      // Check abort signal at the start of each iteration
      if (abortController.signal.aborted) {
        console.log("Fake stream process aborted by user at step", i);
        break;
      }
      const step = fakeStreamData.steps[i];
      console.log(`Processing step ${i + 1}/${fakeStreamData.steps.length}: ${step.name}`);

      // Use abortable delay instead of regular setTimeout
      await abortableDelay(FAKE_STREAM_DELAY, abortController.signal);

      // Check again after delay in case it was aborted during the wait
      if (abortController.signal.aborted) {
        console.log("Fake stream process aborted during delay at step", i);
        break;
      }

      // Simulate the event
      const fakeEvent = {
        event: step.name,
        data: step.data
      };
      console.log("Simulating fake stream event:", fakeEvent);
      let currentStep = getCurrentStep(fakeEvent);
      let stepTitle = step.name;

      // Add the message (this is not abortable, but it's fast)
      // Use the card manager if available, otherwise add individual messages
      if (window.aiSystemInterface) {
        window.aiSystemInterface.addStep(stepTitle, currentStep);
      } else {
        await instance.messaging.addMessage({
          message_options: {
            response_user_profile: _constants__WEBPACK_IMPORTED_MODULE_2__.RESPONSE_USER_PROFILE
          },
          output: {
            generic: [{
              id: workflowId + stepTitle,
              response_type: "user_defined",
              user_defined: {
                user_defined_type: "my_unique_identifier",
                data: currentStep,
                step_title: stepTitle
              }
            }]
          }
        });
      }

      // Final check after adding message
      if (abortController.signal.aborted) {
        console.log("Fake stream process aborted after adding message at step", i);
        break;
      }
    }

    // If we completed all steps without aborting
    if (!abortController.signal.aborted) {
      console.log("Fake stream completed successfully");
    }
    return fullResponse;
  } catch (error) {
    if (error.name === "AbortError" || abortController.signal.aborted) {
      console.log("Fake stream was cancelled by user");

      // Add a message indicating the stream was stopped
      await instance.messaging.addMessage({
        message_options: {
          response_user_profile: _constants__WEBPACK_IMPORTED_MODULE_2__.RESPONSE_USER_PROFILE
        },
        output: {
          generic: [{
            id: workflowId + "_stopped",
            response_type: "text",
            text: `<div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 16px; color: #64748b; text-align: center; margin: 8px 0; display: flex; align-items: center; justify-content: center; gap: 8px;"><div style="font-size: 1.1rem;"></div><div><div style="font-size: 0.9rem; font-weight: 500; margin: 0; color: #475569;">Processing Stopped</div><div style="font-size: 0.75rem; opacity: 0.8; margin: 0; color: #64748b;">You stopped the task</div></div></div>`
          }]
        }
      });
      return fullResponse; // Return partial response
    } else {
      console.error("Fake streaming error:", error);

      // Add error message
      await instance.messaging.addMessage({
        message_options: {
          response_user_profile: _constants__WEBPACK_IMPORTED_MODULE_2__.RESPONSE_USER_PROFILE
        },
        output: {
          generic: [{
            id: workflowId + "_error",
            response_type: "text",
            text: "❌ An error occurred while processing your request."
          }]
        }
      });
      throw error;
    }
  } finally {
    // Always reset streaming state when done
    console.log("Cleaning up fake stream state");
    _StreamManager__WEBPACK_IMPORTED_MODULE_1__.streamStateManager.setStreaming(false);
    _StreamManager__WEBPACK_IMPORTED_MODULE_1__.streamStateManager.setAbortController(null);
  }
};

// Helper function to create abortable delays
function abortableDelay(ms, signal) {
  return new Promise((resolve, reject) => {
    // If already aborted, reject immediately
    if (signal.aborted) {
      reject(new Error("Aborted"));
      return;
    }
    const timeoutId = setTimeout(() => {
      resolve();
    }, ms);

    // Listen for abort signal
    const abortHandler = () => {
      clearTimeout(timeoutId);
      reject(new Error("Aborted"));
    };
    signal.addEventListener("abort", abortHandler, {
      once: true
    });
  });
}

// Enhanced streaming function that integrates workflow component
// Helper function to send messages easily
const addStreamMessage = async (instance, workflowId, stepTitle, data, responseType = "user_defined") => {
  // For the new card system, we don't add individual messages
  // Instead, we let the CardManager handle the steps through the global interface
  if (window.aiSystemInterface && responseType === "user_defined") {
    console.log("Adding step to card manager:", stepTitle, data);
    console.log("aiSystemInterface available:", !!window.aiSystemInterface);
    console.log("addStep function available:", !!window.aiSystemInterface.addStep);
    try {
      window.aiSystemInterface.addStep(stepTitle, data);
      console.log("Step added successfully");
    } catch (error) {
      console.error("Error adding step:", error);
    }
    return;
  } else {
    console.log("Not using card manager - aiSystemInterface:", !!window.aiSystemInterface, "responseType:", responseType);
  }

  // For text messages, still add them normally
  if (responseType === "text") {
    const messageConfig = {
      id: workflowId + stepTitle,
      response_type: "text",
      text: typeof data === "string" ? data : JSON.stringify(data)
    };
    await instance.messaging.addMessage({
      message_options: {
        response_user_profile: _constants__WEBPACK_IMPORTED_MODULE_2__.RESPONSE_USER_PROFILE
      },
      output: {
        generic: [messageConfig]
      }
    });
  }
};
const fetchStreamingData = async (instance, query, action = null, threadId) => {
  // Check if we should use fake streaming
  if (USE_FAKE_STREAM) {
    console.log("Using fake stream simulation");
    return simulateFakeStream(instance, query);
  }
  console.log("🚀 Starting new fetchStreamingData with query:", query.substring(0, 50));

  // Create abort controller for this stream
  const abortController = new AbortController();
  _StreamManager__WEBPACK_IMPORTED_MODULE_1__.streamStateManager.setAbortController(abortController);
  let fullResponse = "";
  let workflowInitialized = false;
  let workflowId = "workflow_" + generateTimestampId();

  // Set streaming state
  _StreamManager__WEBPACK_IMPORTED_MODULE_1__.streamStateManager.setStreaming(true);
  console.log("🎯 Set streaming to true, abort controller set");

  // Add abort listener for debugging
  abortController.signal.addEventListener("abort", () => {
    console.log("🛑 ABORT SIGNAL RECEIVED IN FETCH STREAM!");
  });
  try {
    // Check if already aborted before starting
    if (abortController.signal.aborted) {
      console.log("🛑 Stream aborted before starting");
      return fullResponse;
    }

    // Do not reset the existing UI; we want to preserve prior cards/history

    // Check after reset delay
    if (abortController.signal.aborted) {
      console.log("🛑 Stream aborted after UI reset");
      return fullResponse;
    }

    // First create the workflow component
    console.log("💬 Initializing workflow without adding placeholder chat message");
    workflowInitialized = true;

    // Give a moment for the new CardManager message to mount
    await abortableDelayV2(300, abortController.signal);

    // Check after initialization delay
    if (abortController.signal.aborted) {
      console.log("🛑 Stream aborted after initialization");
      return fullResponse;
    }
    console.log("🌊 Beginning stream connection");

    // Start streaming with abort signal
    await (0,_microsoft_fetch_event_source__WEBPACK_IMPORTED_MODULE_0__.fetchEventSource)(`${_constants__WEBPACK_IMPORTED_MODULE_2__.API_BASE_URL}/stream`, {
      headers: {
        "Content-Type": "application/json",
        ...(threadId ? {
          "X-Thread-ID": threadId
        } : {})
      },
      method: "POST",
      body: query ? JSON.stringify({
        query
      }) : JSON.stringify(action),
      signal: abortController.signal,
      // 🔑 KEY: Pass abort signal to fetchEventSource

      async onopen(response) {
        console.log("🌊 Stream connection opened:", response.status);

        // Check if aborted during connection
        if (abortController.signal.aborted) {
          console.log("🛑 Stream aborted during connection opening");
          return;
        }
        // Intentionally no chat message here to avoid polluting history
      },
      async onmessage(ev) {
        // Check if aborted before processing message
        if (abortController.signal.aborted) {
          console.log("🛑 Stream aborted - skipping message processing");
          return;
        }
        let currentStep = getCurrentStep(ev);
        if (currentStep) {
          let stepTitle = ev.event;
          console.log("⚡ Processing step:", stepTitle);
          await addStreamMessage(instance, workflowId, stepTitle, currentStep, "user_defined");
        }

        // Check if aborted after processing message
        if (abortController.signal.aborted) {
          console.log("🛑 Stream aborted after processing message");
          return;
        }
      },
      async onclose() {
        console.log("🌊 Stream connection closed");
        console.log("🌊 Signal aborted state:", abortController.signal.aborted);
      },
      async onerror(err) {
        console.error("🌊 Stream error:", err);
        console.log("🌊 Error name:", err.name);
        console.log("🌊 Signal aborted:", abortController.signal.aborted);

        // Don't add error message if stream was aborted by user
        if (abortController.signal.aborted) {
          console.log("🛑 Stream error was due to user abort - not adding error message");
          return;
        }

        // Add error step for real errors
        if (workflowInitialized) {
          await addStreamMessage(instance, workflowId, "error", `An error occurred during processing: ${err.message}`, "text");
        }
      }
    });

    // Check if completed successfully or was aborted
    if (abortController.signal.aborted) {
      console.log("🛑 Stream completed due to abort");
    } else {
      console.log("🎉 Stream completed successfully");
    }
    return fullResponse;
  } catch (error) {
    console.log("❌ Caught error in fetchStreamingData:", error);
    console.log("❌ Error name:", error.name);
    console.log("❌ Signal aborted:", abortController.signal.aborted);

    // Handle abort vs real errors
    if (error.name === "AbortError" || error.message === "Aborted" || abortController.signal.aborted) {
      console.log("🛑 Fetch stream was cancelled by user");

      // Add a message indicating the stream was stopped
      if (workflowInitialized) {
        await addStreamMessage(instance, workflowId, "stopped", `<div style="background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); border-radius: 8px; padding: 12px 16px; color: white; text-align: center; box-shadow: 0 4px 12px rgba(245, 158, 11, 0.3); margin: 8px 0; display: flex; align-items: center; justify-content: center; gap: 8px;"><div style="font-size: 1.2rem;">⏹</div><div><div style="font-size: 0.9rem; font-weight: 600; margin: 0;">Processing Stopped</div><div style="font-size: 0.75rem; opacity: 0.9; margin: 0;">Stopped by user</div></div></div>`, "text");
      }
      return fullResponse; // Return partial response
    } else {
      console.error("💥 Real error in fetchStreamingData:", error);

      // Add error step if workflow is initialized
      if (workflowInitialized) {
        await addStreamMessage(instance, workflowId, "error", `❌ An error occurred: ${error.message}`, "text");

        // Signal completion to the system on error
        if (window.aiSystemInterface && window.aiSystemInterface.setProcessingComplete) {
          window.aiSystemInterface.setProcessingComplete(true);
        }
      }
      throw error;
    }
  } finally {
    // Always reset streaming state when done
    console.log("🧹 Cleaning up fetch stream state");
    _StreamManager__WEBPACK_IMPORTED_MODULE_1__.streamStateManager.setStreaming(false);
    _StreamManager__WEBPACK_IMPORTED_MODULE_1__.streamStateManager.setAbortController(null);
    console.log("🧹 Fetch stream cleanup complete");
  }
};

// Enhanced abortable delay function (same as before but with logging)
function abortableDelayV2(ms, signal) {
  console.log(`⏰ Creating abortable delay for ${ms}ms, signal.aborted:`, signal.aborted);
  return new Promise((resolve, reject) => {
    // If already aborted, reject immediately
    if (signal.aborted) {
      console.log("⏰ Delay rejected immediately - already aborted");
      reject(new Error("Aborted"));
      return;
    }
    const timeoutId = setTimeout(() => {
      console.log("⏰ Delay timeout completed normally");
      resolve();
    }, ms);

    // Listen for abort signal
    const abortHandler = () => {
      console.log("⏰ Delay abort handler called - clearing timeout");
      clearTimeout(timeoutId);
      reject(new Error("Aborted"));
    };
    signal.addEventListener("abort", abortHandler, {
      once: true
    });
    console.log("⏰ Abort listener added to delay");
  });
}
const waitForInterfaceReady = async (timeoutMs = 3000, intervalMs = 100) => {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (window.aiSystemInterface && typeof window.aiSystemInterface.addStep === "function") {
      return;
    }
    await new Promise(r => setTimeout(r, intervalMs));
  }
  console.warn("aiSystemInterface not available after", timeoutMs, "ms");
};
const streamViaBackground = async (instance, query) => {
  // Guard against empty query
  if (!query?.trim()) {
    return;
  }

  // -------------------------------------------------------------
  // Replicate the original workflow UI behaviour (same as in
  // fetchStreamingData) so that incoming agent responses are
  // rendered through the side-panel component.
  // -------------------------------------------------------------

  // Preserve previous cards/history; do not force-reset the UI here

  // 2. Insert an initial user_defined message that hosts our Workflow UI
  const workflowId = "workflow_" + generateTimestampId();

  // For the new card system, we don't need to add the initial message here
  // as it's already handled in customSendMessage
  // await instance.messaging.addMessage({
  //   output: {
  //     generic: [
  //       {
  //         id: workflowId,
  //         response_type: "user_defined",
  //         user_defined: {
  //           user_defined_type: "my_unique_identifier",
  //           text: "Processing your request...",
  //         },
  //       } as any,
  //     },
  //   },
  // });

  // Wait until the workflow component has mounted
  await waitForInterfaceReady();

  // Track whether processing has been stopped
  let isStopped = false;
  const responseID = crypto.randomUUID();
  let accumulatedText = "";

  // We no longer push plain chat chunks for each stream segment because
  // the workflow component renders them in its own UI. Keeping chat
  // payloads suppressed avoids duplicate, unformatted messages.
  const pushPartial = _text => {};
  const pushComplete = _text => {};

  // -------------------------------------------------------------
  // Helper : parse the `content` received from the background into
  // an object compatible with the old fetchEventSource `ev` shape.
  // -------------------------------------------------------------
  const parseSSEContent = raw => {
    let eventName = "Message";
    const dataLines = [];
    raw.split(/\r?\n/).forEach(line => {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      } else if (line.trim().length) {
        // If the line isn't prefixed, treat it as data as well
        dataLines.push(line.trim());
      }
    });
    return {
      event: eventName,
      data: dataLines.join("\n")
    };
  };

  // Add initial step indicating that the connection has been established
  if (window.aiSystemInterface) {
    window.aiSystemInterface.addStep("Connection Established", "Processing request and preparing response...");
  }

  // -------------------------------------------------------------
  // Listener for streaming responses coming back from the background
  // -------------------------------------------------------------
  const listener = message => {
    if (!message || message.source !== "background") return;
    switch (message.type) {
      case "agent_response":
        {
          const rawContent = message.content ?? "";

          // Convert the raw content into an SSE-like event structure so we can
          // reuse the original render logic.
          const ev = parseSSEContent(rawContent);

          // Handle workflow-step visualisation
          if (!isStopped && window.aiSystemInterface && !window.aiSystemInterface.isProcessingStopped()) {
            const currentStep = getCurrentStep(ev);
            if (currentStep) {
              const stepTitle = ev.event;
              if (ev.event === "Stopped") {
                // Graceful stop handling
                window.aiSystemInterface.stopProcessing();
                isStopped = true;
              } else if (!window.aiSystemInterface.hasStepWithTitle(stepTitle)) {
                window.aiSystemInterface.addStep(stepTitle, currentStep);
              }
            }
          }

          // No longer sending plain chat messages – only updating workflow UI
          accumulatedText += ev.data;
          break;
        }
      case "agent_complete":
        {
          // Finalise UI state (no plain chat message)

          if (window.aiSystemInterface && !isStopped) {
            window.aiSystemInterface.setProcessingComplete?.(true);
          }
          window.chrome.runtime.onMessage.removeListener(listener);
          break;
        }
      case "agent_error":
        {
          // Report error in workflow UI
          window.aiSystemInterface?.addStep("Error Occurred", `An error occurred during processing: ${message.message}`);
          if (window.aiSystemInterface && !isStopped) {
            window.aiSystemInterface.setProcessingComplete?.(true);
          }
          window.chrome.runtime.onMessage.removeListener(listener);
          break;
        }
      default:
        break;
    }
  };

  // Register the listener *before* dispatching the query so that no
  // early backend messages are missed.
  window.chrome.runtime.onMessage.addListener(listener);

  // -------------------------------------------------------------
  // Now dispatch the query to the background service-worker. We do
  // NOT await the response here because the background script keeps
  // the promise pending until the stream completes, which would block
  // our execution and cause UI updates to stall.
  // -------------------------------------------------------------

  window.chrome.runtime.sendMessage({
    source: "popup",
    type: "send_agent_query",
    query
  }).then(bgResp => {
    if (bgResp?.type === "error") {
      console.error("Background returned error during dispatch", bgResp);
      window.aiSystemInterface?.addStep("Error Occurred", bgResp.message || "Background error");
      window.aiSystemInterface?.setProcessingComplete?.(true);
    }
  }).catch(err => {
    console.error("Failed to dispatch agent_query", err);
    if (window.aiSystemInterface) {
      window.aiSystemInterface.addStep("Error Occurred", `An error occurred: ${err.message || "Failed to dispatch query"}`);
      window.aiSystemInterface.setProcessingComplete?.(true);
    }
  });
};


/***/ }),

/***/ "../agentic_chat/src/SubAgentsConfig.tsx":
/*!***********************************************!*\
  !*** ../agentic_chat/src/SubAgentsConfig.tsx ***!
  \***********************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ SubAgentsConfig; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");
/* harmony import */ var _ConfigModal_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./ConfigModal.css */ "../agentic_chat/src/ConfigModal.css");



function SubAgentsConfig({
  onClose
}) {
  const [config, setConfig] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({
    mode: "supervisor",
    subAgents: [],
    supervisorStrategy: "adaptive",
    availableTools: []
  });
  const [saveStatus, setSaveStatus] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("idle");
  const [expandedAgent, setExpandedAgent] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [availableApps, setAvailableApps] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [appToolsCache, setAppToolsCache] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({});
  const [loadingApps, setLoadingApps] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [showAddAgentModal, setShowAddAgentModal] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [newAgentSource, setNewAgentSource] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("direct");
  const [newAgentUrl, setNewAgentUrl] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("");
  const [newAgentName, setNewAgentName] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("");
  const [newAgentEnvVars, setNewAgentEnvVars] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [newAgentStreamType, setNewAgentStreamType] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("http");
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    loadConfig();
    loadApps();
  }, []);
  const loadConfig = async () => {
    try {
      const response = await fetch('/api/config/subagents');
      if (response.ok) {
        const data = await response.json();
        const updatedData = {
          ...data,
          subAgents: data.subAgents.map(agent => ({
            ...agent,
            assignedApps: agent.assignedApps || [],
            source: agent.source || {
              type: "direct"
            }
          }))
        };
        setConfig(updatedData);
      }
    } catch (error) {
      console.error("Error loading config:", error);
    }
  };
  const loadApps = async () => {
    setLoadingApps(true);
    try {
      const response = await fetch('/api/apps');
      if (response.ok) {
        const data = await response.json();
        setAvailableApps(data.apps || []);
      }
    } catch (error) {
      console.error("Error loading apps:", error);
    } finally {
      setLoadingApps(false);
    }
  };
  const loadAppTools = async appName => {
    if (appToolsCache[appName]) {
      return appToolsCache[appName];
    }
    try {
      const response = await fetch(`/api/apps/${encodeURIComponent(appName)}/tools`);
      if (response.ok) {
        const data = await response.json();
        const tools = data.tools || [];
        setAppToolsCache(prev => ({
          ...prev,
          [appName]: tools
        }));
        return tools;
      }
    } catch (error) {
      console.error(`Error loading tools for app ${appName}:`, error);
    }
    return [];
  };
  const saveConfig = async () => {
    setSaveStatus("saving");
    try {
      const response = await fetch('/api/config/subagents', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(config)
      });
      if (response.ok) {
        setSaveStatus("success");
        setTimeout(() => setSaveStatus("idle"), 2000);
      } else {
        setSaveStatus("error");
        setTimeout(() => setSaveStatus("idle"), 2000);
      }
    } catch (error) {
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 2000);
    }
  };
  const openAddAgentModal = () => {
    setNewAgentSource("direct");
    setNewAgentUrl("");
    setNewAgentName("");
    setNewAgentEnvVars([]);
    setNewAgentStreamType("http");
    setShowAddAgentModal(true);
  };
  const closeAddAgentModal = () => {
    setShowAddAgentModal(false);
  };
  const addEnvVar = () => {
    setNewAgentEnvVars([...newAgentEnvVars, {
      key: "",
      value: ""
    }]);
  };
  const updateEnvVar = (index, key, value) => {
    const newEnvVars = [...newAgentEnvVars];
    newEnvVars[index] = {
      key,
      value
    };
    setNewAgentEnvVars(newEnvVars);
  };
  const removeEnvVar = index => {
    setNewAgentEnvVars(newAgentEnvVars.filter((_, i) => i !== index));
  };
  const createAgent = () => {
    const sourceConfig = {
      type: newAgentSource
    };
    if (newAgentSource === "a2a" || newAgentSource === "mcp") {
      if (newAgentSource === "a2a") {
        sourceConfig.url = newAgentUrl;
        sourceConfig.name = newAgentName;
      } else {
        sourceConfig.url = newAgentUrl;
        sourceConfig.streamType = newAgentStreamType;
      }
      const envVarsObj = {};
      newAgentEnvVars.forEach(env => {
        if (env.key.trim()) {
          envVarsObj[env.key.trim()] = env.value;
        }
      });
      if (Object.keys(envVarsObj).length > 0) {
        sourceConfig.envVars = envVarsObj;
      }
    }
    const newAgent = {
      id: Date.now().toString(),
      name: newAgentSource === "a2a" && newAgentName ? newAgentName : "New Agent",
      role: "Assistant",
      description: "",
      enabled: true,
      capabilities: [],
      tools: config.availableTools.map(tool => ({
        name: tool,
        enabled: false
      })),
      assignedApps: [],
      policies: [],
      source: sourceConfig
    };
    setConfig({
      ...config,
      subAgents: [...config.subAgents, newAgent]
    });
    closeAddAgentModal();
  };
  const assignApp = async (agentId, appName) => {
    const agent = config.subAgents.find(a => a.id === agentId);
    if (!agent) return;
    if (agent.assignedApps.some(a => a.appName === appName)) {
      return;
    }
    const tools = await loadAppTools(appName);
    const newAssignedApp = {
      appName,
      tools: tools.map(t => ({
        name: t.name,
        enabled: true
      }))
    };
    updateAgent(agentId, {
      assignedApps: [...agent.assignedApps, newAssignedApp]
    });
  };
  const unassignApp = (agentId, appName) => {
    const agent = config.subAgents.find(a => a.id === agentId);
    if (agent) {
      updateAgent(agentId, {
        assignedApps: agent.assignedApps.filter(a => a.appName !== appName)
      });
    }
  };
  const toggleAppTool = (agentId, appName, toolName) => {
    const agent = config.subAgents.find(a => a.id === agentId);
    if (agent) {
      const newAssignedApps = agent.assignedApps.map(app => app.appName === appName ? {
        ...app,
        tools: app.tools.map(t => t.name === toolName ? {
          ...t,
          enabled: !t.enabled
        } : t)
      } : app);
      updateAgent(agentId, {
        assignedApps: newAssignedApps
      });
    }
  };
  const addPolicy = agentId => {
    const agent = config.subAgents.find(a => a.id === agentId);
    if (agent) {
      updateAgent(agentId, {
        policies: [...agent.policies, ""]
      });
    }
  };
  const updatePolicy = (agentId, index, value) => {
    const agent = config.subAgents.find(a => a.id === agentId);
    if (agent) {
      const newPolicies = [...agent.policies];
      newPolicies[index] = value;
      updateAgent(agentId, {
        policies: newPolicies
      });
    }
  };
  const removePolicy = (agentId, index) => {
    const agent = config.subAgents.find(a => a.id === agentId);
    if (agent) {
      const newPolicies = agent.policies.filter((_, i) => i !== index);
      updateAgent(agentId, {
        policies: newPolicies
      });
    }
  };
  const toggleTool = (agentId, toolName) => {
    const agent = config.subAgents.find(a => a.id === agentId);
    if (agent) {
      const newTools = agent.tools.map(t => t.name === toolName ? {
        ...t,
        enabled: !t.enabled
      } : t);
      updateAgent(agentId, {
        tools: newTools
      });
    }
  };
  const updateAgent = (id, updates) => {
    setConfig({
      ...config,
      subAgents: config.subAgents.map(agent => agent.id === id ? {
        ...agent,
        ...updates
      } : agent)
    });
  };
  const removeAgent = id => {
    setConfig({
      ...config,
      subAgents: config.subAgents.filter(agent => agent.id !== id)
    });
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-overlay",
    onClick: onClose
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal",
    onClick: e => e.stopPropagation()
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h2", null, "Sub-Agents Configuration"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "config-modal-close",
    onClick: onClose
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
    size: 20
  }))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-content"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Agent Mode Settings"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-form"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Execution Mode"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
    value: config.mode,
    onChange: e => setConfig({
      ...config,
      mode: e.target.value
    })
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "supervisor"
  }, "Supervisor (Multi-Agent)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "single"
  }, "Single Agent")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Supervisor mode delegates tasks to specialized sub-agents")), config.mode === "supervisor" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Supervisor Strategy"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
    value: config.supervisorStrategy,
    onChange: e => setConfig({
      ...config,
      supervisorStrategy: e.target.value
    })
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "sequential"
  }, "Sequential"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "parallel"
  }, "Parallel"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "adaptive"
  }, "Adaptive")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "How the supervisor coordinates sub-agents")))), config.mode === "supervisor" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "section-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Sub-Agents"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "add-btn",
    onClick: openAddAgentModal
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Plus, {
    size: 16
  }), "Add Agent")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "sources-list"
  }, config.subAgents.map(agent => {
    const isExpanded = expandedAgent === agent.id;
    const enabledTools = agent.tools.filter(t => t.enabled).length;
    const totalAppTools = agent.assignedApps.reduce((sum, app) => sum + app.tools.filter(t => t.enabled).length, 0);
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      key: agent.id,
      className: "agent-config-card"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "agent-config-header"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "agent-config-top"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
      type: "checkbox",
      checked: agent.enabled,
      onChange: e => updateAgent(agent.id, {
        enabled: e.target.checked
      })
    }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
      type: "text",
      value: agent.name,
      onChange: e => updateAgent(agent.id, {
        name: e.target.value
      }),
      className: "agent-config-name",
      placeholder: "Agent Name"
    }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
      type: "text",
      value: agent.role,
      onChange: e => updateAgent(agent.id, {
        role: e.target.value
      }),
      placeholder: "Role",
      style: {
        width: "120px"
      }
    }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
      className: "expand-btn",
      onClick: () => setExpandedAgent(isExpanded ? null : agent.id)
    }, isExpanded ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronUp, {
      size: 16
    }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronDown, {
      size: 16
    })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
      className: "delete-btn",
      onClick: () => removeAgent(agent.id)
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Trash2, {
      size: 16
    }))), !isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "agent-summary"
    }, agent.source && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      className: "agent-summary-item",
      title: `Source: ${agent.source.type.toUpperCase()}${agent.source.url ? ` - ${agent.source.url}` : ''}`
    }, agent.source.type === "direct" ? "Direct" : agent.source.type === "a2a" ? "A2A" : "MCP"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      className: "agent-summary-item"
    }, agent.assignedApps.length, " app", agent.assignedApps.length !== 1 ? 's' : ''), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      className: "agent-summary-item"
    }, totalAppTools + enabledTools, " tool", totalAppTools + enabledTools !== 1 ? 's' : ''), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      className: "agent-summary-item"
    }, agent.policies.length, " polic", agent.policies.length !== 1 ? 'ies' : 'y'))), isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "agent-config-details"
    }, agent.source && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "form-group"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Source Configuration"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "source-info-card"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "source-info-row"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", null, "Type:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, agent.source.type === "direct" ? "Direct" : agent.source.type === "a2a" ? "A2A Protocol" : "MCP Server")), agent.source.url && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "source-info-row"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", null, "URL:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, agent.source.url)), agent.source.name && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "source-info-row"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", null, "Name:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, agent.source.name)), agent.source.streamType && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "source-info-row"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", null, "Stream Type:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, agent.source.streamType.toUpperCase())), agent.source.envVars && Object.keys(agent.source.envVars).length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "source-info-row"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", null, "Environment Variables:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "env-vars-display"
    }, Object.entries(agent.source.envVars).map(([key, value]) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      key: key,
      className: "env-var-display-item"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, key), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "="), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, value))))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "form-group"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Description"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("textarea", {
      value: agent.description,
      onChange: e => updateAgent(agent.id, {
        description: e.target.value
      }),
      placeholder: "What this agent does...",
      rows: 2
    })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "form-group"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Capabilities"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
      type: "text",
      value: agent.capabilities.join(", "),
      onChange: e => updateAgent(agent.id, {
        capabilities: e.target.value.split(",").map(c => c.trim()).filter(c => c)
      }),
      placeholder: "research, code, planning, analysis"
    }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Comma-separated list of capabilities")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "form-group"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "form-group-header"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Assigned Apps"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
      value: "",
      onChange: e => {
        if (e.target.value) {
          assignApp(agent.id, e.target.value);
          e.target.value = "";
        }
      },
      style: {
        width: "200px",
        marginLeft: "auto"
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
      value: ""
    }, "Select an app to assign..."), availableApps.filter(app => !agent.assignedApps.some(a => a.appName === app.name)).map(app => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
      key: app.name,
      value: app.name
    }, app.name)))), agent.assignedApps.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "policies-empty"
    }, "No apps assigned. Select an app from the dropdown above.") : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "apps-list"
    }, agent.assignedApps.map(assignedApp => {
      const app = availableApps.find(a => a.name === assignedApp.appName);
      const enabledCount = assignedApp.tools.filter(t => t.enabled).length;
      return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        key: assignedApp.appName,
        className: "app-config-section"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "app-config-header"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", null, assignedApp.appName), app?.description && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", {
        style: {
          display: "block",
          color: "#666",
          marginTop: "4px"
        }
      }, app.description)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
        className: "remove-btn",
        onClick: () => unassignApp(agent.id, assignedApp.appName),
        title: "Remove app"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
        size: 14
      }))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "app-tools-section"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group-header",
        style: {
          marginTop: "8px",
          marginBottom: "8px"
        }
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
        style: {
          fontSize: "0.9em",
          margin: 0
        }
      }, "Tools (", enabledCount, "/", assignedApp.tools.length, " enabled)")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "tools-grid"
      }, assignedApp.tools.map(tool => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
        key: tool.name,
        className: "tool-checkbox-label"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "checkbox",
        checked: tool.enabled,
        onChange: () => toggleAppTool(agent.id, assignedApp.appName, tool.name)
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, tool.name))))));
    })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Assign apps and configure which tools from each app this agent can use")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "form-group"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "form-group-header"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Legacy Tools"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      className: "tools-count-small"
    }, enabledTools, "/", agent.tools.length, " enabled")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "tools-grid"
    }, agent.tools.map(tool => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
      key: tool.name,
      className: "tool-checkbox-label"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
      type: "checkbox",
      checked: tool.enabled,
      onChange: () => toggleTool(agent.id, tool.name)
    }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, tool.name)))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Legacy tool configuration (deprecated - use apps above)")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "form-group"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "form-group-header"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Policies (Natural Language)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
      className: "add-small-btn",
      onClick: () => addPolicy(agent.id)
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Plus, {
      size: 12
    }), "Add Policy")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "policies-list"
    }, agent.policies.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "policies-empty"
    }, "No policies defined. Add policies to control agent behavior.") : agent.policies.map((policy, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      key: index,
      className: "policy-item"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("textarea", {
      value: policy,
      onChange: e => updatePolicy(agent.id, index, e.target.value),
      placeholder: "e.g., Always verify information from multiple sources before making decisions",
      rows: 2
    }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
      className: "remove-btn",
      onClick: () => removePolicy(agent.id, index)
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
      size: 14
    }))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Define behavior rules in plain English"))));
  })), config.subAgents.length === 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "empty-state"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "No sub-agents configured. Click \"Add Agent\" to create one.")))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-footer"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "cancel-btn",
    onClick: onClose
  }, "Cancel"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `save-btn ${saveStatus}`,
    onClick: saveConfig,
    disabled: saveStatus === "saving"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Save, {
    size: 16
  }), saveStatus === "idle" && "Save Changes", saveStatus === "saving" && "Saving...", saveStatus === "success" && "Saved!", saveStatus === "error" && "Error!"))), showAddAgentModal && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-overlay",
    onClick: closeAddAgentModal
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal add-agent-modal",
    onClick: e => e.stopPropagation()
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h2", null, "Add New Sub-Agent"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "config-modal-close",
    onClick: closeAddAgentModal
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
    size: 20
  }))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-content"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Agent Source"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-form"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "How to create this agent?"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
    value: newAgentSource,
    onChange: e => setNewAgentSource(e.target.value)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "direct"
  }, "Direct (Local Agent)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "a2a"
  }, "A2A Protocol"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "mcp"
  }, "MCP Server")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, newAgentSource === "direct" && "Create a local agent directly", newAgentSource === "a2a" && "Connect via A2A protocol", newAgentSource === "mcp" && "Connect to an MCP server via HTTP or SSE")), newAgentSource === "a2a" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Agent Name"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: newAgentName,
    onChange: e => setNewAgentName(e.target.value),
    placeholder: "e.g., research-agent"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Name identifier for the A2A agent")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "URL"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: newAgentUrl,
    onChange: e => setNewAgentUrl(e.target.value),
    placeholder: "e.g., http://localhost:8080"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "A2A protocol endpoint URL"))), newAgentSource === "mcp" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "MCP Server URL"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: newAgentUrl,
    onChange: e => setNewAgentUrl(e.target.value),
    placeholder: "e.g., http://localhost:8001"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "MCP server endpoint URL")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Stream Type"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
    value: newAgentStreamType,
    onChange: e => setNewAgentStreamType(e.target.value)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "http"
  }, "HTTP (Streamable)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "sse"
  }, "SSE (Server-Sent Events)")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Communication protocol for MCP server"))), (newAgentSource === "a2a" || newAgentSource === "mcp") && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Environment Variables"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "add-small-btn",
    onClick: addEnvVar
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Plus, {
    size: 12
  }), "Add Variable")), newAgentEnvVars.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policies-empty"
  }, "No environment variables. Click \"Add Variable\" to add one.") : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "env-list"
  }, newAgentEnvVars.map((env, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    className: "env-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: env.key,
    onChange: e => updateEnvVar(index, e.target.value, env.value),
    placeholder: "Variable name",
    style: {
      width: "200px"
    }
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "="), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: env.value,
    onChange: e => updateEnvVar(index, env.key, e.target.value),
    placeholder: "Variable value",
    style: {
      flex: 1
    }
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "remove-btn",
    onClick: () => removeEnvVar(index)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
    size: 14
  }))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Environment variables to pass to the agent"))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-footer"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "cancel-btn",
    onClick: closeAddAgentModal
  }, "Cancel"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "save-btn",
    onClick: createAgent,
    disabled: newAgentSource === "a2a" && (!newAgentUrl || !newAgentName) || newAgentSource === "mcp" && !newAgentUrl
  }, "Create Agent")))));
}

/***/ }),

/***/ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/StatusBar.css":
/*!************************************************************************************************************************************!*\
  !*** ../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/StatusBar.css ***!
  \************************************************************************************************************************************/
/***/ (function(module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__);
// Imports


var ___CSS_LOADER_EXPORT___ = _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default()((_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default()));
// Module
___CSS_LOADER_EXPORT___.push([module.id, ".status-bar {\n  position: fixed;\n  bottom: 0;\n  left: 0;\n  right: 0;\n  height: 42px;\n  background: #f9fafb;\n  border-top: 1px solid #e5e7eb;\n  display: flex;\n  align-items: center;\n  justify-content: space-between;\n  padding: 0 20px;\n  z-index: 900;\n  font-size: 13px;\n  color: #64748b;\n}\n\n.status-bar-left {\n  flex: 1;\n  display: flex;\n  align-items: center;\n}\n\n.status-bar-center {\n  display: flex;\n  align-items: center;\n  gap: 16px;\n  justify-content: center;\n}\n\n.status-bar-right {\n  flex: 1;\n  display: flex;\n  align-items: center;\n  justify-content: flex-end;\n}\n\n.status-item {\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  position: relative;\n}\n\n.status-label {\n  font-weight: 500;\n  color: #475569;\n}\n\n.status-badge {\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n  font-size: 10px;\n  font-weight: 600;\n  padding: 2px 6px;\n  border-radius: 10px;\n  min-width: 18px;\n  text-align: center;\n}\n\n.status-warning {\n  color: #f59e0b;\n  animation: pulse 2s ease-in-out infinite;\n}\n\n@keyframes pulse {\n  0%, 100% {\n    opacity: 1;\n  }\n  50% {\n    opacity: 0.5;\n  }\n}\n\n.status-tools {\n  cursor: pointer;\n  padding: 4px 8px;\n  border-radius: 6px;\n  transition: background 0.2s;\n}\n\n.status-tools:hover {\n  background: #f1f5f9;\n}\n\n.tools-popup {\n  position: absolute;\n  bottom: calc(100% + 8px);\n  left: 50%;\n  transform: translateX(-50%);\n  width: 280px;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);\n  z-index: 1000;\n  animation: slideUp 0.2s ease;\n}\n\n@keyframes slideUp {\n  from {\n    opacity: 0;\n    transform: translateY(8px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n.tools-popup-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 12px 14px;\n  border-bottom: 1px solid #e5e7eb;\n  background: #f9fafb;\n  border-radius: 8px 8px 0 0;\n  font-weight: 600;\n  font-size: 12px;\n  color: #1e293b;\n}\n\n.tools-count {\n  font-size: 11px;\n  color: #64748b;\n  background: white;\n  padding: 2px 6px;\n  border-radius: 4px;\n  border: 1px solid #e5e7eb;\n}\n\n.tools-list {\n  max-height: 240px;\n  overflow-y: auto;\n  padding: 8px;\n}\n\n.tools-empty {\n  padding: 20px;\n  text-align: center;\n  color: #94a3b8;\n  font-size: 12px;\n}\n\n.tool-item {\n  display: flex;\n  align-items: center;\n  gap: 10px;\n  padding: 8px 10px;\n  border-radius: 6px;\n  margin-bottom: 4px;\n  transition: background 0.2s;\n}\n\n.tool-item:hover {\n  background: #f8fafc;\n}\n\n.tool-item.connected {\n  border-left: 2px solid #10b981;\n}\n\n.tool-item.error {\n  border-left: 2px solid #ef4444;\n}\n\n.tool-item.disconnected {\n  border-left: 2px solid #94a3b8;\n  opacity: 0.6;\n}\n\n.tool-status-indicator {\n  width: 6px;\n  height: 6px;\n  border-radius: 50%;\n  flex-shrink: 0;\n}\n\n.tool-item.connected .tool-status-indicator {\n  background: #10b981;\n  box-shadow: 0 0 6px rgba(16, 185, 129, 0.5);\n}\n\n.tool-item.error .tool-status-indicator {\n  background: #ef4444;\n  box-shadow: 0 0 6px rgba(239, 68, 68, 0.5);\n}\n\n.tool-item.disconnected .tool-status-indicator {\n  background: #94a3b8;\n}\n\n.tool-info {\n  flex: 1;\n  display: flex;\n  flex-direction: column;\n  gap: 2px;\n  min-width: 0;\n}\n\n.tool-name {\n  font-size: 12px;\n  font-weight: 600;\n  color: #1e293b;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.tool-type {\n  font-size: 10px;\n  color: #94a3b8;\n  text-transform: uppercase;\n  letter-spacing: 0.5px;\n}\n\n.tool-status-text {\n  font-size: 10px;\n  color: #64748b;\n  text-transform: capitalize;\n}\n\n.status-mode {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n}\n\n.mode-toggle {\n  display: flex;\n  align-items: center;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 6px;\n  padding: 2px;\n  cursor: pointer;\n  transition: border-color 0.2s;\n}\n\n.mode-toggle:hover {\n  border-color: #cbd5e1;\n}\n\n.mode-toggle.disabled {\n  cursor: not-allowed;\n  opacity: 0.7;\n}\n\n.mode-toggle.disabled:hover {\n  border-color: #e5e7eb;\n}\n\n.mode-option {\n  padding: 3px 10px;\n  border-radius: 4px;\n  font-size: 11px;\n  font-weight: 500;\n  color: #64748b;\n  transition: all 0.2s;\n  user-select: none;\n}\n\n.mode-option.active {\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n  box-shadow: 0 2px 4px rgba(102, 126, 234, 0.3);\n}\n\n.mode-option.disabled {\n  opacity: 0.4;\n  cursor: not-allowed;\n}\n\n\n.status-connected {\n  color: #10b981;\n}\n\n.tools-list::-webkit-scrollbar {\n  width: 4px;\n}\n\n.tools-list::-webkit-scrollbar-track {\n  background: transparent;\n}\n\n.tools-list::-webkit-scrollbar-thumb {\n  background: #cbd5e1;\n  border-radius: 2px;\n}\n\n.tools-list::-webkit-scrollbar-thumb:hover {\n  background: #94a3b8;\n}\n\n/* Agent Mode Styles */\n.status-agents {\n  cursor: pointer;\n  padding: 4px 8px;\n  border-radius: 6px;\n  transition: background 0.2s;\n  position: relative;\n}\n\n.status-agents:hover {\n  background: #f1f5f9;\n}\n\n.agents-popup {\n  position: absolute;\n  bottom: calc(100% + 8px);\n  left: 50%;\n  transform: translateX(-50%);\n  width: 280px;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);\n  z-index: 1000;\n  animation: slideUp 0.2s ease;\n}\n\n.agents-popup-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 12px 14px;\n  border-bottom: 1px solid #e5e7eb;\n  background: #f9fafb;\n  border-radius: 8px 8px 0 0;\n  font-weight: 600;\n  font-size: 12px;\n  color: #1e293b;\n}\n\n.agents-count {\n  font-size: 11px;\n  color: #64748b;\n  background: white;\n  padding: 2px 6px;\n  border-radius: 4px;\n  border: 1px solid #e5e7eb;\n}\n\n.agents-list {\n  max-height: 240px;\n  overflow-y: auto;\n  padding: 8px;\n}\n\n.agents-empty {\n  padding: 20px;\n  text-align: center;\n  color: #94a3b8;\n  font-size: 12px;\n}\n\n.agent-item {\n  display: flex;\n  align-items: center;\n  gap: 10px;\n  padding: 8px 10px;\n  border-radius: 6px;\n  margin-bottom: 4px;\n  transition: background 0.2s;\n}\n\n.agent-item:hover {\n  background: #f8fafc;\n}\n\n.agent-item.enabled {\n  border-left: 2px solid #667eea;\n}\n\n.agent-item.disabled {\n  border-left: 2px solid #94a3b8;\n  opacity: 0.6;\n}\n\n.agent-status-indicator {\n  width: 6px;\n  height: 6px;\n  border-radius: 50%;\n  flex-shrink: 0;\n}\n\n.agent-item.enabled .agent-status-indicator {\n  background: #667eea;\n  box-shadow: 0 0 6px rgba(102, 126, 234, 0.5);\n}\n\n.agent-item.disabled .agent-status-indicator {\n  background: #94a3b8;\n}\n\n.agent-info {\n  flex: 1;\n  display: flex;\n  flex-direction: column;\n  gap: 2px;\n  min-width: 0;\n}\n\n.agent-name {\n  font-size: 12px;\n  font-weight: 600;\n  color: #1e293b;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.agent-role {\n  font-size: 10px;\n  color: #94a3b8;\n  text-transform: capitalize;\n}\n\n.agent-status-text {\n  font-size: 10px;\n  color: #64748b;\n  text-transform: capitalize;\n}\n\n.agents-list::-webkit-scrollbar {\n  width: 4px;\n}\n\n.agents-list::-webkit-scrollbar-track {\n  background: transparent;\n}\n\n.agents-list::-webkit-scrollbar-thumb {\n  background: #cbd5e1;\n  border-radius: 2px;\n}\n\n.agents-list::-webkit-scrollbar-thumb:hover {\n  background: #94a3b8;\n}\n\n.agents-info-box {\n  padding: 12px 14px;\n  background: #f8fafc;\n  border-radius: 6px;\n  margin-bottom: 8px;\n}\n\n.agents-info-box.single-mode {\n  display: flex;\n  flex-direction: column;\n  align-items: center;\n  gap: 8px;\n  padding: 24px 14px;\n  text-align: center;\n}\n\n.agents-info-label {\n  font-size: 11px;\n  font-weight: 600;\n  color: #64748b;\n  text-transform: uppercase;\n  letter-spacing: 0.5px;\n}\n\n.single-agent-icon {\n  color: #667eea;\n  margin-bottom: 4px;\n}\n\n.single-agent-label {\n  font-size: 13px;\n  font-weight: 600;\n  color: #1e293b;\n}\n\n.single-agent-description {\n  font-size: 11px;\n  color: #64748b;\n  line-height: 1.5;\n  max-width: 240px;\n}\n\n/* More Menu Styles */\n.status-more {\n  cursor: pointer;\n  padding: 4px 8px;\n  border-radius: 6px;\n  transition: background 0.2s;\n  position: relative;\n}\n\n.status-more:hover {\n  background: #f1f5f9;\n}\n\n.more-popup {\n  position: absolute;\n  bottom: calc(100% + 8px);\n  left: 50%;\n  transform: translateX(-50%);\n  width: 200px;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);\n  z-index: 1000;\n  animation: slideUp 0.2s ease;\n}\n\n.more-popup-header {\n  display: flex;\n  justify-content: center;\n  align-items: center;\n  padding: 12px 14px;\n  border-bottom: 1px solid #e5e7eb;\n  background: #f9fafb;\n  border-radius: 8px 8px 0 0;\n  font-weight: 600;\n  font-size: 12px;\n  color: #1e293b;\n}\n\n.more-list {\n  padding: 4px 0;\n}\n\n.more-item {\n  display: flex;\n  align-items: center;\n  gap: 10px;\n  padding: 10px 14px;\n  cursor: pointer;\n  transition: background 0.2s;\n  font-size: 12px;\n}\n\n.more-item:hover {\n  background: #f8fafc;\n}\n\n.more-item-label {\n  color: #475569;\n  font-weight: 500;\n}\n\n/* Responsive Design */\n@media (max-width: 768px) {\n  .status-bar {\n    padding: 0 12px;\n    height: 40px;\n    font-size: 12px;\n  }\n\n  .status-bar-center {\n    gap: 8px;\n  }\n\n  .status-item {\n    gap: 4px;\n  }\n\n  .status-label {\n    display: none;\n  }\n\n  .status-badge {\n    font-size: 9px;\n    padding: 1px 4px;\n    min-width: 16px;\n  }\n\n  .mode-option {\n    padding: 2px 8px;\n    font-size: 10px;\n  }\n}\n\n@media (max-width: 480px) {\n  .status-bar {\n    padding: 0 8px;\n  }\n\n  .status-bar-center {\n    gap: 4px;\n  }\n\n  .tools-popup,\n  .agents-popup,\n  .more-popup {\n    width: 180px;\n    max-height: 200px;\n  }\n\n  .tool-item,\n  .agent-item,\n  .more-item {\n    padding: 6px 8px;\n    font-size: 11px;\n  }\n}\n\n/* Tool grouping styles */\n.tool-group {\n  margin-bottom: 12px;\n}\n\n.tool-group:last-child {\n  margin-bottom: 0;\n}\n\n.tool-group-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 8px 12px;\n  background: #f8fafc;\n  border-radius: 6px;\n  margin-bottom: 4px;\n  border: 1px solid #e5e7eb;\n}\n\n.tool-group-name {\n  font-size: 12px;\n  font-weight: 600;\n  color: #374151;\n  text-transform: capitalize;\n}\n\n.tool-group-stats {\n  display: flex;\n  flex-direction: column;\n  align-items: flex-end;\n  gap: 2px;\n}\n\n.tool-group-count {\n  font-size: 10px;\n  color: #6b7280;\n  background: #e5e7eb;\n  padding: 2px 6px;\n  border-radius: 8px;\n  font-weight: 500;\n}\n\n.tool-group-internal {\n  font-size: 9px;\n  color: #9ca3af;\n  font-weight: 500;\n}\n\n.tool-group-items {\n  margin-left: 8px;\n}\n\n.tool-group-items .tool-item {\n  padding-left: 20px;\n  border-left: 2px solid #e5e7eb;\n  margin-bottom: 2px;\n}\n\n.tool-group-items .tool-item:last-child {\n  margin-bottom: 0;\n}\n\n/* Examples popup styles */\n.status-examples {\n  cursor: pointer;\n  transition: all 0.2s ease;\n}\n\n.status-examples:hover {\n  background: rgba(251, 191, 36, 0.1);\n}\n\n.status-examples:hover .status-label {\n  color: #f59e0b;\n}\n\n.status-examples:hover .status-badge {\n  background: linear-gradient(135deg, #fbbf24 0%, #f59e0b 100%);\n  color: white;\n}\n\n/* Animated lightbulb when input is empty */\n.lightbulb-glow {\n  animation: lightbulbGlow 2s ease-in-out infinite;\n  color: #8b5cf6;\n}\n\n@keyframes lightbulbGlow {\n  0%, 100% {\n    color: #8b5cf6;\n    filter: drop-shadow(0 0 2px rgba(139, 92, 246, 0.4));\n    transform: scale(1);\n  }\n  50% {\n    color: #a78bfa;\n    filter: drop-shadow(0 0 4px rgba(167, 139, 250, 0.6));\n    transform: scale(1.1);\n  }\n}\n\n/* Animate the entire button when input is empty */\n.status-examples.animate-prompt {\n  animation: pulsePrompt 2s ease-in-out infinite;\n  border-radius: 6px;\n}\n\n@keyframes pulsePrompt {\n  0%, 100% {\n    transform: scale(1);\n    box-shadow: 0 0 0 0 rgba(139, 92, 246, 0);\n    background: transparent;\n  }\n  50% {\n    transform: scale(1.02);\n    box-shadow: 0 0 8px rgba(139, 92, 246, 0.15);\n    background: rgba(139, 92, 246, 0.05);\n  }\n}\n\n/* Make the label slightly more prominent when animating */\n.status-examples.animate-prompt .status-label {\n  animation: labelPulse 2s ease-in-out infinite;\n}\n\n@keyframes labelPulse {\n  0%, 100% {\n    color: #475569;\n  }\n  50% {\n    color: #8b5cf6;\n  }\n}\n\n.examples-popup {\n  position: absolute;\n  bottom: calc(100% + 8px);\n  left: 0;\n  min-width: 450px;\n  max-width: 600px;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 12px;\n  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.1);\n  padding: 0;\n  z-index: 1000;\n  animation: slideUpFadeIn 0.2s ease;\n}\n\n.examples-popup-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 14px 16px;\n  border-bottom: 1px solid #e5e7eb;\n  background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%);\n  border-radius: 12px 12px 0 0;\n}\n\n.examples-popup-header span:first-child {\n  font-weight: 600;\n  font-size: 13px;\n  color: #92400e;\n}\n\n.examples-count {\n  font-size: 11px;\n  padding: 3px 8px;\n  background: rgba(146, 64, 14, 0.1);\n  border-radius: 12px;\n  color: #92400e;\n  font-weight: 600;\n}\n\n.examples-list {\n  padding: 8px;\n  max-height: 400px;\n  overflow-y: auto;\n}\n\n.example-item {\n  display: flex;\n  align-items: flex-start;\n  gap: 10px;\n  padding: 12px;\n  border-radius: 8px;\n  cursor: pointer;\n  transition: all 0.2s ease;\n  margin-bottom: 4px;\n  border: 1px solid transparent;\n}\n\n.example-item:hover {\n  background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);\n  border-color: #fbbf24;\n  transform: translateX(4px);\n}\n\n.example-icon {\n  flex-shrink: 0;\n  margin-top: 2px;\n  color: #f59e0b;\n}\n\n.example-item:hover .example-icon {\n  color: #d97706;\n}\n\n.example-text {\n  font-size: 13px;\n  color: #475569;\n  line-height: 1.5;\n  font-weight: 500;\n}\n\n.example-item:hover .example-text {\n  color: #1e293b;\n}\n\n", "",{"version":3,"sources":["webpack://./../agentic_chat/src/StatusBar.css"],"names":[],"mappings":"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","sourcesContent":[".status-bar {\n  position: fixed;\n  bottom: 0;\n  left: 0;\n  right: 0;\n  height: 42px;\n  background: #f9fafb;\n  border-top: 1px solid #e5e7eb;\n  display: flex;\n  align-items: center;\n  justify-content: space-between;\n  padding: 0 20px;\n  z-index: 900;\n  font-size: 13px;\n  color: #64748b;\n}\n\n.status-bar-left {\n  flex: 1;\n  display: flex;\n  align-items: center;\n}\n\n.status-bar-center {\n  display: flex;\n  align-items: center;\n  gap: 16px;\n  justify-content: center;\n}\n\n.status-bar-right {\n  flex: 1;\n  display: flex;\n  align-items: center;\n  justify-content: flex-end;\n}\n\n.status-item {\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  position: relative;\n}\n\n.status-label {\n  font-weight: 500;\n  color: #475569;\n}\n\n.status-badge {\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n  font-size: 10px;\n  font-weight: 600;\n  padding: 2px 6px;\n  border-radius: 10px;\n  min-width: 18px;\n  text-align: center;\n}\n\n.status-warning {\n  color: #f59e0b;\n  animation: pulse 2s ease-in-out infinite;\n}\n\n@keyframes pulse {\n  0%, 100% {\n    opacity: 1;\n  }\n  50% {\n    opacity: 0.5;\n  }\n}\n\n.status-tools {\n  cursor: pointer;\n  padding: 4px 8px;\n  border-radius: 6px;\n  transition: background 0.2s;\n}\n\n.status-tools:hover {\n  background: #f1f5f9;\n}\n\n.tools-popup {\n  position: absolute;\n  bottom: calc(100% + 8px);\n  left: 50%;\n  transform: translateX(-50%);\n  width: 280px;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);\n  z-index: 1000;\n  animation: slideUp 0.2s ease;\n}\n\n@keyframes slideUp {\n  from {\n    opacity: 0;\n    transform: translateY(8px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n.tools-popup-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 12px 14px;\n  border-bottom: 1px solid #e5e7eb;\n  background: #f9fafb;\n  border-radius: 8px 8px 0 0;\n  font-weight: 600;\n  font-size: 12px;\n  color: #1e293b;\n}\n\n.tools-count {\n  font-size: 11px;\n  color: #64748b;\n  background: white;\n  padding: 2px 6px;\n  border-radius: 4px;\n  border: 1px solid #e5e7eb;\n}\n\n.tools-list {\n  max-height: 240px;\n  overflow-y: auto;\n  padding: 8px;\n}\n\n.tools-empty {\n  padding: 20px;\n  text-align: center;\n  color: #94a3b8;\n  font-size: 12px;\n}\n\n.tool-item {\n  display: flex;\n  align-items: center;\n  gap: 10px;\n  padding: 8px 10px;\n  border-radius: 6px;\n  margin-bottom: 4px;\n  transition: background 0.2s;\n}\n\n.tool-item:hover {\n  background: #f8fafc;\n}\n\n.tool-item.connected {\n  border-left: 2px solid #10b981;\n}\n\n.tool-item.error {\n  border-left: 2px solid #ef4444;\n}\n\n.tool-item.disconnected {\n  border-left: 2px solid #94a3b8;\n  opacity: 0.6;\n}\n\n.tool-status-indicator {\n  width: 6px;\n  height: 6px;\n  border-radius: 50%;\n  flex-shrink: 0;\n}\n\n.tool-item.connected .tool-status-indicator {\n  background: #10b981;\n  box-shadow: 0 0 6px rgba(16, 185, 129, 0.5);\n}\n\n.tool-item.error .tool-status-indicator {\n  background: #ef4444;\n  box-shadow: 0 0 6px rgba(239, 68, 68, 0.5);\n}\n\n.tool-item.disconnected .tool-status-indicator {\n  background: #94a3b8;\n}\n\n.tool-info {\n  flex: 1;\n  display: flex;\n  flex-direction: column;\n  gap: 2px;\n  min-width: 0;\n}\n\n.tool-name {\n  font-size: 12px;\n  font-weight: 600;\n  color: #1e293b;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.tool-type {\n  font-size: 10px;\n  color: #94a3b8;\n  text-transform: uppercase;\n  letter-spacing: 0.5px;\n}\n\n.tool-status-text {\n  font-size: 10px;\n  color: #64748b;\n  text-transform: capitalize;\n}\n\n.status-mode {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n}\n\n.mode-toggle {\n  display: flex;\n  align-items: center;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 6px;\n  padding: 2px;\n  cursor: pointer;\n  transition: border-color 0.2s;\n}\n\n.mode-toggle:hover {\n  border-color: #cbd5e1;\n}\n\n.mode-toggle.disabled {\n  cursor: not-allowed;\n  opacity: 0.7;\n}\n\n.mode-toggle.disabled:hover {\n  border-color: #e5e7eb;\n}\n\n.mode-option {\n  padding: 3px 10px;\n  border-radius: 4px;\n  font-size: 11px;\n  font-weight: 500;\n  color: #64748b;\n  transition: all 0.2s;\n  user-select: none;\n}\n\n.mode-option.active {\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n  box-shadow: 0 2px 4px rgba(102, 126, 234, 0.3);\n}\n\n.mode-option.disabled {\n  opacity: 0.4;\n  cursor: not-allowed;\n}\n\n\n.status-connected {\n  color: #10b981;\n}\n\n.tools-list::-webkit-scrollbar {\n  width: 4px;\n}\n\n.tools-list::-webkit-scrollbar-track {\n  background: transparent;\n}\n\n.tools-list::-webkit-scrollbar-thumb {\n  background: #cbd5e1;\n  border-radius: 2px;\n}\n\n.tools-list::-webkit-scrollbar-thumb:hover {\n  background: #94a3b8;\n}\n\n/* Agent Mode Styles */\n.status-agents {\n  cursor: pointer;\n  padding: 4px 8px;\n  border-radius: 6px;\n  transition: background 0.2s;\n  position: relative;\n}\n\n.status-agents:hover {\n  background: #f1f5f9;\n}\n\n.agents-popup {\n  position: absolute;\n  bottom: calc(100% + 8px);\n  left: 50%;\n  transform: translateX(-50%);\n  width: 280px;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);\n  z-index: 1000;\n  animation: slideUp 0.2s ease;\n}\n\n.agents-popup-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 12px 14px;\n  border-bottom: 1px solid #e5e7eb;\n  background: #f9fafb;\n  border-radius: 8px 8px 0 0;\n  font-weight: 600;\n  font-size: 12px;\n  color: #1e293b;\n}\n\n.agents-count {\n  font-size: 11px;\n  color: #64748b;\n  background: white;\n  padding: 2px 6px;\n  border-radius: 4px;\n  border: 1px solid #e5e7eb;\n}\n\n.agents-list {\n  max-height: 240px;\n  overflow-y: auto;\n  padding: 8px;\n}\n\n.agents-empty {\n  padding: 20px;\n  text-align: center;\n  color: #94a3b8;\n  font-size: 12px;\n}\n\n.agent-item {\n  display: flex;\n  align-items: center;\n  gap: 10px;\n  padding: 8px 10px;\n  border-radius: 6px;\n  margin-bottom: 4px;\n  transition: background 0.2s;\n}\n\n.agent-item:hover {\n  background: #f8fafc;\n}\n\n.agent-item.enabled {\n  border-left: 2px solid #667eea;\n}\n\n.agent-item.disabled {\n  border-left: 2px solid #94a3b8;\n  opacity: 0.6;\n}\n\n.agent-status-indicator {\n  width: 6px;\n  height: 6px;\n  border-radius: 50%;\n  flex-shrink: 0;\n}\n\n.agent-item.enabled .agent-status-indicator {\n  background: #667eea;\n  box-shadow: 0 0 6px rgba(102, 126, 234, 0.5);\n}\n\n.agent-item.disabled .agent-status-indicator {\n  background: #94a3b8;\n}\n\n.agent-info {\n  flex: 1;\n  display: flex;\n  flex-direction: column;\n  gap: 2px;\n  min-width: 0;\n}\n\n.agent-name {\n  font-size: 12px;\n  font-weight: 600;\n  color: #1e293b;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.agent-role {\n  font-size: 10px;\n  color: #94a3b8;\n  text-transform: capitalize;\n}\n\n.agent-status-text {\n  font-size: 10px;\n  color: #64748b;\n  text-transform: capitalize;\n}\n\n.agents-list::-webkit-scrollbar {\n  width: 4px;\n}\n\n.agents-list::-webkit-scrollbar-track {\n  background: transparent;\n}\n\n.agents-list::-webkit-scrollbar-thumb {\n  background: #cbd5e1;\n  border-radius: 2px;\n}\n\n.agents-list::-webkit-scrollbar-thumb:hover {\n  background: #94a3b8;\n}\n\n.agents-info-box {\n  padding: 12px 14px;\n  background: #f8fafc;\n  border-radius: 6px;\n  margin-bottom: 8px;\n}\n\n.agents-info-box.single-mode {\n  display: flex;\n  flex-direction: column;\n  align-items: center;\n  gap: 8px;\n  padding: 24px 14px;\n  text-align: center;\n}\n\n.agents-info-label {\n  font-size: 11px;\n  font-weight: 600;\n  color: #64748b;\n  text-transform: uppercase;\n  letter-spacing: 0.5px;\n}\n\n.single-agent-icon {\n  color: #667eea;\n  margin-bottom: 4px;\n}\n\n.single-agent-label {\n  font-size: 13px;\n  font-weight: 600;\n  color: #1e293b;\n}\n\n.single-agent-description {\n  font-size: 11px;\n  color: #64748b;\n  line-height: 1.5;\n  max-width: 240px;\n}\n\n/* More Menu Styles */\n.status-more {\n  cursor: pointer;\n  padding: 4px 8px;\n  border-radius: 6px;\n  transition: background 0.2s;\n  position: relative;\n}\n\n.status-more:hover {\n  background: #f1f5f9;\n}\n\n.more-popup {\n  position: absolute;\n  bottom: calc(100% + 8px);\n  left: 50%;\n  transform: translateX(-50%);\n  width: 200px;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);\n  z-index: 1000;\n  animation: slideUp 0.2s ease;\n}\n\n.more-popup-header {\n  display: flex;\n  justify-content: center;\n  align-items: center;\n  padding: 12px 14px;\n  border-bottom: 1px solid #e5e7eb;\n  background: #f9fafb;\n  border-radius: 8px 8px 0 0;\n  font-weight: 600;\n  font-size: 12px;\n  color: #1e293b;\n}\n\n.more-list {\n  padding: 4px 0;\n}\n\n.more-item {\n  display: flex;\n  align-items: center;\n  gap: 10px;\n  padding: 10px 14px;\n  cursor: pointer;\n  transition: background 0.2s;\n  font-size: 12px;\n}\n\n.more-item:hover {\n  background: #f8fafc;\n}\n\n.more-item-label {\n  color: #475569;\n  font-weight: 500;\n}\n\n/* Responsive Design */\n@media (max-width: 768px) {\n  .status-bar {\n    padding: 0 12px;\n    height: 40px;\n    font-size: 12px;\n  }\n\n  .status-bar-center {\n    gap: 8px;\n  }\n\n  .status-item {\n    gap: 4px;\n  }\n\n  .status-label {\n    display: none;\n  }\n\n  .status-badge {\n    font-size: 9px;\n    padding: 1px 4px;\n    min-width: 16px;\n  }\n\n  .mode-option {\n    padding: 2px 8px;\n    font-size: 10px;\n  }\n}\n\n@media (max-width: 480px) {\n  .status-bar {\n    padding: 0 8px;\n  }\n\n  .status-bar-center {\n    gap: 4px;\n  }\n\n  .tools-popup,\n  .agents-popup,\n  .more-popup {\n    width: 180px;\n    max-height: 200px;\n  }\n\n  .tool-item,\n  .agent-item,\n  .more-item {\n    padding: 6px 8px;\n    font-size: 11px;\n  }\n}\n\n/* Tool grouping styles */\n.tool-group {\n  margin-bottom: 12px;\n}\n\n.tool-group:last-child {\n  margin-bottom: 0;\n}\n\n.tool-group-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 8px 12px;\n  background: #f8fafc;\n  border-radius: 6px;\n  margin-bottom: 4px;\n  border: 1px solid #e5e7eb;\n}\n\n.tool-group-name {\n  font-size: 12px;\n  font-weight: 600;\n  color: #374151;\n  text-transform: capitalize;\n}\n\n.tool-group-stats {\n  display: flex;\n  flex-direction: column;\n  align-items: flex-end;\n  gap: 2px;\n}\n\n.tool-group-count {\n  font-size: 10px;\n  color: #6b7280;\n  background: #e5e7eb;\n  padding: 2px 6px;\n  border-radius: 8px;\n  font-weight: 500;\n}\n\n.tool-group-internal {\n  font-size: 9px;\n  color: #9ca3af;\n  font-weight: 500;\n}\n\n.tool-group-items {\n  margin-left: 8px;\n}\n\n.tool-group-items .tool-item {\n  padding-left: 20px;\n  border-left: 2px solid #e5e7eb;\n  margin-bottom: 2px;\n}\n\n.tool-group-items .tool-item:last-child {\n  margin-bottom: 0;\n}\n\n/* Examples popup styles */\n.status-examples {\n  cursor: pointer;\n  transition: all 0.2s ease;\n}\n\n.status-examples:hover {\n  background: rgba(251, 191, 36, 0.1);\n}\n\n.status-examples:hover .status-label {\n  color: #f59e0b;\n}\n\n.status-examples:hover .status-badge {\n  background: linear-gradient(135deg, #fbbf24 0%, #f59e0b 100%);\n  color: white;\n}\n\n/* Animated lightbulb when input is empty */\n.lightbulb-glow {\n  animation: lightbulbGlow 2s ease-in-out infinite;\n  color: #8b5cf6;\n}\n\n@keyframes lightbulbGlow {\n  0%, 100% {\n    color: #8b5cf6;\n    filter: drop-shadow(0 0 2px rgba(139, 92, 246, 0.4));\n    transform: scale(1);\n  }\n  50% {\n    color: #a78bfa;\n    filter: drop-shadow(0 0 4px rgba(167, 139, 250, 0.6));\n    transform: scale(1.1);\n  }\n}\n\n/* Animate the entire button when input is empty */\n.status-examples.animate-prompt {\n  animation: pulsePrompt 2s ease-in-out infinite;\n  border-radius: 6px;\n}\n\n@keyframes pulsePrompt {\n  0%, 100% {\n    transform: scale(1);\n    box-shadow: 0 0 0 0 rgba(139, 92, 246, 0);\n    background: transparent;\n  }\n  50% {\n    transform: scale(1.02);\n    box-shadow: 0 0 8px rgba(139, 92, 246, 0.15);\n    background: rgba(139, 92, 246, 0.05);\n  }\n}\n\n/* Make the label slightly more prominent when animating */\n.status-examples.animate-prompt .status-label {\n  animation: labelPulse 2s ease-in-out infinite;\n}\n\n@keyframes labelPulse {\n  0%, 100% {\n    color: #475569;\n  }\n  50% {\n    color: #8b5cf6;\n  }\n}\n\n.examples-popup {\n  position: absolute;\n  bottom: calc(100% + 8px);\n  left: 0;\n  min-width: 450px;\n  max-width: 600px;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 12px;\n  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.1);\n  padding: 0;\n  z-index: 1000;\n  animation: slideUpFadeIn 0.2s ease;\n}\n\n.examples-popup-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 14px 16px;\n  border-bottom: 1px solid #e5e7eb;\n  background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%);\n  border-radius: 12px 12px 0 0;\n}\n\n.examples-popup-header span:first-child {\n  font-weight: 600;\n  font-size: 13px;\n  color: #92400e;\n}\n\n.examples-count {\n  font-size: 11px;\n  padding: 3px 8px;\n  background: rgba(146, 64, 14, 0.1);\n  border-radius: 12px;\n  color: #92400e;\n  font-weight: 600;\n}\n\n.examples-list {\n  padding: 8px;\n  max-height: 400px;\n  overflow-y: auto;\n}\n\n.example-item {\n  display: flex;\n  align-items: flex-start;\n  gap: 10px;\n  padding: 12px;\n  border-radius: 8px;\n  cursor: pointer;\n  transition: all 0.2s ease;\n  margin-bottom: 4px;\n  border: 1px solid transparent;\n}\n\n.example-item:hover {\n  background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);\n  border-color: #fbbf24;\n  transform: translateX(4px);\n}\n\n.example-icon {\n  flex-shrink: 0;\n  margin-top: 2px;\n  color: #f59e0b;\n}\n\n.example-item:hover .example-icon {\n  color: #d97706;\n}\n\n.example-text {\n  font-size: 13px;\n  color: #475569;\n  line-height: 1.5;\n  font-weight: 500;\n}\n\n.example-item:hover .example-text {\n  color: #1e293b;\n}\n\n"],"sourceRoot":""}]);
// Exports
/* harmony default export */ __webpack_exports__["default"] = (___CSS_LOADER_EXPORT___);


/***/ })

}]);
//# sourceMappingURL=main-agentic_chat_src_St.ffc2380844142d36bcfa.js.map