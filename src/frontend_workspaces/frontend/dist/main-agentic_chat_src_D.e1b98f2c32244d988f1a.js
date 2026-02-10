"use strict";
(self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] = self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] || []).push([["main-agentic_chat_src_D"],{

/***/ "../agentic_chat/src/DebugPanel.css":
/*!******************************************!*\
  !*** ../agentic_chat/src/DebugPanel.css ***!
  \******************************************/
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
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_DebugPanel_css__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! !!../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!./DebugPanel.css */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/DebugPanel.css");

      
      
      
      
      
      
      
      
      

var options = {};

options.styleTagTransform = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default());
options.setAttributes = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default());
options.insert = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default().bind(null, "head");
options.domAPI = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default());
options.insertStyleElement = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default());

var update = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default()(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_DebugPanel_css__WEBPACK_IMPORTED_MODULE_6__["default"], options);




       /* unused harmony default export */ var __WEBPACK_DEFAULT_EXPORT__ = (_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_DebugPanel_css__WEBPACK_IMPORTED_MODULE_6__["default"] && _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_DebugPanel_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals ? _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_DebugPanel_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals : undefined);


/***/ }),

/***/ "../agentic_chat/src/DebugPanel.tsx":
/*!******************************************!*\
  !*** ../agentic_chat/src/DebugPanel.tsx ***!
  \******************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   DebugPanel: function() { return /* binding */ DebugPanel; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");
/* harmony import */ var _constants__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./constants */ "../agentic_chat/src/constants.ts");
/* harmony import */ var _DebugPanel_css__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ./DebugPanel.css */ "../agentic_chat/src/DebugPanel.css");




const EMPTY_THREAD_ID = "Not initialized";
function DebugPanel({
  threadId
}) {
  const [isOpen, setIsOpen] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [agentState, setAgentState] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [isLoading, setIsLoading] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [error, setError] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [autoRefresh, setAutoRefresh] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [copied, setCopied] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const fetchAgentState = (0,react__WEBPACK_IMPORTED_MODULE_0__.useCallback)(async () => {
    if (!threadId || threadId === EMPTY_THREAD_ID) {
      setError("No thread ID available");
      setAgentState(null);
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch(`${_constants__WEBPACK_IMPORTED_MODULE_2__.API_BASE_URL}/api/agent/state`, {
        method: "GET",
        headers: {
          "X-Thread-ID": threadId
        }
      });
      if (response.status === 503) {
        setAgentState({
          thread_id: threadId,
          state: null,
          variables: {},
          variables_count: 0,
          memories: {},
          memories_count: 0,
          message: "Agent graph not initialized"
        });
        return;
      }
      if (!response.ok) {
        throw new Error(`Failed to fetch state: ${response.status} ${response.statusText}`);
      }
      const data = await response.json();
      setAgentState(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch agent state");
      console.error("Error fetching agent state:", err);
    } finally {
      setIsLoading(false);
    }
  }, [threadId]);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    if (isOpen && threadId) {
      fetchAgentState();
    }
  }, [isOpen, threadId, fetchAgentState]);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    if (!autoRefresh || !isOpen || !threadId) return;
    const interval = setInterval(() => {
      fetchAgentState();
    }, 2000);
    return () => clearInterval(interval);
  }, [autoRefresh, isOpen, threadId, fetchAgentState]);
  const copyToClipboard = text => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  const formatJSON = obj => {
    try {
      return JSON.stringify(obj, null, 2);
    } catch {
      return String(obj);
    }
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-panel-container"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "debug-panel-toggle",
    onClick: () => setIsOpen(!isOpen),
    title: "Toggle Debug Panel"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Bug, {
    size: 16
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Debug"), isOpen ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronUp, {
    size: 16
  }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronDown, {
    size: 16
  })), isOpen && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-panel-content"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-panel-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Debug Information"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-panel-actions"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "debug-action-btn",
    onClick: fetchAgentState,
    disabled: isLoading,
    title: "Refresh State"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.RefreshCw, {
    size: 14,
    className: isLoading ? "spinning" : ""
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
    className: "debug-auto-refresh"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: autoRefresh,
    onChange: e => setAutoRefresh(e.target.checked)
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Auto-refresh")))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-section"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-section-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", null, "Thread ID"), threadId && threadId !== EMPTY_THREAD_ID && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "debug-copy-btn",
    onClick: () => copyToClipboard(threadId),
    title: "Copy Thread ID"
  }, copied ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Check, {
    size: 12
  }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Copy, {
    size: 12
  }))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-thread-id"
  }, threadId || EMPTY_THREAD_ID)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-section"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-section-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", null, "Agent State")), isLoading && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-loading"
  }, "Loading..."), error && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-error"
  }, "Error: ", error), agentState && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-state-content"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-state-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-label"
  }, "Thread ID:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-value"
  }, agentState.thread_id)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-state-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-label"
  }, "Variables Count:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-value"
  }, agentState.variables_count)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-state-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-label"
  }, "Memories Count:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-value"
  }, agentState.memories_count || 0)), agentState.state ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-state-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-label"
  }, "Lite Mode:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-value",
    style: {
      color: agentState.state.lite_mode === null ? '#94a3b8' : agentState.state.lite_mode ? '#10b981' : '#f59e0b',
      fontWeight: 600
    }
  }, agentState.state.lite_mode === null ? 'Not Set (using settings)' : agentState.state.lite_mode ? 'True (Fast/Lite)' : 'False (Balanced)')), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-state-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-label"
  }, "Current App:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-value"
  }, agentState.state.current_app || "N/A")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-state-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-label"
  }, "Chat Messages Count:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-value"
  }, agentState.state.chat_messages_count || 0)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-state-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-label"
  }, "URL:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-value"
  }, agentState.state.url || "N/A")), agentState.state.input && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-state-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-label"
  }, "Input:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-value"
  }, agentState.state.input))) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-state-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-label"
  }, "State:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-value"
  }, agentState.message || "No state available")), Object.keys(agentState.variables).length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-state-item debug-variables"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-label"
  }, "Variables:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("pre", {
    className: "debug-json"
  }, formatJSON(agentState.variables))), agentState.memories && Object.keys(agentState.memories).length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "debug-state-item debug-variables"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "debug-label"
  }, "Memories:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("pre", {
    className: "debug-json"
  }, formatJSON(agentState.memories)))))));
}

/***/ }),

/***/ "../agentic_chat/src/FileAutocomplete.css":
/*!************************************************!*\
  !*** ../agentic_chat/src/FileAutocomplete.css ***!
  \************************************************/
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
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_FileAutocomplete_css__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! !!../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!./FileAutocomplete.css */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/FileAutocomplete.css");

      
      
      
      
      
      
      
      
      

var options = {};

options.styleTagTransform = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default());
options.setAttributes = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default());
options.insert = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default().bind(null, "head");
options.domAPI = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default());
options.insertStyleElement = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default());

var update = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default()(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_FileAutocomplete_css__WEBPACK_IMPORTED_MODULE_6__["default"], options);




       /* unused harmony default export */ var __WEBPACK_DEFAULT_EXPORT__ = (_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_FileAutocomplete_css__WEBPACK_IMPORTED_MODULE_6__["default"] && _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_FileAutocomplete_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals ? _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_FileAutocomplete_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals : undefined);


/***/ }),

/***/ "../agentic_chat/src/FileAutocomplete.tsx":
/*!************************************************!*\
  !*** ../agentic_chat/src/FileAutocomplete.tsx ***!
  \************************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   FileAutocomplete: function() { return /* binding */ FileAutocomplete; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");
/* harmony import */ var _FileAutocomplete_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./FileAutocomplete.css */ "../agentic_chat/src/FileAutocomplete.css");




function FileAutocomplete({
  onFileSelect,
  onAutocompleteOpen,
  onFileHover,
  disabled = false
}) {
  const [allFiles, setAllFiles] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [suggestions, setSuggestions] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [showSuggestions, setShowSuggestions] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [selectedIndex, setSelectedIndex] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(0);
  const [position, setPosition] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const currentInputValueRef = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)('');
  const lastProcessedValueRef = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)('');
  const usedMockRef = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)(false);
  const suggestionsRef = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)(null);
  const isProcessingRef = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)(false);
  const selectedItemRef = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)(null);
  const getInputPosition = () => {
    const inputContainer = document.querySelector('.WACInputContainer');
    if (inputContainer) {
      const rect = inputContainer.getBoundingClientRect();
      return {
        top: rect.top,
        left: rect.left,
        width: rect.width
      };
    }
    const carbonChat = document.querySelector('cds-aichat-react');
    if (carbonChat) {
      const rect = carbonChat.getBoundingClientRect();
      return {
        top: rect.top,
        left: rect.left,
        width: rect.width
      };
    }
    const textarea = document.querySelector('.WAC__TextArea-textarea, textarea, input[type="text"]');
    if (textarea) {
      const rect = textarea.getBoundingClientRect();
      if (rect.top > 50 && rect.left > 0) {
        return {
          top: rect.top,
          left: rect.left,
          width: rect.width
        };
      }
    }
    return {
      top: window.innerHeight - 100,
      left: 20,
      width: Math.min(600, window.innerWidth - 40)
    };
  };
  const handleInputChange = value => {
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
  const handleFileSelect = (0,react__WEBPACK_IMPORTED_MODULE_0__.useCallback)(file => {
    const textarea = document.getElementById('main-input_field');
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
    const newValue = currentValue.substring(0, lastAtIndex) + `./${file.path}` + textAfterSearchTerm;
    const nativeTextareaSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
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
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    if (disabled) {
      return;
    }
    loadWorkspaceFiles();
    const fileInterval = setInterval(loadWorkspaceFiles, 15000);

    // Listen to Carbon AI Chat events
    const setupCarbonListeners = () => {
      // Find the Carbon AI Chat component
      const carbonChat = document.querySelector('cds-aichat-react');

      // Also check for custom chat textarea
      const customChatTextarea = document.getElementById('main-input_field');
      if (carbonChat || customChatTextarea) {
        // Listen for input events from Carbon chat
        const handleCarbonInput = event => {
          const target = event.target || event.currentTarget;
          const tryHandleValue = value => {
            if (typeof value === 'string') {
              currentInputValueRef.current = value;
              handleInputChange(value);
              return true;
            }
            return false;
          };
          if (tryHandleValue(target?.value)) {
            return;
          }
          if (tryHandleValue(event.detail?.value)) {
            return;
          }
          if (typeof event.composedPath === 'function') {
            const path = event.composedPath();
            for (const el of path) {
              const node = el;
              if (node && (node.tagName === 'TEXTAREA' || node.tagName === 'INPUT' || node.contentEditable === 'true')) {
                if (tryHandleValue(node.value || node.textContent)) {
                  return;
                }
              }
            }
          }
          const active = document.activeElement;
          if (active && (active.tagName === 'TEXTAREA' || active.tagName === 'INPUT' || active.contentEditable === 'true')) {
            if (tryHandleValue(active.value || active.textContent)) {
              return;
            }
          }
          const textarea = document.querySelector('.WAC__TextArea-textarea, textarea, input[type="text"], [contenteditable]');
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
              textarea.addEventListener('input', e => {
                // Don't stop propagation - let React handle it too
                const value = e.target?.value || e.target?.textContent || '';
                currentInputValueRef.current = value;
                handleInputChange(value);
              });
            }
          });
        }, 1000);
        const handleDocumentInput = e => {
          const target = e.target;
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
  }, [disabled]);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    if (disabled) {
      return;
    }
    const handleKeyDown = e => {
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
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    if (selectedItemRef.current) {
      selectedItemRef.current.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
        inline: 'nearest'
      });
    }
  }, [selectedIndex]);

  // Highlight file when selection changes via keyboard navigation
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    if (showSuggestions && suggestions.length > 0 && selectedIndex >= 0 && selectedIndex < suggestions.length) {
      onFileHover?.(suggestions[selectedIndex].path);
    } else if (!showSuggestions) {
      onFileHover?.(null);
    }
  }, [selectedIndex, showSuggestions, suggestions, onFileHover]);
  const loadWorkspaceFiles = async () => {
    try {
      const {
        workspaceService
      } = await __webpack_require__.e(/*! import() */ "agentic_chat_src_workspaceService_ts").then(__webpack_require__.bind(__webpack_require__, /*! ./workspaceService */ "../agentic_chat/src/workspaceService.ts"));
      const data = await workspaceService.getWorkspaceTree();
      const files = extractFiles(data.tree || []);
      setAllFiles(files);
    } catch (error) {
      if (!usedMockRef.current) {
        useMockData();
      }
    }
  };
  const useMockData = () => {
    const mockFiles = [{
      name: 'top_opportunities_arkansas.txt',
      path: 'cuga_workspace/top_opportunities_arkansas.txt'
    }, {
      name: 'top_10_opportunities_arkansas.txt',
      path: 'cuga_workspace/top_10_opportunities_arkansas.txt'
    }, {
      name: 'top_3_opportunities_arkansas.txt',
      path: 'cuga_workspace/top_3_opportunities_arkansas.txt'
    }, {
      name: 'analysis_report.md',
      path: 'cuga_workspace/analysis_report.md'
    }, {
      name: 'data_export.json',
      path: 'cuga_workspace/data_export.json'
    }];
    usedMockRef.current = true;
    setAllFiles(mockFiles);
  };
  const extractFiles = nodes => {
    const files = [];
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
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, showSuggestions && suggestions.length > 0 && position && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    ref: suggestionsRef,
    className: "file-autocomplete",
    style: {
      top: `${position.top}px`,
      left: `${position.left}px`
    },
    "data-debug-position": JSON.stringify(position)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "file-autocomplete-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Workspace Files"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "file-count"
  }, suggestions.length)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "file-autocomplete-list"
  }, suggestions.map((file, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: file.path,
    ref: index === selectedIndex ? selectedItemRef : null,
    className: `file-autocomplete-item ${index === selectedIndex ? 'selected' : ''}`,
    onClick: e => {
      e.preventDefault();
      handleFileSelect(file);
    },
    onMouseEnter: () => {
      setSelectedIndex(index);
      onFileHover?.(file.path);
    },
    onMouseLeave: () => onFileHover?.(null)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.FileText, {
    size: 16,
    className: "file-icon"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "file-info"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "file-name"
  }, file.name), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "file-path"
  }, "./", file.path))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "file-autocomplete-footer"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "hint"
  }, "\u2191\u2193 navigate \u2022 Enter/Tab select \u2022 Esc close"))));
}

/***/ }),

/***/ "../agentic_chat/src/Followup.tsx":
/*!****************************************!*\
  !*** ../agentic_chat/src/Followup.tsx ***!
  \****************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   FollowupAction: function() { return /* binding */ FollowupAction; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");


const FollowupAction = ({
  followupAction,
  callback
}) => {
  const [response, setResponse] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("");
  const [selectedValues, setSelectedValues] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [isSubmitted, setIsSubmitted] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [startTime] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(Date.now());
  const [isWaiting, setIsWaiting] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(true);

  // Safety check for followupAction
  if (!followupAction) {
    console.error("FollowupAction received null or undefined followupAction");
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "text-red-500 p-4"
    }, "Error: Invalid action data");
  }
  const {
    action_id,
    action_name,
    description,
    type,
    button_text,
    placeholder,
    options,
    max_selections,
    min_selections = 1,
    required = true,
    validation_pattern,
    max_length,
    min_length,
    color = "primary"
  } = followupAction;
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    const timer = setTimeout(() => {
      setIsWaiting(false);
    }, 300);
    return () => clearTimeout(timer);
  }, []);
  const colorThemes = {
    primary: {
      button: "bg-blue-500 hover:bg-blue-600 text-white",
      accent: "text-blue-600 border-blue-200 bg-blue-50"
    },
    success: {
      button: "bg-green-500 hover:bg-green-600 text-white",
      accent: "text-green-600 border-green-200 bg-green-50"
    },
    warning: {
      button: "bg-yellow-500 hover:bg-yellow-600 text-white",
      accent: "text-yellow-600 border-yellow-200 bg-yellow-50"
    },
    danger: {
      button: "bg-red-500 hover:bg-red-600 text-white",
      accent: "text-red-600 border-red-200 bg-red-50"
    },
    secondary: {
      button: "bg-gray-500 hover:bg-gray-600 text-white",
      accent: "text-gray-600 border-gray-200 bg-gray-50"
    }
  };
  const theme = colorThemes[color] || colorThemes.primary;
  const createResponse = responseData => {
    const baseResponse = {
      action_id,
      response_type: type,
      timestamp: new Date().toISOString(),
      response_time_ms: Date.now() - startTime,
      client_info: {
        user_agent: navigator.userAgent,
        language: navigator.language,
        platform: navigator.platform
      }
    };
    return {
      ...baseResponse,
      ...responseData
    };
  };
  const handleSubmit = responseData => {
    if (isSubmitted) return;
    setIsSubmitted(true);
    const fullResponse = createResponse(responseData);
    callback(fullResponse);
  };
  const handleTextSubmit = () => {
    if (!response.trim() && required) return;

    // Validation
    if (validation_pattern && !new RegExp(validation_pattern).test(response)) {
      // Replaced alert with a simple console log for demonstration.
      // In a real app, you'd use a custom modal or inline error message.
      console.error("Please enter a valid response");
      return;
    }
    if (min_length && response.length < min_length) {
      console.error(`Response must be at least ${min_length} characters`);
      return;
    }
    if (max_length && response.length > max_length) {
      console.error(`Response must be no more than ${max_length} characters`);
      return;
    }
    handleSubmit({
      text_response: response
    });
  };
  const handleButtonClick = () => {
    handleSubmit({
      button_clicked: true
    });
  };
  const handleConfirmation = confirmed => {
    handleSubmit({
      confirmed
    });
  };
  const handleSelectChange = value => {
    let newSelection;
    if (type === "multi_select") {
      if (selectedValues.includes(value)) {
        newSelection = selectedValues.filter(v => v !== value);
      } else {
        if (max_selections && selectedValues.length >= max_selections) {
          return;
        }
        newSelection = [...selectedValues, value];
      }
    } else {
      newSelection = [value];
    }
    setSelectedValues(newSelection);
    if (type === "select") {
      const selectedOptions = (options || []).filter(opt => newSelection.includes(opt.value));
      handleSubmit({
        selected_values: newSelection,
        selected_options: selectedOptions
      });
    }
  };
  const handleMultiSelectSubmit = () => {
    if (selectedValues.length < min_selections) {
      console.error(`Please select at least ${min_selections} option(s)`);
      return;
    }
    const selectedOptions = (options || []).filter(opt => selectedValues.includes(opt.value));
    handleSubmit({
      selected_values: selectedValues,
      selected_options: selectedOptions
    });
  };
  const renderWaitingState = () => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-center py-4"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm text-gray-500"
  }, "Loading..."));
  const renderActionContent = () => {
    if (isWaiting) {
      return renderWaitingState();
    }
    if (isSubmitted) {
      return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "flex items-center justify-center py-4 text-green-600"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "flex items-center space-x-2 bg-green-50 px-4 py-2 rounded border border-green-200"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Check, {
        className: "w-5 h-5"
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "text-sm font-medium"
      }, "Response submitted successfully!")));
    }
    switch (type) {
      case "button":
        return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
          onClick: handleButtonClick,
          disabled: isSubmitted,
          className: `w-full px-4 py-3 rounded font-medium ${theme.button} flex items-center justify-center gap-2 ${isSubmitted ? "opacity-50 cursor-not-allowed" : ""}`
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, button_text || action_name));
      case "text_input":
      case "natural_language":
        return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "space-y-3"
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("textarea", {
          value: response,
          onChange: e => setResponse(e.target.value),
          placeholder: placeholder || "Enter your response...",
          disabled: isSubmitted,
          className: `w-full px-4 py-3 border border-gray-200 rounded resize-none focus:outline-none focus:border-blue-500 text-sm ${response.trim() ? theme.accent : ""} ${isSubmitted ? "opacity-50 cursor-not-allowed bg-gray-50" : ""}`,
          rows: type === "natural_language" ? 3 : 1,
          maxLength: max_length
        }), max_length && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "text-xs text-gray-500 text-right"
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
          className: response.length > max_length * 0.8 ? "text-orange-500" : ""
        }, response.length), "/", max_length), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
          onClick: handleTextSubmit,
          disabled: isSubmitted || !response.trim() && required,
          className: `px-4 py-2 rounded text-sm font-medium ${isSubmitted || !response.trim() && required ? "bg-gray-200 text-gray-400 cursor-not-allowed" : theme.button} flex items-center gap-2`
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Send, {
          className: "w-4 h-4"
        }), "Submit"));
      case "select":
        return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "space-y-2"
        }, (options || []).map(option => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
          key: option.value,
          onClick: () => handleSelectChange(option.value),
          disabled: isSubmitted,
          className: `w-full px-4 py-3 text-left rounded border text-sm ${selectedValues.includes(option.value) ? theme.button : "border-gray-200 hover:border-gray-300 hover:bg-gray-50"} ${isSubmitted ? "opacity-50 cursor-not-allowed" : ""}`
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "font-medium"
        }, option.label), option.description && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "text-xs opacity-75 mt-1"
        }, option.description))));
      case "multi_select":
        return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "space-y-3"
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "space-y-2 max-h-48 overflow-y-auto"
        }, (options || []).map(option => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
          key: option.value,
          className: `flex items-start gap-3 p-3 rounded border cursor-pointer ${selectedValues.includes(option.value) ? theme.accent : "border-gray-200 hover:border-gray-300 hover:bg-gray-50"}`
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
          type: "checkbox",
          checked: selectedValues.includes(option.value),
          onChange: () => handleSelectChange(option.value),
          className: "mt-1 w-4 h-4 text-blue-600 rounded focus:ring-blue-500",
          disabled: isSubmitted || !selectedValues.includes(option.value) && !!max_selections && selectedValues.length >= max_selections
        }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "flex-1"
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "text-sm font-medium"
        }, option.label), option.description && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "text-xs text-gray-600 mt-1"
        }, option.description))))), max_selections && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "text-xs text-gray-500"
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
          className: selectedValues.length === max_selections ? "text-orange-500 font-medium" : ""
        }, selectedValues.length), "/", max_selections, " selected"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
          onClick: handleMultiSelectSubmit,
          disabled: isSubmitted || selectedValues.length < min_selections,
          className: `px-4 py-2 rounded text-sm font-medium ${isSubmitted || selectedValues.length < min_selections ? "bg-gray-200 text-gray-400 cursor-not-allowed" : theme.button} flex items-center gap-2`
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Check, {
          className: "w-4 h-4"
        }), "Submit (", selectedValues.length, ")"));
      case "confirmation":
        // Check if this is a tool approval action with code preview
        const isToolApproval = action_id === "tool_approval";
        const toolData = followupAction.additional_data?.tool;
        const codePreview = toolData?.code_preview || [];
        const requiredTools = toolData?.required_tools || [];
        return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "space-y-3"
        }, isToolApproval && (codePreview.length > 0 || requiredTools.length > 0) && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "bg-amber-50 border border-amber-200 rounded p-3 text-sm"
        }, requiredTools.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "mb-2"
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
          className: "font-semibold text-amber-900"
        }, "Tools requiring approval:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "mt-1 flex flex-wrap gap-1"
        }, requiredTools.map((tool, idx) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
          key: idx,
          className: "inline-block bg-amber-100 text-amber-800 px-2 py-0.5 rounded text-xs font-mono"
        }, tool)))), codePreview.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
          className: "font-semibold text-amber-900"
        }, "Code Preview:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("pre", {
          className: "mt-1 bg-gray-800 text-green-400 p-3 rounded text-xs overflow-x-auto font-mono border border-gray-700"
        }, codePreview.join('\n')))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "flex gap-3"
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
          onClick: () => handleConfirmation(true),
          disabled: isSubmitted,
          className: `flex-1 px-4 py-3 ${isToolApproval ? "bg-amber-500 hover:bg-amber-600" : "bg-green-500 hover:bg-green-600"} text-white rounded font-medium flex items-center justify-center gap-2 ${isSubmitted ? "opacity-50 cursor-not-allowed" : ""}`
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Check, {
          className: "w-4 h-4"
        }), isToolApproval ? "Approve & Execute" : "Confirm"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
          onClick: () => handleConfirmation(false),
          disabled: isSubmitted,
          className: `flex-1 px-4 py-3 bg-gray-500 hover:bg-gray-600 text-white rounded font-medium flex items-center justify-center gap-2 ${isSubmitted ? "opacity-50 cursor-not-allowed" : ""}`
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
          className: "w-4 h-4"
        }), isToolApproval ? "Deny" : "Cancel")));
      default:
        return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "text-gray-500 text-sm"
        }, "Unsupported action type: ", type);
    }
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-white border border-gray-200 rounded p-4 mx-auto"
  }, !isWaiting && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-4"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2 mb-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
    className: "font-medium text-gray-900 text-sm"
  }, action_name), required && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-red-500 text-xs"
  }, "*")), description && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-gray-600 text-xs"
  }, description)), renderActionContent());
};

/***/ }),

/***/ "../agentic_chat/src/FollowupSuggestions.css":
/*!***************************************************!*\
  !*** ../agentic_chat/src/FollowupSuggestions.css ***!
  \***************************************************/
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
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_FollowupSuggestions_css__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! !!../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!./FollowupSuggestions.css */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/FollowupSuggestions.css");

      
      
      
      
      
      
      
      
      

var options = {};

options.styleTagTransform = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default());
options.setAttributes = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default());
options.insert = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default().bind(null, "head");
options.domAPI = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default());
options.insertStyleElement = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default());

var update = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default()(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_FollowupSuggestions_css__WEBPACK_IMPORTED_MODULE_6__["default"], options);




       /* unused harmony default export */ var __WEBPACK_DEFAULT_EXPORT__ = (_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_FollowupSuggestions_css__WEBPACK_IMPORTED_MODULE_6__["default"] && _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_FollowupSuggestions_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals ? _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_FollowupSuggestions_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals : undefined);


/***/ }),

/***/ "../agentic_chat/src/FollowupSuggestions.tsx":
/*!***************************************************!*\
  !*** ../agentic_chat/src/FollowupSuggestions.tsx ***!
  \***************************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   FollowupSuggestions: function() { return /* binding */ FollowupSuggestions; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _FollowupSuggestions_css__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./FollowupSuggestions.css */ "../agentic_chat/src/FollowupSuggestions.css");


function FollowupSuggestions({
  suggestions,
  onSuggestionClick
}) {
  if (suggestions.length === 0) {
    return null;
  }
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "followup-suggestions-container"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "followup-suggestions-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "followup-suggestions-title"
  }, "Suggested followup questions:")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "followup-suggestions-list"
  }, suggestions.map((suggestion, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    key: index,
    className: "followup-suggestion-chip",
    onClick: () => onSuggestionClick(suggestion),
    type: "button"
  }, suggestion))));
}

/***/ }),

/***/ "../agentic_chat/src/GuidedTour.css":
/*!******************************************!*\
  !*** ../agentic_chat/src/GuidedTour.css ***!
  \******************************************/
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
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_GuidedTour_css__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! !!../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!./GuidedTour.css */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/GuidedTour.css");

      
      
      
      
      
      
      
      
      

var options = {};

options.styleTagTransform = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default());
options.setAttributes = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default());
options.insert = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default().bind(null, "head");
options.domAPI = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default());
options.insertStyleElement = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default());

var update = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default()(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_GuidedTour_css__WEBPACK_IMPORTED_MODULE_6__["default"], options);




       /* unused harmony default export */ var __WEBPACK_DEFAULT_EXPORT__ = (_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_GuidedTour_css__WEBPACK_IMPORTED_MODULE_6__["default"] && _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_GuidedTour_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals ? _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_GuidedTour_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals : undefined);


/***/ }),

/***/ "../agentic_chat/src/GuidedTour.tsx":
/*!******************************************!*\
  !*** ../agentic_chat/src/GuidedTour.tsx ***!
  \******************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   GuidedTour: function() { return /* binding */ GuidedTour; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");
/* harmony import */ var _GuidedTour_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./GuidedTour.css */ "../agentic_chat/src/GuidedTour.css");



function GuidedTour({
  steps,
  isActive,
  onComplete,
  onSkip
}) {
  const [currentStep, setCurrentStep] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(0);
  const [tooltipPosition, setTooltipPosition] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({
    top: 0,
    left: 0
  });
  const [highlightPosition, setHighlightPosition] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({
    top: 0,
    left: 0,
    width: 0,
    height: 0
  });
  const calculatePositions = (0,react__WEBPACK_IMPORTED_MODULE_0__.useCallback)(() => {
    if (!isActive || currentStep >= steps.length) return;
    const step = steps[currentStep];

    // Support multiple selectors separated by comma
    const selectors = step.target.split(',').map(s => s.trim());
    let targetElement = null;
    for (const selector of selectors) {
      targetElement = document.querySelector(selector);
      if (targetElement) break;
    }
    if (!targetElement) {
      console.warn(`Tour target not found: ${step.target}`);
      return;
    }
    const rect = targetElement.getBoundingClientRect();
    const padding = step.highlightPadding || 8;

    // Set highlight position
    setHighlightPosition({
      top: rect.top - padding,
      left: rect.left - padding,
      width: rect.width + padding * 2,
      height: rect.height + padding * 2
    });

    // Calculate tooltip position
    const tooltipWidth = 320;
    const tooltipHeight = 200;
    const spacing = 16;
    let top = 0;
    let left = 0;
    const placement = step.placement || "bottom";
    switch (placement) {
      case "top":
        top = rect.top - tooltipHeight - spacing;
        left = rect.left + rect.width / 2 - tooltipWidth / 2;
        break;
      case "bottom":
        top = rect.bottom + spacing;
        left = rect.left + rect.width / 2 - tooltipWidth / 2;
        break;
      case "left":
        top = rect.top + rect.height / 2 - tooltipHeight / 2;
        left = rect.left - tooltipWidth - spacing;
        break;
      case "right":
        top = rect.top + rect.height / 2 - tooltipHeight / 2;
        left = rect.right + spacing;
        break;
    }

    // Ensure tooltip stays within viewport
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    if (left < spacing) left = spacing;
    if (left + tooltipWidth > viewportWidth - spacing) {
      left = viewportWidth - tooltipWidth - spacing;
    }
    if (top < spacing) top = spacing;
    if (top + tooltipHeight > viewportHeight - spacing) {
      top = viewportHeight - tooltipHeight - spacing;
    }
    setTooltipPosition({
      top,
      left
    });

    // Scroll element into view if needed
    targetElement.scrollIntoView({
      behavior: "smooth",
      block: "center"
    });
  }, [isActive, currentStep, steps]);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    if (isActive) {
      calculatePositions();

      // Execute beforeShow callback
      const step = steps[currentStep];
      if (step?.beforeShow) {
        step.beforeShow();
      }

      // Recalculate on window resize
      const handleResize = () => calculatePositions();
      window.addEventListener("resize", handleResize);

      // Small delay to ensure DOM is ready
      const timer = setTimeout(calculatePositions, 100);
      return () => {
        window.removeEventListener("resize", handleResize);
        clearTimeout(timer);
      };
    }
  }, [isActive, currentStep, calculatePositions, steps]);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    // Execute afterShow callback
    const step = steps[currentStep];
    if (step?.afterShow && isActive) {
      step.afterShow();
    }
  }, [currentStep, isActive, steps]);
  const handleNext = () => {
    if (currentStep < steps.length - 1) {
      setCurrentStep(currentStep + 1);
    } else {
      handleComplete();
    }
  };
  const handlePrevious = () => {
    if (currentStep > 0) {
      setCurrentStep(currentStep - 1);
    }
  };
  const handleComplete = () => {
    onComplete();
    setCurrentStep(0);
  };
  const handleSkip = () => {
    onSkip();
    setCurrentStep(0);
  };
  if (!isActive || currentStep >= steps.length) {
    return null;
  }
  const step = steps[currentStep];
  const progress = (currentStep + 1) / steps.length * 100;
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tour-overlay"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tour-highlight",
    style: {
      top: `${highlightPosition.top}px`,
      left: `${highlightPosition.left}px`,
      width: `${highlightPosition.width}px`,
      height: `${highlightPosition.height}px`
    }
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tour-tooltip",
    style: {
      top: `${tooltipPosition.top}px`,
      left: `${tooltipPosition.left}px`
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tour-tooltip-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tour-step-counter"
  }, "Step ", currentStep + 1, " of ", steps.length), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "tour-close-btn",
    onClick: handleSkip,
    title: "Skip tour"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
    size: 16
  }))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tour-tooltip-content"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
    className: "tour-tooltip-title"
  }, step.title), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "tour-tooltip-text"
  }, step.content)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tour-tooltip-footer"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tour-progress-bar"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tour-progress-fill",
    style: {
      width: `${progress}%`
    }
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tour-tooltip-actions"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "tour-btn tour-btn-secondary",
    onClick: handleSkip
  }, "Skip Tour"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tour-navigation"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "tour-btn tour-btn-icon",
    onClick: handlePrevious,
    disabled: currentStep === 0,
    title: "Previous"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronLeft, {
    size: 16
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "tour-btn tour-btn-primary",
    onClick: handleNext
  }, currentStep === steps.length - 1 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Check, {
    size: 16
  }), "Finish") : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, "Next", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronRight, {
    size: 16
  }))))))));
}

/***/ }),

/***/ "../agentic_chat/src/KnowledgeConfig.tsx":
/*!***********************************************!*\
  !*** ../agentic_chat/src/KnowledgeConfig.tsx ***!
  \***********************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ KnowledgeConfig; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");
/* harmony import */ var _ConfigModal_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./ConfigModal.css */ "../agentic_chat/src/ConfigModal.css");



function KnowledgeConfig({
  onClose
}) {
  const [config, setConfig] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({
    sources: [],
    embeddingModel: "text-embedding-3-small",
    chunkSize: 1000,
    chunkOverlap: 200
  });
  const [saveStatus, setSaveStatus] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("idle");
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    loadConfig();
  }, []);
  const loadConfig = async () => {
    try {
      const response = await fetch('/api/config/knowledge');
      if (response.ok) {
        const data = await response.json();
        setConfig(data);
      }
    } catch (error) {
      console.error("Error loading config:", error);
    }
  };
  const saveConfig = async () => {
    setSaveStatus("saving");
    try {
      const response = await fetch('/api/config/knowledge', {
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
  const addSource = () => {
    const newSource = {
      id: Date.now().toString(),
      name: "New Source",
      type: "file",
      enabled: true
    };
    setConfig({
      ...config,
      sources: [...config.sources, newSource]
    });
  };
  const updateSource = (id, updates) => {
    setConfig({
      ...config,
      sources: config.sources.map(source => source.id === id ? {
        ...source,
        ...updates
      } : source)
    });
  };
  const removeSource = id => {
    setConfig({
      ...config,
      sources: config.sources.filter(source => source.id !== id)
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
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h2", null, "Knowledge Configuration"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "config-modal-close",
    onClick: onClose
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
    size: 20
  }))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-content"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Embedding Settings"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-form"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Embedding Model"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
    value: config.embeddingModel,
    onChange: e => setConfig({
      ...config,
      embeddingModel: e.target.value
    })
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "text-embedding-3-small"
  }, "text-embedding-3-small"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "text-embedding-3-large"
  }, "text-embedding-3-large"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "text-embedding-ada-002"
  }, "text-embedding-ada-002"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-row"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Chunk Size"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "number",
    value: config.chunkSize,
    onChange: e => setConfig({
      ...config,
      chunkSize: parseInt(e.target.value)
    }),
    min: "100",
    max: "4000"
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Chunk Overlap"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "number",
    value: config.chunkOverlap,
    onChange: e => setConfig({
      ...config,
      chunkOverlap: parseInt(e.target.value)
    }),
    min: "0",
    max: "1000"
  }))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "section-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Knowledge Sources"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "add-btn",
    onClick: addSource
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Plus, {
    size: 16
  }), "Add Source")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "sources-list"
  }, config.sources.map(source => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: source.id,
    className: "source-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "source-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: source.enabled,
    onChange: e => updateSource(source.id, {
      enabled: e.target.checked
    })
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: source.name,
    onChange: e => updateSource(source.id, {
      name: e.target.value
    }),
    className: "source-name"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
    value: source.type,
    onChange: e => updateSource(source.id, {
      type: e.target.value
    })
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "file"
  }, "File"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "url"
  }, "URL"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "database"
  }, "Database")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "delete-btn",
    onClick: () => removeSource(source.id)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Trash2, {
    size: 16
  }))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "source-details"
  }, source.type === "file" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: source.path || "",
    onChange: e => updateSource(source.id, {
      path: e.target.value
    }),
    placeholder: "File path..."
  })), source.type === "url" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: source.url || "",
    onChange: e => updateSource(source.id, {
      url: e.target.value
    }),
    placeholder: "https://..."
  })))))), config.sources.length === 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "empty-state"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "No knowledge sources configured. Click \"Add Source\" to get started.")))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
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
  }), saveStatus === "idle" && "Save Changes", saveStatus === "saving" && "Saving...", saveStatus === "success" && "Saved!", saveStatus === "error" && "Error!"))));
}

/***/ }),

/***/ "../agentic_chat/src/LeftSidebar.css":
/*!*******************************************!*\
  !*** ../agentic_chat/src/LeftSidebar.css ***!
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
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_LeftSidebar_css__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! !!../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!./LeftSidebar.css */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/LeftSidebar.css");

      
      
      
      
      
      
      
      
      

var options = {};

options.styleTagTransform = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default());
options.setAttributes = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default());
options.insert = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default().bind(null, "head");
options.domAPI = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default());
options.insertStyleElement = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default());

var update = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default()(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_LeftSidebar_css__WEBPACK_IMPORTED_MODULE_6__["default"], options);




       /* unused harmony default export */ var __WEBPACK_DEFAULT_EXPORT__ = (_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_LeftSidebar_css__WEBPACK_IMPORTED_MODULE_6__["default"] && _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_LeftSidebar_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals ? _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_LeftSidebar_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals : undefined);


/***/ }),

/***/ "../agentic_chat/src/LeftSidebar.tsx":
/*!*******************************************!*\
  !*** ../agentic_chat/src/LeftSidebar.tsx ***!
  \*******************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   LeftSidebar: function() { return /* binding */ LeftSidebar; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");
/* harmony import */ var _LeftSidebar_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./LeftSidebar.css */ "../agentic_chat/src/LeftSidebar.css");
/* harmony import */ var _VariablesSidebar__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ./VariablesSidebar */ "../agentic_chat/src/VariablesSidebar.tsx");




const ENABLE_CHAT_HISTORY = false;
const ENABLE_SAVED_FLOWS = false;
function LeftSidebar({
  globalVariables,
  globalMemories,
  variablesHistory,
  selectedAnswerId,
  onSelectAnswer,
  isCollapsed = false,
  activeTab = "conversations",
  onTabChange,
  leftSidebarRef
}) {
  const [isExpanded, setIsExpanded] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(!isCollapsed);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    setIsExpanded(!isCollapsed);
  }, [isCollapsed]);

  // Debug logging for variables
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    console.log('[LeftSidebar] globalVariables updated:', Object.keys(globalVariables).length, 'keys');
    console.log('[LeftSidebar] globalMemories updated:', Object.keys(globalMemories).length, 'categories');
    console.log('[LeftSidebar] variablesHistory updated:', variablesHistory.length, 'items');
  }, [globalVariables, globalMemories, variablesHistory]);
  const [conversations, setConversations] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [selectedConversation, setSelectedConversation] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [savedFlows, setSavedFlows] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [hoveredFlowId, setHoveredFlowId] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [tooltipPosition, setTooltipPosition] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    console.log('[LeftSidebar] Component mounted');
    loadConversations();
    loadSavedFlows();
  }, []);
  const loadConversations = async () => {
    try {
      const response = await fetch('/api/conversations');
      if (response.ok) {
        const data = await response.json();
        setConversations(data);
      }
    } catch (error) {
      console.error("Error loading conversations:", error);
    }
  };
  const createNewConversation = async customTitle => {
    console.log('[LeftSidebar] createNewConversation called with title:', customTitle);
    const newConv = {
      id: `conv-${Date.now()}`,
      title: customTitle || "New Conversation",
      timestamp: Date.now()
    };

    // Always add conversation locally first for immediate UI feedback
    console.log('[LeftSidebar] Adding conversation locally:', newConv);
    setConversations(prevConversations => [newConv, ...prevConversations]);
    setSelectedConversation(newConv.id);

    // Try to sync with API (but don't fail if API is unavailable)
    try {
      const response = await fetch('/api/conversations', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          title: customTitle || "New Conversation",
          timestamp: Date.now()
        })
      });
      if (response.ok) {
        const apiConv = await response.json();
        console.log('[LeftSidebar] API sync successful:', apiConv);
        // Update with API response if needed
        setConversations(prevConversations => prevConversations.map(conv => conv.id === newConv.id ? {
          ...apiConv,
          id: newConv.id
        } : conv));
      } else {
        console.warn('[LeftSidebar] API call failed but local conversation added');
      }
    } catch (error) {
      console.warn('[LeftSidebar] API error but local conversation added:', error);
    }
  };

  // Function to add a conversation programmatically
  const addConversation = react__WEBPACK_IMPORTED_MODULE_0___default().useCallback(title => {
    createNewConversation(title);
  }, []);

  // Expose addConversation function via ref
  react__WEBPACK_IMPORTED_MODULE_0___default().useImperativeHandle(leftSidebarRef, () => ({
    addConversation
  }), [addConversation]);

  // Debug: log when ref is set
  react__WEBPACK_IMPORTED_MODULE_0___default().useEffect(() => {
    if (leftSidebarRef?.current) {
      console.log('[LeftSidebar] Ref is set, addConversation available');
    }
  }, [leftSidebarRef?.current]);
  const deleteConversation = async (id, e) => {
    e.stopPropagation();
    try {
      const response = await fetch(`/api/conversations/${id}`, {
        method: 'DELETE'
      });
      if (response.ok) {
        setConversations(conversations.filter(c => c.id !== id));
        if (selectedConversation === id) {
          setSelectedConversation(null);
        }
      }
    } catch (error) {
      console.error("Error deleting conversation:", error);
    }
  };
  const loadSavedFlows = async () => {
    try {
      const response = await fetch('/api/flows');
      if (response.ok) {
        const data = await response.json();
        setSavedFlows(data.flows || []);
      } else {
        setSavedFlows([]);
      }
    } catch (error) {
      console.error("Error loading saved flows:", error);
      setSavedFlows([]);
    }
  };
  const formatDate = timestamp => {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const minutes = Math.floor(diff / (1000 * 60));
    const hours = Math.floor(diff / (1000 * 60 * 60));
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
    if (minutes < 1) return "1 minute ago";
    if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
    if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
    if (days === 1) return "now";
    if (days < 7) return `${days} days ago`;
    return date.toLocaleDateString();
  };
  if (!isExpanded) {
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "left-sidebar-floating-toggle",
      onClick: () => setIsExpanded(true)
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronRight, {
      size: 20
    }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "sidebar-floating-count"
    }, conversations.length));
  }
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: `left-sidebar ${isExpanded ? "expanded" : "collapsed"}`
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "left-sidebar-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "left-sidebar-tabs"
  }, ENABLE_CHAT_HISTORY && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `sidebar-tab ${activeTab === "conversations" ? "active" : ""}`,
    onClick: () => onTabChange ? onTabChange("conversations") : null
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.MessageSquare, {
    size: 16
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Chats")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `sidebar-tab ${activeTab === "variables" ? "active" : ""}`,
    onClick: () => onTabChange ? onTabChange("variables") : null
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Database, {
    size: 16
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Variables"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "sidebar-tab-info-tooltip-wrapper",
    onClick: e => e.stopPropagation(),
    onMouseEnter: e => e.stopPropagation()
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.HelpCircle, {
    size: 14,
    className: "sidebar-info-icon"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "sidebar-tab-info-tooltip"
  }, "Variables are the results from task execution. Ask any question about the variables and CUGA will respond."))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `sidebar-tab ${activeTab === "memories" ? "active" : ""}`,
    onClick: () => onTabChange ? onTabChange("memories") : null
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Brain, {
    size: 16
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Memories"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "sidebar-tab-info-tooltip-wrapper",
    onClick: e => e.stopPropagation(),
    onMouseEnter: e => e.stopPropagation()
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.HelpCircle, {
    size: 14,
    className: "sidebar-info-icon"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "sidebar-tab-info-tooltip"
  }, "Memories are extracted user facts from prior turns. Click this tab to browse each stored memory item."))), ENABLE_SAVED_FLOWS && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `sidebar-tab ${activeTab === "savedflows" ? "active" : ""}`,
    onClick: () => onTabChange ? onTabChange("savedflows") : null
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Workflow, {
    size: 16
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Saved Flows"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "left-sidebar-toggle",
    onClick: () => setIsExpanded(false)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronLeft, {
    size: 18
  }))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "left-sidebar-content"
  }, ENABLE_CHAT_HISTORY && activeTab === "conversations" ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "conversations-actions"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "new-conversation-btn",
    onClick: () => createNewConversation()
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Plus, {
    size: 16
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "New Chat"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "conversations-list"
  }, conversations.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "empty-state"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.MessageSquare, {
    size: 32
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "No conversations yet"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Start a new chat to begin")) : conversations.map(conv => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: conv.id,
    className: `conversation-item ${selectedConversation === conv.id ? "selected" : ""}`,
    onClick: () => setSelectedConversation(conv.id)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "conversation-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.MessageSquare, {
    size: 14
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "conversation-title"
  }, conv.title), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "delete-conversation-btn",
    onClick: e => deleteConversation(conv.id, e)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Trash2, {
    size: 14
  }))), conv.preview && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "conversation-preview"
  }, conv.preview), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "conversation-date"
  }, formatDate(conv.timestamp)))))) : activeTab === "variables" ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "variables-wrapper"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_VariablesSidebar__WEBPACK_IMPORTED_MODULE_3__["default"], {
    variables: globalVariables,
    memories: globalMemories,
    history: variablesHistory,
    selectedAnswerId: selectedAnswerId,
    onSelectAnswer: onSelectAnswer,
    mode: "variables"
  })) : activeTab === "memories" ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "variables-wrapper"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_VariablesSidebar__WEBPACK_IMPORTED_MODULE_3__["default"], {
    variables: globalVariables,
    memories: globalMemories,
    history: variablesHistory,
    selectedAnswerId: selectedAnswerId,
    onSelectAnswer: onSelectAnswer,
    mode: "memories"
  })) : ENABLE_SAVED_FLOWS && activeTab === "savedflows" ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "conversations-list"
  }, savedFlows.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "empty-state"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Workflow, {
    size: 32
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "No saved flows yet"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Saved flows will appear here")) : savedFlows.map(flow => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: flow.id,
    className: "conversation-item flow-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "conversation-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Workflow, {
    size: 14
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "conversation-title"
  }, flow.name), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flow-info-icon-wrapper",
    onMouseEnter: e => {
      const rect = e.currentTarget.getBoundingClientRect();
      const viewportWidth = window.innerWidth;
      const tooltipWidth = 300;
      const tooltipHeight = 120; // approximate height

      // Position tooltip to the right if there's space, otherwise to the left
      let left = rect.right + 12;
      if (left + tooltipWidth > viewportWidth) {
        left = rect.left - tooltipWidth - 12;
      }

      // Ensure tooltip doesn't go off the top or bottom
      let top = rect.top;
      if (top + tooltipHeight > window.innerHeight) {
        top = window.innerHeight - tooltipHeight - 10;
      }
      if (top < 10) {
        top = 10;
      }
      setTooltipPosition({
        top,
        left
      });
      setHoveredFlowId(flow.id);
    },
    onMouseLeave: e => {
      // Only hide if the mouse is not entering the tooltip area
      const tooltip = document.querySelector('.flow-info-tooltip');
      if (tooltip) {
        const tooltipRect = tooltip.getBoundingClientRect();
        const mouseX = e.clientX;
        const mouseY = e.clientY;

        // Check if mouse is within tooltip bounds
        if (mouseX >= tooltipRect.left && mouseX <= tooltipRect.right && mouseY >= tooltipRect.top && mouseY <= tooltipRect.bottom) {
          return; // Keep tooltip visible
        }
      }
      setHoveredFlowId(null);
      setTooltipPosition(null);
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Info, {
    size: 14,
    className: "flow-info-icon"
  }))), flow.description && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "conversation-preview"
  }, flow.description), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flow-parameters"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flow-function-signature"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, flow.name, "("), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flow-params-list"
  }, flow.parameters.map((param, idx) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: idx,
    className: "flow-param"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", {
    className: "param-name"
  }, param.name), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "param-type"
  }, ": ", param.type), !param.required && param.default !== undefined && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "param-default"
  }, " = ", JSON.stringify(param.default)), param.required && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "param-required"
  }, "*"), idx < flow.parameters.length - 1 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, ",")))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, ")"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "conversation-date"
  }, formatDate(flow.timestamp)))))) : null), hoveredFlowId && tooltipPosition && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flow-info-tooltip",
    style: {
      top: `${tooltipPosition.top}px`,
      left: `${tooltipPosition.left}px`
    },
    onMouseEnter: () => {
      // Keep tooltip visible when mouse enters it
      setHoveredFlowId(hoveredFlowId);
    },
    onMouseLeave: () => {
      // Hide tooltip when mouse leaves
      setHoveredFlowId(null);
      setTooltipPosition(null);
    }
  }, "Saved from a previous conversation where you completed a similar task using CRM, filesystem, and email tools. Reuse this flow to repeat the same pattern with different parameters."));
}

/***/ }),

/***/ "../agentic_chat/src/MemoryConfig.tsx":
/*!********************************************!*\
  !*** ../agentic_chat/src/MemoryConfig.tsx ***!
  \********************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ MemoryConfig; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");
/* harmony import */ var _ConfigModal_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./ConfigModal.css */ "../agentic_chat/src/ConfigModal.css");



function MemoryConfig({
  onClose
}) {
  const [config, setConfig] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({
    enableMemory: true,
    disableMemory: false,
    memoryType: "both",
    contextWindow: 4096,
    maxMemoryItems: 100,
    semanticSearch: true,
    autoSummarization: true,
    factStorage: false,
    learningFromFailures: false,
    blockedMemoryItems: [],
    saveAndReuse: {
      enabled: false,
      autoGeneralize: false,
      minSuccessfulRuns: 3,
      requireApproval: true,
      savedTrajectories: []
    }
  });
  const [saveStatus, setSaveStatus] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("idle");
  const [newBlockedItem, setNewBlockedItem] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("");
  const [expandedTrajectory, setExpandedTrajectory] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    loadConfig();
  }, []);
  const loadConfig = async () => {
    try {
      const response = await fetch('/api/config/memory');
      if (response.ok) {
        const data = await response.json();
        setConfig({
          enableMemory: data.enableMemory ?? true,
          disableMemory: data.disableMemory ?? false,
          memoryType: data.memoryType ?? "both",
          contextWindow: data.contextWindow ?? 4096,
          maxMemoryItems: data.maxMemoryItems ?? 100,
          semanticSearch: data.semanticSearch ?? true,
          autoSummarization: data.autoSummarization ?? true,
          factStorage: data.factStorage ?? false,
          learningFromFailures: data.learningFromFailures ?? false,
          blockedMemoryItems: data.blockedMemoryItems ?? [],
          saveAndReuse: data.saveAndReuse ?? {
            enabled: false,
            autoGeneralize: false,
            minSuccessfulRuns: 3,
            requireApproval: true,
            savedTrajectories: []
          }
        });
      }
    } catch (error) {
      console.error("Error loading config:", error);
    }
  };
  const saveConfig = async () => {
    setSaveStatus("saving");
    try {
      const response = await fetch('/api/config/memory', {
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
  const addBlockedItem = () => {
    if (newBlockedItem.trim() && !config.blockedMemoryItems.includes(newBlockedItem.trim())) {
      setConfig({
        ...config,
        blockedMemoryItems: [...config.blockedMemoryItems, newBlockedItem.trim()]
      });
      setNewBlockedItem("");
    }
  };
  const removeBlockedItem = item => {
    setConfig({
      ...config,
      blockedMemoryItems: config.blockedMemoryItems.filter(i => i !== item)
    });
  };
  const toggleTrajectory = id => {
    setConfig({
      ...config,
      saveAndReuse: {
        ...config.saveAndReuse,
        savedTrajectories: config.saveAndReuse.savedTrajectories.map(t => t.id === id ? {
          ...t,
          enabled: !t.enabled
        } : t)
      }
    });
  };
  const deleteTrajectory = id => {
    setConfig({
      ...config,
      saveAndReuse: {
        ...config.saveAndReuse,
        savedTrajectories: config.saveAndReuse.savedTrajectories.filter(t => t.id !== id)
      }
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
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h2", null, "Memory Configuration"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "config-modal-close",
    onClick: onClose
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
    size: 20
  }))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-content"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Memory Settings"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-form"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
    className: "checkbox-label"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: config.disableMemory,
    onChange: e => {
      const disabled = e.target.checked;
      setConfig({
        ...config,
        disableMemory: disabled,
        enableMemory: disabled ? false : config.enableMemory
      });
    }
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Disable Memory Completely")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Turn off all memory functionality")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
    className: "checkbox-label"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: config.enableMemory,
    onChange: e => setConfig({
      ...config,
      enableMemory: e.target.checked
    }),
    disabled: config.disableMemory
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Enable Memory System")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Allow the agent to remember context across conversations")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Max Memory Items"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "number",
    value: config.maxMemoryItems,
    onChange: e => setConfig({
      ...config,
      maxMemoryItems: parseInt(e.target.value)
    }),
    min: "10",
    max: "1000",
    disabled: !config.enableMemory || config.disableMemory
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Maximum number of memory items to store")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
    className: "checkbox-label"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: config.semanticSearch,
    onChange: e => setConfig({
      ...config,
      semanticSearch: e.target.checked
    }),
    disabled: !config.enableMemory || config.disableMemory
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Enable Semantic Search")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Use embeddings to find relevant memories")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
    className: "checkbox-label"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: config.autoSummarization,
    onChange: e => setConfig({
      ...config,
      autoSummarization: e.target.checked
    }),
    disabled: !config.enableMemory || config.disableMemory
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Auto-summarization")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Automatically summarize long conversations")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
    className: "checkbox-label"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: config.factStorage,
    onChange: e => setConfig({
      ...config,
      factStorage: e.target.checked
    }),
    disabled: !config.enableMemory || config.disableMemory
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Enable Fact Storage and Retrieval")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Store and retrieve important facts like IDs, key values, and persistent information")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
    className: "checkbox-label"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: config.learningFromFailures,
    onChange: e => setConfig({
      ...config,
      learningFromFailures: e.target.checked
    }),
    disabled: !config.enableMemory || config.disableMemory
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Enable Learning from Failures and Tip Injection")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Learn from past failures and analyze trajectories to improve future task performance")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Blocked Memory Items"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      gap: "8px",
      alignItems: "center"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: newBlockedItem,
    onChange: e => setNewBlockedItem(e.target.value),
    onKeyPress: e => {
      if (e.key === "Enter") {
        e.preventDefault();
        addBlockedItem();
      }
    },
    placeholder: "e.g., passwords, secrets",
    disabled: config.disableMemory,
    style: {
      width: "200px",
      padding: "4px 8px",
      fontSize: "12px"
    }
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "add-small-btn",
    onClick: addBlockedItem,
    disabled: config.disableMemory || !newBlockedItem.trim()
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Plus, {
    size: 12
  }), "Add"))), config.blockedMemoryItems.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policies-empty"
  }, "No blocked items. Add items that the agent should never remember.") : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policies-list"
  }, config.blockedMemoryItems.map((item, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    className: "policy-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    style: {
      flex: 1,
      padding: "8px",
      fontSize: "13px"
    }
  }, item), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "remove-btn",
    onClick: () => removeBlockedItem(item),
    disabled: config.disableMemory
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
    size: 14
  }))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Items the agent is not allowed to remember (e.g., sensitive information, passwords)")))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Save & Reuse Trajectories"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-form"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
    className: "checkbox-label"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: config.saveAndReuse.enabled,
    onChange: e => setConfig({
      ...config,
      saveAndReuse: {
        ...config.saveAndReuse,
        enabled: e.target.checked
      }
    }),
    disabled: config.disableMemory
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Enable Save & Reuse")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Allow agent to save successful task trajectories as reusable tools")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
    className: "checkbox-label"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: config.saveAndReuse.autoGeneralize,
    onChange: e => setConfig({
      ...config,
      saveAndReuse: {
        ...config.saveAndReuse,
        autoGeneralize: e.target.checked
      }
    }),
    disabled: !config.saveAndReuse.enabled || config.disableMemory
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Auto-generalize Trajectories")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Automatically identify patterns and create reusable tools from successful tasks")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Min. Successful Runs Before Saving"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "number",
    value: config.saveAndReuse.minSuccessfulRuns,
    onChange: e => setConfig({
      ...config,
      saveAndReuse: {
        ...config.saveAndReuse,
        minSuccessfulRuns: parseInt(e.target.value)
      }
    }),
    min: "1",
    max: "10",
    disabled: !config.saveAndReuse.enabled || config.disableMemory
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Number of successful executions before suggesting trajectory as reusable tool")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
    className: "checkbox-label"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: config.saveAndReuse.requireApproval,
    onChange: e => setConfig({
      ...config,
      saveAndReuse: {
        ...config.saveAndReuse,
        requireApproval: e.target.checked
      }
    }),
    disabled: !config.saveAndReuse.enabled || config.disableMemory
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Require User Approval")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Ask for permission before saving new trajectories as tools")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group",
    style: {
      marginTop: "24px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
    style: {
      fontSize: "14px",
      fontWeight: 600,
      marginBottom: "12px",
      display: "block"
    }
  }, "Saved Trajectories (", config.saveAndReuse.savedTrajectories.length, ")"), config.saveAndReuse.savedTrajectories.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policies-empty"
  }, "No saved trajectories yet. When enabled, successful task patterns will appear here.") : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policies-list",
    style: {
      maxHeight: "400px",
      overflowY: "auto"
    }
  }, config.saveAndReuse.savedTrajectories.map(trajectory => {
    const isExpanded = expandedTrajectory === trajectory.id;
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      key: trajectory.id,
      className: "policy-item",
      style: {
        flexDirection: "column",
        alignItems: "stretch"
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        display: "flex",
        alignItems: "center",
        gap: "8px",
        padding: "8px"
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
      type: "checkbox",
      checked: trajectory.enabled,
      onChange: () => toggleTrajectory(trajectory.id),
      disabled: !config.saveAndReuse.enabled || config.disableMemory,
      style: {
        cursor: "pointer"
      }
    }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        flex: 1,
        cursor: "pointer"
      },
      onClick: () => setExpandedTrajectory(isExpanded ? null : trajectory.id)
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        fontWeight: 600,
        fontSize: "13px",
        color: "#1e293b"
      }
    }, trajectory.name), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        fontSize: "11px",
        color: "#64748b",
        marginTop: "2px"
      }
    }, trajectory.description)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        display: "flex",
        gap: "4px",
        alignItems: "center",
        fontSize: "11px",
        color: "#64748b"
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Confidence: ", trajectory.confidence, "%"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "\u2022"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, trajectory.parameters.length, " params")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
      className: "remove-btn",
      onClick: () => deleteTrajectory(trajectory.id),
      disabled: !config.saveAndReuse.enabled || config.disableMemory
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Trash2, {
      size: 14
    }))), isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        padding: "12px",
        background: "#f8fafc",
        borderTop: "1px solid #e5e7eb",
        fontSize: "12px"
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        marginBottom: "12px"
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", {
      style: {
        color: "#475569",
        display: "block",
        marginBottom: "6px"
      }
    }, "Parameters:"), trajectory.parameters.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        color: "#94a3b8",
        fontStyle: "italic"
      }
    }, "No parameters") : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        display: "flex",
        flexDirection: "column",
        gap: "6px"
      }
    }, trajectory.parameters.map((param, idx) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      key: idx,
      style: {
        background: "white",
        padding: "6px 8px",
        borderRadius: "4px",
        border: "1px solid #e5e7eb"
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", {
      style: {
        color: "#667eea",
        fontWeight: 600,
        fontSize: "11px"
      }
    }, param.name), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      style: {
        color: "#64748b"
      }
    }, ": ", param.type), param.required && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      style: {
        color: "#ef4444",
        marginLeft: "4px"
      }
    }, "*"), param.description && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        color: "#64748b",
        fontSize: "11px",
        marginTop: "2px"
      }
    }, param.description))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        display: "flex",
        gap: "16px",
        fontSize: "11px",
        color: "#64748b"
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Min interactions: ", trajectory.minInteractions), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "\u2022"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Created: ", new Date(trajectory.createdAt).toLocaleDateString()))));
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", {
    style: {
      display: "block",
      marginTop: "8px"
    }
  }, "Trajectories are automatically learned from successful task completions"))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
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
  }), saveStatus === "idle" && "Save Changes", saveStatus === "saving" && "Saving...", saveStatus === "success" && "Saved!", saveStatus === "error" && "Error!"))));
}

/***/ }),

/***/ "../agentic_chat/src/ModelConfig.tsx":
/*!*******************************************!*\
  !*** ../agentic_chat/src/ModelConfig.tsx ***!
  \*******************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ ModelConfig; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");
/* harmony import */ var _ConfigModal_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./ConfigModal.css */ "../agentic_chat/src/ConfigModal.css");



function ModelConfig({
  onClose
}) {
  const [config, setConfig] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({
    provider: "watsonx",
    model: "openai/gpt-oss-120b",
    temperature: 0.7,
    maxTokens: 4096,
    topP: 1.0
  });
  const [saveStatus, setSaveStatus] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("idle");
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    loadConfig();
  }, []);
  const loadConfig = async () => {
    try {
      const response = await fetch('/api/config/model');
      if (response.ok) {
        const data = await response.json();
        setConfig(data);
      }
    } catch (error) {
      console.error("Error loading config:", error);
    }
  };
  const saveConfig = async () => {
    setSaveStatus("saving");
    try {
      const response = await fetch('/api/config/model', {
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
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-overlay",
    onClick: onClose
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal",
    onClick: e => e.stopPropagation()
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h2", null, "Model Configuration"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "config-modal-close",
    onClick: onClose
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
    size: 20
  }))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-content"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Language Model Settings"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-form"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Provider"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
    value: config.provider,
    onChange: e => setConfig({
      ...config,
      provider: e.target.value
    })
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "anthropic"
  }, "Anthropic"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "openai"
  }, "OpenAI"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "azure"
  }, "Azure OpenAI"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "watsonx"
  }, "IBM watsonx"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "ollama"
  }, "Ollama"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Model"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: config.model,
    onChange: e => setConfig({
      ...config,
      model: e.target.value
    }),
    placeholder: "e.g., claude-3-5-sonnet-20241022"
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Temperature: ", config.temperature), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "range",
    min: "0",
    max: "2",
    step: "0.1",
    value: config.temperature,
    onChange: e => setConfig({
      ...config,
      temperature: parseFloat(e.target.value)
    })
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Controls randomness: 0 = focused, 2 = creative")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Max Tokens"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "number",
    value: config.maxTokens,
    onChange: e => setConfig({
      ...config,
      maxTokens: parseInt(e.target.value)
    }),
    min: "1",
    max: "200000"
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Top P: ", config.topP), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "range",
    min: "0",
    max: "1",
    step: "0.01",
    value: config.topP,
    onChange: e => setConfig({
      ...config,
      topP: parseFloat(e.target.value)
    })
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Nucleus sampling threshold")), config.provider !== "ollama" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "API Key"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "password",
    value: config.apiKey || "",
    onChange: e => setConfig({
      ...config,
      apiKey: e.target.value
    }),
    placeholder: "Enter API key..."
  }))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
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
  }), saveStatus === "idle" && "Save Changes", saveStatus === "saving" && "Saving...", saveStatus === "success" && "Saved!", saveStatus === "error" && "Error!"))));
}

/***/ }),

/***/ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/DebugPanel.css":
/*!*************************************************************************************************************************************!*\
  !*** ../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/DebugPanel.css ***!
  \*************************************************************************************************************************************/
/***/ (function(module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__);
// Imports


var ___CSS_LOADER_EXPORT___ = _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default()((_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default()));
// Module
___CSS_LOADER_EXPORT___.push([module.id, ".debug-panel-container {\n  position: fixed;\n  bottom: 20px;\n  right: 100px;\n  z-index: 1000;\n  font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif;\n}\n\n.debug-panel-toggle {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  padding: 8px 12px;\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n  border: none;\n  border-radius: 8px;\n  cursor: pointer;\n  font-size: 13px;\n  font-weight: 500;\n  box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);\n  transition: all 0.2s ease;\n}\n\n.debug-panel-toggle:hover {\n  transform: translateY(-1px);\n  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);\n}\n\n.debug-panel-content {\n  position: absolute;\n  bottom: 50px;\n  right: 0;\n  width: 500px;\n  max-height: 600px;\n  background: white;\n  border-radius: 12px;\n  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);\n  overflow: hidden;\n  display: flex;\n  flex-direction: column;\n}\n\n.debug-panel-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 16px;\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n}\n\n.debug-panel-header h3 {\n  margin: 0;\n  font-size: 16px;\n  font-weight: 600;\n}\n\n.debug-panel-actions {\n  display: flex;\n  align-items: center;\n  gap: 12px;\n}\n\n.debug-action-btn {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  width: 28px;\n  height: 28px;\n  background: rgba(255, 255, 255, 0.2);\n  border: none;\n  border-radius: 6px;\n  color: white;\n  cursor: pointer;\n  transition: all 0.2s ease;\n}\n\n.debug-action-btn:hover:not(:disabled) {\n  background: rgba(255, 255, 255, 0.3);\n}\n\n.debug-action-btn:disabled {\n  opacity: 0.5;\n  cursor: not-allowed;\n}\n\n.debug-action-btn .spinning {\n  animation: spin 1s linear infinite;\n}\n\n@keyframes spin {\n  from {\n    transform: rotate(0deg);\n  }\n  to {\n    transform: rotate(360deg);\n  }\n}\n\n.debug-auto-refresh {\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  font-size: 12px;\n  cursor: pointer;\n}\n\n.debug-auto-refresh input[type=\"checkbox\"] {\n  cursor: pointer;\n}\n\n.debug-section {\n  padding: 16px;\n  border-bottom: 1px solid #e5e7eb;\n}\n\n.debug-section:last-child {\n  border-bottom: none;\n}\n\n.debug-section-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  margin-bottom: 12px;\n}\n\n.debug-section-header strong {\n  font-size: 14px;\n  font-weight: 600;\n  color: #374151;\n}\n\n.debug-copy-btn {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  width: 24px;\n  height: 24px;\n  background: #f3f4f6;\n  border: none;\n  border-radius: 4px;\n  cursor: pointer;\n  transition: all 0.2s ease;\n  color: #6b7280;\n}\n\n.debug-copy-btn:hover {\n  background: #e5e7eb;\n  color: #374151;\n}\n\n.debug-thread-id {\n  font-family: \"Monaco\", \"Menlo\", \"Courier New\", monospace;\n  font-size: 12px;\n  padding: 10px;\n  background: #f9fafb;\n  border: 1px solid #e5e7eb;\n  border-radius: 6px;\n  word-break: break-all;\n  color: #111827;\n}\n\n.debug-state-content {\n  display: flex;\n  flex-direction: column;\n  gap: 12px;\n}\n\n.debug-state-item {\n  display: flex;\n  flex-direction: column;\n  gap: 4px;\n}\n\n.debug-state-item.debug-variables {\n  margin-top: 8px;\n}\n\n.debug-label {\n  font-size: 12px;\n  font-weight: 600;\n  color: #6b7280;\n  text-transform: uppercase;\n  letter-spacing: 0.5px;\n}\n\n.debug-value {\n  font-size: 13px;\n  color: #111827;\n  word-break: break-word;\n}\n\n.debug-json {\n  font-family: \"Monaco\", \"Menlo\", \"Courier New\", monospace;\n  font-size: 11px;\n  padding: 12px;\n  background: #f9fafb;\n  border: 1px solid #e5e7eb;\n  border-radius: 6px;\n  overflow-x: auto;\n  max-height: 300px;\n  overflow-y: auto;\n  margin: 0;\n  color: #111827;\n  line-height: 1.5;\n}\n\n.debug-loading {\n  padding: 12px;\n  text-align: center;\n  color: #6b7280;\n  font-size: 13px;\n}\n\n.debug-error {\n  padding: 12px;\n  background: #fef2f2;\n  border: 1px solid #fecaca;\n  border-radius: 6px;\n  color: #dc2626;\n  font-size: 13px;\n}\n\n", "",{"version":3,"sources":["webpack://./../agentic_chat/src/DebugPanel.css"],"names":[],"mappings":"AAAA;EACE,eAAe;EACf,YAAY;EACZ,YAAY;EACZ,aAAa;EACb,8EAA8E;AAChF;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,QAAQ;EACR,iBAAiB;EACjB,6DAA6D;EAC7D,YAAY;EACZ,YAAY;EACZ,kBAAkB;EAClB,eAAe;EACf,eAAe;EACf,gBAAgB;EAChB,8CAA8C;EAC9C,yBAAyB;AAC3B;;AAEA;EACE,2BAA2B;EAC3B,+CAA+C;AACjD;;AAEA;EACE,kBAAkB;EAClB,YAAY;EACZ,QAAQ;EACR,YAAY;EACZ,iBAAiB;EACjB,iBAAiB;EACjB,mBAAmB;EACnB,0CAA0C;EAC1C,gBAAgB;EAChB,aAAa;EACb,sBAAsB;AACxB;;AAEA;EACE,aAAa;EACb,8BAA8B;EAC9B,mBAAmB;EACnB,aAAa;EACb,6DAA6D;EAC7D,YAAY;AACd;;AAEA;EACE,SAAS;EACT,eAAe;EACf,gBAAgB;AAClB;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,SAAS;AACX;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,uBAAuB;EACvB,WAAW;EACX,YAAY;EACZ,oCAAoC;EACpC,YAAY;EACZ,kBAAkB;EAClB,YAAY;EACZ,eAAe;EACf,yBAAyB;AAC3B;;AAEA;EACE,oCAAoC;AACtC;;AAEA;EACE,YAAY;EACZ,mBAAmB;AACrB;;AAEA;EACE,kCAAkC;AACpC;;AAEA;EACE;IACE,uBAAuB;EACzB;EACA;IACE,yBAAyB;EAC3B;AACF;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,QAAQ;EACR,eAAe;EACf,eAAe;AACjB;;AAEA;EACE,eAAe;AACjB;;AAEA;EACE,aAAa;EACb,gCAAgC;AAClC;;AAEA;EACE,mBAAmB;AACrB;;AAEA;EACE,aAAa;EACb,8BAA8B;EAC9B,mBAAmB;EACnB,mBAAmB;AACrB;;AAEA;EACE,eAAe;EACf,gBAAgB;EAChB,cAAc;AAChB;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,uBAAuB;EACvB,WAAW;EACX,YAAY;EACZ,mBAAmB;EACnB,YAAY;EACZ,kBAAkB;EAClB,eAAe;EACf,yBAAyB;EACzB,cAAc;AAChB;;AAEA;EACE,mBAAmB;EACnB,cAAc;AAChB;;AAEA;EACE,wDAAwD;EACxD,eAAe;EACf,aAAa;EACb,mBAAmB;EACnB,yBAAyB;EACzB,kBAAkB;EAClB,qBAAqB;EACrB,cAAc;AAChB;;AAEA;EACE,aAAa;EACb,sBAAsB;EACtB,SAAS;AACX;;AAEA;EACE,aAAa;EACb,sBAAsB;EACtB,QAAQ;AACV;;AAEA;EACE,eAAe;AACjB;;AAEA;EACE,eAAe;EACf,gBAAgB;EAChB,cAAc;EACd,yBAAyB;EACzB,qBAAqB;AACvB;;AAEA;EACE,eAAe;EACf,cAAc;EACd,sBAAsB;AACxB;;AAEA;EACE,wDAAwD;EACxD,eAAe;EACf,aAAa;EACb,mBAAmB;EACnB,yBAAyB;EACzB,kBAAkB;EAClB,gBAAgB;EAChB,iBAAiB;EACjB,gBAAgB;EAChB,SAAS;EACT,cAAc;EACd,gBAAgB;AAClB;;AAEA;EACE,aAAa;EACb,kBAAkB;EAClB,cAAc;EACd,eAAe;AACjB;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,yBAAyB;EACzB,kBAAkB;EAClB,cAAc;EACd,eAAe;AACjB","sourcesContent":[".debug-panel-container {\n  position: fixed;\n  bottom: 20px;\n  right: 100px;\n  z-index: 1000;\n  font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif;\n}\n\n.debug-panel-toggle {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  padding: 8px 12px;\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n  border: none;\n  border-radius: 8px;\n  cursor: pointer;\n  font-size: 13px;\n  font-weight: 500;\n  box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);\n  transition: all 0.2s ease;\n}\n\n.debug-panel-toggle:hover {\n  transform: translateY(-1px);\n  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);\n}\n\n.debug-panel-content {\n  position: absolute;\n  bottom: 50px;\n  right: 0;\n  width: 500px;\n  max-height: 600px;\n  background: white;\n  border-radius: 12px;\n  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);\n  overflow: hidden;\n  display: flex;\n  flex-direction: column;\n}\n\n.debug-panel-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 16px;\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n}\n\n.debug-panel-header h3 {\n  margin: 0;\n  font-size: 16px;\n  font-weight: 600;\n}\n\n.debug-panel-actions {\n  display: flex;\n  align-items: center;\n  gap: 12px;\n}\n\n.debug-action-btn {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  width: 28px;\n  height: 28px;\n  background: rgba(255, 255, 255, 0.2);\n  border: none;\n  border-radius: 6px;\n  color: white;\n  cursor: pointer;\n  transition: all 0.2s ease;\n}\n\n.debug-action-btn:hover:not(:disabled) {\n  background: rgba(255, 255, 255, 0.3);\n}\n\n.debug-action-btn:disabled {\n  opacity: 0.5;\n  cursor: not-allowed;\n}\n\n.debug-action-btn .spinning {\n  animation: spin 1s linear infinite;\n}\n\n@keyframes spin {\n  from {\n    transform: rotate(0deg);\n  }\n  to {\n    transform: rotate(360deg);\n  }\n}\n\n.debug-auto-refresh {\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  font-size: 12px;\n  cursor: pointer;\n}\n\n.debug-auto-refresh input[type=\"checkbox\"] {\n  cursor: pointer;\n}\n\n.debug-section {\n  padding: 16px;\n  border-bottom: 1px solid #e5e7eb;\n}\n\n.debug-section:last-child {\n  border-bottom: none;\n}\n\n.debug-section-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  margin-bottom: 12px;\n}\n\n.debug-section-header strong {\n  font-size: 14px;\n  font-weight: 600;\n  color: #374151;\n}\n\n.debug-copy-btn {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  width: 24px;\n  height: 24px;\n  background: #f3f4f6;\n  border: none;\n  border-radius: 4px;\n  cursor: pointer;\n  transition: all 0.2s ease;\n  color: #6b7280;\n}\n\n.debug-copy-btn:hover {\n  background: #e5e7eb;\n  color: #374151;\n}\n\n.debug-thread-id {\n  font-family: \"Monaco\", \"Menlo\", \"Courier New\", monospace;\n  font-size: 12px;\n  padding: 10px;\n  background: #f9fafb;\n  border: 1px solid #e5e7eb;\n  border-radius: 6px;\n  word-break: break-all;\n  color: #111827;\n}\n\n.debug-state-content {\n  display: flex;\n  flex-direction: column;\n  gap: 12px;\n}\n\n.debug-state-item {\n  display: flex;\n  flex-direction: column;\n  gap: 4px;\n}\n\n.debug-state-item.debug-variables {\n  margin-top: 8px;\n}\n\n.debug-label {\n  font-size: 12px;\n  font-weight: 600;\n  color: #6b7280;\n  text-transform: uppercase;\n  letter-spacing: 0.5px;\n}\n\n.debug-value {\n  font-size: 13px;\n  color: #111827;\n  word-break: break-word;\n}\n\n.debug-json {\n  font-family: \"Monaco\", \"Menlo\", \"Courier New\", monospace;\n  font-size: 11px;\n  padding: 12px;\n  background: #f9fafb;\n  border: 1px solid #e5e7eb;\n  border-radius: 6px;\n  overflow-x: auto;\n  max-height: 300px;\n  overflow-y: auto;\n  margin: 0;\n  color: #111827;\n  line-height: 1.5;\n}\n\n.debug-loading {\n  padding: 12px;\n  text-align: center;\n  color: #6b7280;\n  font-size: 13px;\n}\n\n.debug-error {\n  padding: 12px;\n  background: #fef2f2;\n  border: 1px solid #fecaca;\n  border-radius: 6px;\n  color: #dc2626;\n  font-size: 13px;\n}\n\n"],"sourceRoot":""}]);
// Exports
/* harmony default export */ __webpack_exports__["default"] = (___CSS_LOADER_EXPORT___);


/***/ }),

/***/ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/FileAutocomplete.css":
/*!*******************************************************************************************************************************************!*\
  !*** ../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/FileAutocomplete.css ***!
  \*******************************************************************************************************************************************/
/***/ (function(module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__);
// Imports


var ___CSS_LOADER_EXPORT___ = _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default()((_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default()));
// Module
___CSS_LOADER_EXPORT___.push([module.id, ".file-autocomplete {\n  position: fixed;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.15);\n  z-index: 99999;\n  min-width: 320px;\n  max-width: 500px;\n  animation: slideUpFade 0.2s ease;\n  pointer-events: auto;\n}\n\n@keyframes slideUpFade {\n  from {\n    opacity: 0;\n    transform: translateY(10px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n.file-autocomplete-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 8px 12px;\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n  font-size: 11px;\n  font-weight: 600;\n  border-radius: 8px 8px 0 0;\n  text-transform: uppercase;\n  letter-spacing: 0.5px;\n}\n\n.file-count {\n  background: rgba(255, 255, 255, 0.2);\n  padding: 2px 6px;\n  border-radius: 10px;\n  font-size: 10px;\n}\n\n.file-autocomplete-list {\n  max-height: 400px;\n  overflow-y: auto;\n  padding: 4px;\n}\n\n.file-autocomplete-item {\n  display: flex;\n  align-items: center;\n  gap: 10px;\n  padding: 8px 10px;\n  border-radius: 6px;\n  cursor: pointer;\n  transition: all 0.15s ease;\n  margin-bottom: 2px;\n}\n\n.file-autocomplete-item:hover,\n.file-autocomplete-item.selected {\n  background: linear-gradient(135deg, rgba(102, 126, 234, 0.1) 0%, rgba(118, 75, 162, 0.1) 100%);\n}\n\n.file-autocomplete-item.selected {\n  border-left: 3px solid #667eea;\n  padding-left: 7px;\n}\n\n.file-icon {\n  flex-shrink: 0;\n  color: #667eea;\n}\n\n.file-info {\n  flex: 1;\n  display: flex;\n  flex-direction: column;\n  gap: 2px;\n  min-width: 0;\n}\n\n.file-name {\n  font-size: 13px;\n  font-weight: 500;\n  color: #1f2937;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.file-path {\n  font-size: 11px;\n  color: #6b7280;\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.file-autocomplete-footer {\n  padding: 6px 12px;\n  background: #f9fafb;\n  border-top: 1px solid #e5e7eb;\n  border-radius: 0 0 8px 8px;\n}\n\n.hint {\n  font-size: 10px;\n  color: #9ca3af;\n  font-style: italic;\n}\n\n.file-autocomplete-list::-webkit-scrollbar {\n  width: 6px;\n}\n\n.file-autocomplete-list::-webkit-scrollbar-track {\n  background: transparent;\n}\n\n.file-autocomplete-list::-webkit-scrollbar-thumb {\n  background: #cbd5e1;\n  border-radius: 3px;\n}\n\n.file-autocomplete-list::-webkit-scrollbar-thumb:hover {\n  background: #94a3b8;\n}\n\n", "",{"version":3,"sources":["webpack://./../agentic_chat/src/FileAutocomplete.css"],"names":[],"mappings":"AAAA;EACE,eAAe;EACf,iBAAiB;EACjB,yBAAyB;EACzB,kBAAkB;EAClB,2CAA2C;EAC3C,cAAc;EACd,gBAAgB;EAChB,gBAAgB;EAChB,gCAAgC;EAChC,oBAAoB;AACtB;;AAEA;EACE;IACE,UAAU;IACV,2BAA2B;EAC7B;EACA;IACE,UAAU;IACV,wBAAwB;EAC1B;AACF;;AAEA;EACE,aAAa;EACb,8BAA8B;EAC9B,mBAAmB;EACnB,iBAAiB;EACjB,6DAA6D;EAC7D,YAAY;EACZ,eAAe;EACf,gBAAgB;EAChB,0BAA0B;EAC1B,yBAAyB;EACzB,qBAAqB;AACvB;;AAEA;EACE,oCAAoC;EACpC,gBAAgB;EAChB,mBAAmB;EACnB,eAAe;AACjB;;AAEA;EACE,iBAAiB;EACjB,gBAAgB;EAChB,YAAY;AACd;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,SAAS;EACT,iBAAiB;EACjB,kBAAkB;EAClB,eAAe;EACf,0BAA0B;EAC1B,kBAAkB;AACpB;;AAEA;;EAEE,8FAA8F;AAChG;;AAEA;EACE,8BAA8B;EAC9B,iBAAiB;AACnB;;AAEA;EACE,cAAc;EACd,cAAc;AAChB;;AAEA;EACE,OAAO;EACP,aAAa;EACb,sBAAsB;EACtB,QAAQ;EACR,YAAY;AACd;;AAEA;EACE,eAAe;EACf,gBAAgB;EAChB,cAAc;EACd,gBAAgB;EAChB,uBAAuB;EACvB,mBAAmB;AACrB;;AAEA;EACE,eAAe;EACf,cAAc;EACd,oEAAoE;EACpE,gBAAgB;EAChB,uBAAuB;EACvB,mBAAmB;AACrB;;AAEA;EACE,iBAAiB;EACjB,mBAAmB;EACnB,6BAA6B;EAC7B,0BAA0B;AAC5B;;AAEA;EACE,eAAe;EACf,cAAc;EACd,kBAAkB;AACpB;;AAEA;EACE,UAAU;AACZ;;AAEA;EACE,uBAAuB;AACzB;;AAEA;EACE,mBAAmB;EACnB,kBAAkB;AACpB;;AAEA;EACE,mBAAmB;AACrB","sourcesContent":[".file-autocomplete {\n  position: fixed;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.15);\n  z-index: 99999;\n  min-width: 320px;\n  max-width: 500px;\n  animation: slideUpFade 0.2s ease;\n  pointer-events: auto;\n}\n\n@keyframes slideUpFade {\n  from {\n    opacity: 0;\n    transform: translateY(10px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n.file-autocomplete-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 8px 12px;\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n  font-size: 11px;\n  font-weight: 600;\n  border-radius: 8px 8px 0 0;\n  text-transform: uppercase;\n  letter-spacing: 0.5px;\n}\n\n.file-count {\n  background: rgba(255, 255, 255, 0.2);\n  padding: 2px 6px;\n  border-radius: 10px;\n  font-size: 10px;\n}\n\n.file-autocomplete-list {\n  max-height: 400px;\n  overflow-y: auto;\n  padding: 4px;\n}\n\n.file-autocomplete-item {\n  display: flex;\n  align-items: center;\n  gap: 10px;\n  padding: 8px 10px;\n  border-radius: 6px;\n  cursor: pointer;\n  transition: all 0.15s ease;\n  margin-bottom: 2px;\n}\n\n.file-autocomplete-item:hover,\n.file-autocomplete-item.selected {\n  background: linear-gradient(135deg, rgba(102, 126, 234, 0.1) 0%, rgba(118, 75, 162, 0.1) 100%);\n}\n\n.file-autocomplete-item.selected {\n  border-left: 3px solid #667eea;\n  padding-left: 7px;\n}\n\n.file-icon {\n  flex-shrink: 0;\n  color: #667eea;\n}\n\n.file-info {\n  flex: 1;\n  display: flex;\n  flex-direction: column;\n  gap: 2px;\n  min-width: 0;\n}\n\n.file-name {\n  font-size: 13px;\n  font-weight: 500;\n  color: #1f2937;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.file-path {\n  font-size: 11px;\n  color: #6b7280;\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.file-autocomplete-footer {\n  padding: 6px 12px;\n  background: #f9fafb;\n  border-top: 1px solid #e5e7eb;\n  border-radius: 0 0 8px 8px;\n}\n\n.hint {\n  font-size: 10px;\n  color: #9ca3af;\n  font-style: italic;\n}\n\n.file-autocomplete-list::-webkit-scrollbar {\n  width: 6px;\n}\n\n.file-autocomplete-list::-webkit-scrollbar-track {\n  background: transparent;\n}\n\n.file-autocomplete-list::-webkit-scrollbar-thumb {\n  background: #cbd5e1;\n  border-radius: 3px;\n}\n\n.file-autocomplete-list::-webkit-scrollbar-thumb:hover {\n  background: #94a3b8;\n}\n\n"],"sourceRoot":""}]);
// Exports
/* harmony default export */ __webpack_exports__["default"] = (___CSS_LOADER_EXPORT___);


/***/ }),

/***/ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/FollowupSuggestions.css":
/*!**********************************************************************************************************************************************!*\
  !*** ../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/FollowupSuggestions.css ***!
  \**********************************************************************************************************************************************/
/***/ (function(module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__);
// Imports


var ___CSS_LOADER_EXPORT___ = _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default()((_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default()));
// Module
___CSS_LOADER_EXPORT___.push([module.id, ".followup-suggestions-container {\n  margin: 16px 0;\n  padding: 0;\n  animation: fadeInUp 0.3s ease-out;\n}\n\n@keyframes fadeInUp {\n  from {\n    opacity: 0;\n    transform: translateY(10px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n.followup-suggestions-header {\n  margin-bottom: 12px;\n}\n\n.followup-suggestions-title {\n  font-size: 13px;\n  font-weight: 500;\n  color: #64748b;\n  display: flex;\n  align-items: center;\n  gap: 6px;\n}\n\n.followup-suggestions-list {\n  display: flex;\n  flex-direction: column;\n  gap: 8px;\n}\n\n.followup-suggestion-chip {\n  padding: 12px 16px;\n  background: #ffffff;\n  border: 1.5px solid #e2e8f0;\n  border-radius: 8px;\n  font-size: 13px;\n  color: #334155;\n  text-align: left;\n  cursor: pointer;\n  transition: all 0.2s ease;\n  font-weight: 400;\n  line-height: 1.5;\n  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);\n}\n\n.followup-suggestion-chip:hover {\n  background: #f8fafc;\n  border-color: #cbd5e1;\n  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08);\n  transform: translateY(-1px);\n}\n\n.followup-suggestion-chip:active {\n  transform: translateY(0);\n  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);\n}\n\n", "",{"version":3,"sources":["webpack://./../agentic_chat/src/FollowupSuggestions.css"],"names":[],"mappings":"AAAA;EACE,cAAc;EACd,UAAU;EACV,iCAAiC;AACnC;;AAEA;EACE;IACE,UAAU;IACV,2BAA2B;EAC7B;EACA;IACE,UAAU;IACV,wBAAwB;EAC1B;AACF;;AAEA;EACE,mBAAmB;AACrB;;AAEA;EACE,eAAe;EACf,gBAAgB;EAChB,cAAc;EACd,aAAa;EACb,mBAAmB;EACnB,QAAQ;AACV;;AAEA;EACE,aAAa;EACb,sBAAsB;EACtB,QAAQ;AACV;;AAEA;EACE,kBAAkB;EAClB,mBAAmB;EACnB,2BAA2B;EAC3B,kBAAkB;EAClB,eAAe;EACf,cAAc;EACd,gBAAgB;EAChB,eAAe;EACf,yBAAyB;EACzB,gBAAgB;EAChB,gBAAgB;EAChB,yCAAyC;AAC3C;;AAEA;EACE,mBAAmB;EACnB,qBAAqB;EACrB,yCAAyC;EACzC,2BAA2B;AAC7B;;AAEA;EACE,wBAAwB;EACxB,yCAAyC;AAC3C","sourcesContent":[".followup-suggestions-container {\n  margin: 16px 0;\n  padding: 0;\n  animation: fadeInUp 0.3s ease-out;\n}\n\n@keyframes fadeInUp {\n  from {\n    opacity: 0;\n    transform: translateY(10px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n.followup-suggestions-header {\n  margin-bottom: 12px;\n}\n\n.followup-suggestions-title {\n  font-size: 13px;\n  font-weight: 500;\n  color: #64748b;\n  display: flex;\n  align-items: center;\n  gap: 6px;\n}\n\n.followup-suggestions-list {\n  display: flex;\n  flex-direction: column;\n  gap: 8px;\n}\n\n.followup-suggestion-chip {\n  padding: 12px 16px;\n  background: #ffffff;\n  border: 1.5px solid #e2e8f0;\n  border-radius: 8px;\n  font-size: 13px;\n  color: #334155;\n  text-align: left;\n  cursor: pointer;\n  transition: all 0.2s ease;\n  font-weight: 400;\n  line-height: 1.5;\n  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);\n}\n\n.followup-suggestion-chip:hover {\n  background: #f8fafc;\n  border-color: #cbd5e1;\n  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08);\n  transform: translateY(-1px);\n}\n\n.followup-suggestion-chip:active {\n  transform: translateY(0);\n  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);\n}\n\n"],"sourceRoot":""}]);
// Exports
/* harmony default export */ __webpack_exports__["default"] = (___CSS_LOADER_EXPORT___);


/***/ }),

/***/ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/GuidedTour.css":
/*!*************************************************************************************************************************************!*\
  !*** ../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/GuidedTour.css ***!
  \*************************************************************************************************************************************/
/***/ (function(module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__);
// Imports


var ___CSS_LOADER_EXPORT___ = _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default()((_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default()));
// Module
___CSS_LOADER_EXPORT___.push([module.id, ".tour-overly {\n  position: fixed;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  background: rgba(0, 0, 0, 0.6);\n  z-index: 9998;\n  animation: tourFadeIn 0.3s ease;\n}\n\n@keyframes tourFadeIn {\n  from {\n    opacity: 0;\n  }\n  to {\n    opacity: 1;\n  }\n}\n\n.tour-highlight {\n  position: fixed;\n  background: transparent;\n  border: 3px solid #667eea;\n  border-radius: 8px;\n  box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.6), 0 0 20px rgba(102, 126, 234, 0.8);\n  z-index: 9999;\n  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);\n  pointer-events: none;\n  animation: tourPulse 2s ease-in-out infinite;\n}\n\n@keyframes tourPulse {\n  0%, 100% {\n    border-color: #667eea;\n    box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.6), 0 0 20px rgba(102, 126, 234, 0.8);\n  }\n  50% {\n    border-color: #764ba2;\n    box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.6), 0 0 30px rgba(118, 75, 162, 1);\n  }\n}\n\n.tour-tooltip {\n  position: fixed;\n  width: 320px;\n  background: white;\n  border-radius: 12px;\n  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);\n  z-index: 10000;\n  animation: tourSlideIn 0.4s cubic-bezier(0.4, 0, 0.2, 1);\n  overflow: hidden;\n}\n\n@keyframes tourSlideIn {\n  from {\n    opacity: 0;\n    transform: scale(0.9) translateY(20px);\n  }\n  to {\n    opacity: 1;\n    transform: scale(1) translateY(0);\n  }\n}\n\n.tour-tooltip-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 12px 16px;\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n}\n\n.tour-step-counter {\n  font-size: 12px;\n  font-weight: 600;\n  text-transform: uppercase;\n  letter-spacing: 0.5px;\n}\n\n.tour-close-btn {\n  background: rgba(255, 255, 255, 0.2);\n  border: none;\n  color: white;\n  width: 28px;\n  height: 28px;\n  border-radius: 6px;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  cursor: pointer;\n  transition: all 0.2s;\n}\n\n.tour-close-btn:hover {\n  background: rgba(255, 255, 255, 0.3);\n  transform: scale(1.05);\n}\n\n.tour-tooltip-content {\n  padding: 20px;\n}\n\n.tour-tooltip-title {\n  font-size: 18px;\n  font-weight: 700;\n  color: #1e293b;\n  margin: 0 0 12px 0;\n  line-height: 1.3;\n}\n\n.tour-tooltip-text {\n  font-size: 14px;\n  color: #64748b;\n  line-height: 1.6;\n  margin: 0;\n}\n\n.tour-tooltip-footer {\n  padding: 0 16px 16px;\n}\n\n.tour-progress-bar {\n  height: 4px;\n  background: #e2e8f0;\n  border-radius: 2px;\n  overflow: hidden;\n  margin-bottom: 12px;\n}\n\n.tour-progress-fill {\n  height: 100%;\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  border-radius: 2px;\n  transition: width 0.4s cubic-bezier(0.4, 0, 0.2, 1);\n}\n\n.tour-tooltip-actions {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  gap: 12px;\n}\n\n.tour-navigation {\n  display: flex;\n  gap: 8px;\n}\n\n.tour-btn {\n  padding: 8px 16px;\n  border: none;\n  border-radius: 8px;\n  font-size: 13px;\n  font-weight: 600;\n  cursor: pointer;\n  transition: all 0.2s;\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  font-family: inherit;\n}\n\n.tour-btn-primary {\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n  box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);\n}\n\n.tour-btn-primary:hover {\n  transform: translateY(-2px);\n  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);\n}\n\n.tour-btn-primary:active {\n  transform: translateY(0);\n}\n\n.tour-btn-secondary {\n  background: transparent;\n  color: #64748b;\n  border: 1px solid #e2e8f0;\n}\n\n.tour-btn-secondary:hover {\n  background: #f8fafc;\n  border-color: #cbd5e1;\n  color: #475569;\n}\n\n.tour-btn-icon {\n  padding: 8px;\n  background: #f8fafc;\n  color: #64748b;\n  border: 1px solid #e2e8f0;\n}\n\n.tour-btn-icon:hover:not(:disabled) {\n  background: #f1f5f9;\n  border-color: #cbd5e1;\n  color: #475569;\n}\n\n.tour-btn-icon:disabled {\n  opacity: 0.4;\n  cursor: not-allowed;\n}\n\n.tour-btn:disabled {\n  opacity: 0.5;\n  cursor: not-allowed;\n}\n\n@media (max-width: 640px) {\n  .tour-tooltip {\n    width: calc(100% - 32px);\n    max-width: 320px;\n    left: 16px !important;\n    right: 16px;\n  }\n\n  .tour-tooltip-content {\n    padding: 16px;\n  }\n\n  .tour-tooltip-title {\n    font-size: 16px;\n  }\n\n  .tour-tooltip-text {\n    font-size: 13px;\n  }\n\n  .tour-btn {\n    padding: 6px 12px;\n    font-size: 12px;\n  }\n}\n\n", "",{"version":3,"sources":["webpack://./../agentic_chat/src/GuidedTour.css"],"names":[],"mappings":"AAAA;EACE,eAAe;EACf,MAAM;EACN,OAAO;EACP,QAAQ;EACR,SAAS;EACT,8BAA8B;EAC9B,aAAa;EACb,+BAA+B;AACjC;;AAEA;EACE;IACE,UAAU;EACZ;EACA;IACE,UAAU;EACZ;AACF;;AAEA;EACE,eAAe;EACf,uBAAuB;EACvB,yBAAyB;EACzB,kBAAkB;EAClB,8EAA8E;EAC9E,aAAa;EACb,iDAAiD;EACjD,oBAAoB;EACpB,4CAA4C;AAC9C;;AAEA;EACE;IACE,qBAAqB;IACrB,8EAA8E;EAChF;EACA;IACE,qBAAqB;IACrB,2EAA2E;EAC7E;AACF;;AAEA;EACE,eAAe;EACf,YAAY;EACZ,iBAAiB;EACjB,mBAAmB;EACnB,0CAA0C;EAC1C,cAAc;EACd,wDAAwD;EACxD,gBAAgB;AAClB;;AAEA;EACE;IACE,UAAU;IACV,sCAAsC;EACxC;EACA;IACE,UAAU;IACV,iCAAiC;EACnC;AACF;;AAEA;EACE,aAAa;EACb,8BAA8B;EAC9B,mBAAmB;EACnB,kBAAkB;EAClB,6DAA6D;EAC7D,YAAY;AACd;;AAEA;EACE,eAAe;EACf,gBAAgB;EAChB,yBAAyB;EACzB,qBAAqB;AACvB;;AAEA;EACE,oCAAoC;EACpC,YAAY;EACZ,YAAY;EACZ,WAAW;EACX,YAAY;EACZ,kBAAkB;EAClB,aAAa;EACb,mBAAmB;EACnB,uBAAuB;EACvB,eAAe;EACf,oBAAoB;AACtB;;AAEA;EACE,oCAAoC;EACpC,sBAAsB;AACxB;;AAEA;EACE,aAAa;AACf;;AAEA;EACE,eAAe;EACf,gBAAgB;EAChB,cAAc;EACd,kBAAkB;EAClB,gBAAgB;AAClB;;AAEA;EACE,eAAe;EACf,cAAc;EACd,gBAAgB;EAChB,SAAS;AACX;;AAEA;EACE,oBAAoB;AACtB;;AAEA;EACE,WAAW;EACX,mBAAmB;EACnB,kBAAkB;EAClB,gBAAgB;EAChB,mBAAmB;AACrB;;AAEA;EACE,YAAY;EACZ,6DAA6D;EAC7D,kBAAkB;EAClB,mDAAmD;AACrD;;AAEA;EACE,aAAa;EACb,8BAA8B;EAC9B,mBAAmB;EACnB,SAAS;AACX;;AAEA;EACE,aAAa;EACb,QAAQ;AACV;;AAEA;EACE,iBAAiB;EACjB,YAAY;EACZ,kBAAkB;EAClB,eAAe;EACf,gBAAgB;EAChB,eAAe;EACf,oBAAoB;EACpB,aAAa;EACb,mBAAmB;EACnB,QAAQ;EACR,oBAAoB;AACtB;;AAEA;EACE,6DAA6D;EAC7D,YAAY;EACZ,8CAA8C;AAChD;;AAEA;EACE,2BAA2B;EAC3B,+CAA+C;AACjD;;AAEA;EACE,wBAAwB;AAC1B;;AAEA;EACE,uBAAuB;EACvB,cAAc;EACd,yBAAyB;AAC3B;;AAEA;EACE,mBAAmB;EACnB,qBAAqB;EACrB,cAAc;AAChB;;AAEA;EACE,YAAY;EACZ,mBAAmB;EACnB,cAAc;EACd,yBAAyB;AAC3B;;AAEA;EACE,mBAAmB;EACnB,qBAAqB;EACrB,cAAc;AAChB;;AAEA;EACE,YAAY;EACZ,mBAAmB;AACrB;;AAEA;EACE,YAAY;EACZ,mBAAmB;AACrB;;AAEA;EACE;IACE,wBAAwB;IACxB,gBAAgB;IAChB,qBAAqB;IACrB,WAAW;EACb;;EAEA;IACE,aAAa;EACf;;EAEA;IACE,eAAe;EACjB;;EAEA;IACE,eAAe;EACjB;;EAEA;IACE,iBAAiB;IACjB,eAAe;EACjB;AACF","sourcesContent":[".tour-overly {\n  position: fixed;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  background: rgba(0, 0, 0, 0.6);\n  z-index: 9998;\n  animation: tourFadeIn 0.3s ease;\n}\n\n@keyframes tourFadeIn {\n  from {\n    opacity: 0;\n  }\n  to {\n    opacity: 1;\n  }\n}\n\n.tour-highlight {\n  position: fixed;\n  background: transparent;\n  border: 3px solid #667eea;\n  border-radius: 8px;\n  box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.6), 0 0 20px rgba(102, 126, 234, 0.8);\n  z-index: 9999;\n  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);\n  pointer-events: none;\n  animation: tourPulse 2s ease-in-out infinite;\n}\n\n@keyframes tourPulse {\n  0%, 100% {\n    border-color: #667eea;\n    box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.6), 0 0 20px rgba(102, 126, 234, 0.8);\n  }\n  50% {\n    border-color: #764ba2;\n    box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.6), 0 0 30px rgba(118, 75, 162, 1);\n  }\n}\n\n.tour-tooltip {\n  position: fixed;\n  width: 320px;\n  background: white;\n  border-radius: 12px;\n  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);\n  z-index: 10000;\n  animation: tourSlideIn 0.4s cubic-bezier(0.4, 0, 0.2, 1);\n  overflow: hidden;\n}\n\n@keyframes tourSlideIn {\n  from {\n    opacity: 0;\n    transform: scale(0.9) translateY(20px);\n  }\n  to {\n    opacity: 1;\n    transform: scale(1) translateY(0);\n  }\n}\n\n.tour-tooltip-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 12px 16px;\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n}\n\n.tour-step-counter {\n  font-size: 12px;\n  font-weight: 600;\n  text-transform: uppercase;\n  letter-spacing: 0.5px;\n}\n\n.tour-close-btn {\n  background: rgba(255, 255, 255, 0.2);\n  border: none;\n  color: white;\n  width: 28px;\n  height: 28px;\n  border-radius: 6px;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  cursor: pointer;\n  transition: all 0.2s;\n}\n\n.tour-close-btn:hover {\n  background: rgba(255, 255, 255, 0.3);\n  transform: scale(1.05);\n}\n\n.tour-tooltip-content {\n  padding: 20px;\n}\n\n.tour-tooltip-title {\n  font-size: 18px;\n  font-weight: 700;\n  color: #1e293b;\n  margin: 0 0 12px 0;\n  line-height: 1.3;\n}\n\n.tour-tooltip-text {\n  font-size: 14px;\n  color: #64748b;\n  line-height: 1.6;\n  margin: 0;\n}\n\n.tour-tooltip-footer {\n  padding: 0 16px 16px;\n}\n\n.tour-progress-bar {\n  height: 4px;\n  background: #e2e8f0;\n  border-radius: 2px;\n  overflow: hidden;\n  margin-bottom: 12px;\n}\n\n.tour-progress-fill {\n  height: 100%;\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  border-radius: 2px;\n  transition: width 0.4s cubic-bezier(0.4, 0, 0.2, 1);\n}\n\n.tour-tooltip-actions {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  gap: 12px;\n}\n\n.tour-navigation {\n  display: flex;\n  gap: 8px;\n}\n\n.tour-btn {\n  padding: 8px 16px;\n  border: none;\n  border-radius: 8px;\n  font-size: 13px;\n  font-weight: 600;\n  cursor: pointer;\n  transition: all 0.2s;\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  font-family: inherit;\n}\n\n.tour-btn-primary {\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n  box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);\n}\n\n.tour-btn-primary:hover {\n  transform: translateY(-2px);\n  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);\n}\n\n.tour-btn-primary:active {\n  transform: translateY(0);\n}\n\n.tour-btn-secondary {\n  background: transparent;\n  color: #64748b;\n  border: 1px solid #e2e8f0;\n}\n\n.tour-btn-secondary:hover {\n  background: #f8fafc;\n  border-color: #cbd5e1;\n  color: #475569;\n}\n\n.tour-btn-icon {\n  padding: 8px;\n  background: #f8fafc;\n  color: #64748b;\n  border: 1px solid #e2e8f0;\n}\n\n.tour-btn-icon:hover:not(:disabled) {\n  background: #f1f5f9;\n  border-color: #cbd5e1;\n  color: #475569;\n}\n\n.tour-btn-icon:disabled {\n  opacity: 0.4;\n  cursor: not-allowed;\n}\n\n.tour-btn:disabled {\n  opacity: 0.5;\n  cursor: not-allowed;\n}\n\n@media (max-width: 640px) {\n  .tour-tooltip {\n    width: calc(100% - 32px);\n    max-width: 320px;\n    left: 16px !important;\n    right: 16px;\n  }\n\n  .tour-tooltip-content {\n    padding: 16px;\n  }\n\n  .tour-tooltip-title {\n    font-size: 16px;\n  }\n\n  .tour-tooltip-text {\n    font-size: 13px;\n  }\n\n  .tour-btn {\n    padding: 6px 12px;\n    font-size: 12px;\n  }\n}\n\n"],"sourceRoot":""}]);
// Exports
/* harmony default export */ __webpack_exports__["default"] = (___CSS_LOADER_EXPORT___);


/***/ }),

/***/ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/LeftSidebar.css":
/*!**************************************************************************************************************************************!*\
  !*** ../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/LeftSidebar.css ***!
  \**************************************************************************************************************************************/
/***/ (function(module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__);
// Imports


var ___CSS_LOADER_EXPORT___ = _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default()((_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default()));
// Module
___CSS_LOADER_EXPORT___.push([module.id, ".left-sidebar {\n  position: fixed;\n  left: 0;\n  top: 48px;\n  bottom: 0;\n  background: white;\n  border-right: 1px solid #e5e7eb;\n  z-index: 1000;\n  display: flex;\n  flex-direction: column;\n  transition: transform 0.4s cubic-bezier(0.4, 0, 0.2, 1), box-shadow 0.4s ease;\n  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.05);\n  width: 320px;\n  transform: translateX(0);\n  overflow: visible;\n}\n\n.left-sidebar.collapsed {\n  transform: translateX(-100%);\n  box-shadow: none;\n}\n\n.left-sidebar.expanded {\n  transform: translateX(0);\n  animation: slideInFromLeft 0.4s cubic-bezier(0.4, 0, 0.2, 1);\n}\n\n@keyframes slideInFromLeft {\n  from {\n    transform: translateX(-100%);\n  }\n  to {\n    transform: translateX(0);\n  }\n}\n\n.left-sidebar-header {\n  display: flex;\n  align-items: center;\n  justify-content: space-between;\n  padding: 10px;\n  border-bottom: 1px solid #e5e7eb;\n  gap: 6px;\n  background: #f9fafb;\n  min-height: 52px;\n}\n\n.left-sidebar-tabs {\n  display: flex;\n  gap: 3px;\n  flex: 1;\n  min-width: 0;\n  background: white;\n  border-radius: 8px;\n  padding: 3px;\n  border: 1px solid #e5e7eb;\n  overflow: hidden;\n}\n\n.sidebar-tab {\n  flex: 1;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  gap: 4px;\n  padding: 6px 6px;\n  background: transparent;\n  border: none;\n  border-radius: 6px;\n  color: #64748b;\n  font-size: 10.5px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s;\n  white-space: nowrap;\n  min-width: 0;\n}\n\n.sidebar-tab span {\n  white-space: nowrap;\n}\n\n.sidebar-tab:hover {\n  background: #f8fafc;\n  color: #4e00ec;\n}\n\n.sidebar-tab.active {\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n  box-shadow: 0 2px 4px rgba(102, 126, 234, 0.3);\n}\n\n.sidebar-tab-info-tooltip-wrapper {\n  position: relative;\n  display: flex;\n  align-items: center;\n  margin-left: 4px;\n  cursor: help;\n}\n\n.sidebar-info-icon {\n  color: #94a3b8;\n  cursor: help;\n  transition: color 0.2s ease;\n  flex-shrink: 0;\n  pointer-events: auto;\n}\n\n.sidebar-tab:hover .sidebar-info-icon {\n  color: #4e00ec;\n}\n\n.sidebar-tab.active .sidebar-info-icon {\n  color: white;\n}\n\n.sidebar-tab-info-tooltip-wrapper:hover .sidebar-info-icon {\n  color: #4e00ec !important;\n}\n\n.sidebar-tab.active .sidebar-tab-info-tooltip-wrapper:hover .sidebar-info-icon {\n  color: #e0e7ff !important;\n}\n\n.sidebar-tab-info-tooltip {\n  position: fixed;\n  top: 60px;\n  left: 20px;\n  width: 320px;\n  max-width: calc(100vw - 40px);\n  padding: 14px 16px;\n  background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);\n  border: 2px solid #e0e7ff;\n  border-radius: 12px;\n  box-shadow: 0 8px 32px rgba(102, 126, 234, 0.25), 0 4px 12px rgba(0, 0, 0, 0.15);\n  font-size: 13px;\n  line-height: 1.6;\n  color: #334155;\n  opacity: 0;\n  visibility: hidden;\n  transition: opacity 0.3s ease, visibility 0.3s ease, transform 0.3s ease;\n  z-index: 9999;\n  pointer-events: none;\n  transform-origin: top left;\n  font-weight: 500;\n  white-space: normal;\n  word-wrap: break-word;\n  word-break: break-word;\n  overflow-wrap: break-word;\n  hyphens: auto;\n}\n\n.sidebar-tab-info-tooltip code {\n  background: #e0e7ff;\n  color: #4f46e5;\n  padding: 2px 6px;\n  border-radius: 4px;\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;\n  font-size: 12px;\n  font-weight: 600;\n  white-space: nowrap;\n}\n\n.sidebar-tab-info-tooltip-wrapper:hover .sidebar-tab-info-tooltip {\n  opacity: 1;\n  visibility: visible;\n  pointer-events: auto;\n  transform: translateY(2px);\n}\n\n.left-sidebar-toggle {\n  width: 32px;\n  min-width: 32px;\n  height: 32px;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 6px;\n  cursor: pointer;\n  color: #64748b;\n  transition: all 0.2s;\n  flex-shrink: 0;\n}\n\n.left-sidebar-toggle:hover {\n  background: #f8fafc;\n  border-color: #cbd5e1;\n  color: #4e00ec;\n}\n\n.left-sidebar-content {\n  flex: 1;\n  overflow-y: auto;\n  overflow-x: visible;\n  display: flex;\n  flex-direction: column;\n}\n\n.conversations-actions {\n  padding: 12px;\n  border-bottom: 1px solid #e5e7eb;\n  background: #f9fafb;\n}\n\n.new-conversation-btn {\n  width: 100%;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  gap: 8px;\n  padding: 10px 16px;\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  border: none;\n  border-radius: 8px;\n  color: white;\n  font-size: 13px;\n  font-weight: 600;\n  cursor: pointer;\n  transition: all 0.2s;\n  box-shadow: 0 2px 4px rgba(102, 126, 234, 0.2);\n}\n\n.new-conversation-btn:hover {\n  transform: translateY(-1px);\n  box-shadow: 0 4px 8px rgba(102, 126, 234, 0.3);\n}\n\n.new-conversation-btn:active {\n  transform: translateY(0);\n}\n\n.conversations-list {\n  display: flex;\n  flex-direction: column;\n  gap: 8px;\n  padding: 12px;\n  flex: 1;\n  overflow: visible;\n}\n\n.conversation-item {\n  background: #f8fafc;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  padding: 12px;\n  cursor: pointer;\n  transition: all 0.2s;\n  position: relative;\n  overflow: visible;\n}\n\n.conversation-item:hover {\n  background: #f1f5f9;\n  border-color: #cbd5e1;\n  transform: translateY(-1px);\n  box-shadow: 0 2px 8px rgba(102, 126, 234, 0.1);\n}\n\n.conversation-item.selected {\n  background: #eef2ff;\n  border-color: #667eea;\n  box-shadow: 0 0 0 2px rgba(102, 126, 234, 0.1);\n}\n\n.conversation-header {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  margin-bottom: 6px;\n  position: relative;\n  overflow: visible;\n}\n\n.conversation-header svg {\n  color: #667eea;\n  flex-shrink: 0;\n}\n\n.conversation-title {\n  font-size: 13px;\n  font-weight: 600;\n  color: #1e293b;\n  flex: 1;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.delete-conversation-btn {\n  background: none;\n  border: none;\n  color: #94a3b8;\n  cursor: pointer;\n  padding: 4px;\n  display: flex;\n  align-items: center;\n  border-radius: 4px;\n  transition: all 0.2s;\n  opacity: 0;\n}\n\n.conversation-item:hover .delete-conversation-btn {\n  opacity: 1;\n}\n\n.delete-conversation-btn:hover {\n  background: #fee2e2;\n  color: #ef4444;\n}\n\n.conversation-preview {\n  font-size: 12px;\n  color: #64748b;\n  line-height: 1.4;\n  margin-bottom: 6px;\n  display: -webkit-box;\n  -webkit-line-clamp: 2;\n  -webkit-box-orient: vertical;\n  overflow: hidden;\n}\n\n.conversation-date {\n  font-size: 11px;\n  color: #94a3b8;\n}\n\n.empty-state {\n  display: flex;\n  flex-direction: column;\n  align-items: center;\n  justify-content: center;\n  padding: 40px 20px;\n  text-align: center;\n  color: #94a3b8;\n  gap: 8px;\n}\n\n.empty-state svg {\n  opacity: 0.5;\n}\n\n.empty-state p {\n  margin: 0;\n  font-size: 14px;\n  font-weight: 600;\n  color: #64748b;\n}\n\n.empty-state small {\n  font-size: 12px;\n}\n\n.variables-wrapper {\n  flex: 1;\n  overflow: hidden;\n  position: relative;\n}\n\n.variables-wrapper .variables-sidebar {\n  position: relative !important;\n  left: 0 !important;\n  top: 0 !important;\n  height: 100%;\n  width: 100%;\n  border-right: none;\n  box-shadow: none;\n  transform: none !important;\n}\n\n.variables-wrapper .variables-sidebar-floating-toggle {\n  display: none;\n}\n\n.left-sidebar-floating-toggle {\n  position: fixed;\n  left: 0;\n  top: 50%;\n  transform: translateY(-50%);\n  width: 48px;\n  height: 64px;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-left: none;\n  border-radius: 0 8px 8px 0;\n  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.1);\n  cursor: pointer;\n  display: flex;\n  flex-direction: column;\n  align-items: center;\n  justify-content: center;\n  gap: 4px;\n  z-index: 999;\n  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);\n  color: #64748b;\n  animation: slideInToggle 0.5s cubic-bezier(0.4, 0, 0.2, 1);\n}\n\n.left-sidebar-floating-toggle:hover {\n  background: #f8fafc;\n  color: #4e00ec;\n  box-shadow: 2px 0 12px rgba(102, 126, 234, 0.2);\n  transform: translateY(-50%) translateX(4px);\n}\n\n@keyframes slideInToggle {\n  from {\n    transform: translateY(-50%) translateX(-100%);\n    opacity: 0;\n  }\n  to {\n    transform: translateY(-50%) translateX(0);\n    opacity: 1;\n  }\n}\n\n.sidebar-floating-count {\n  font-size: 11px;\n  font-weight: 600;\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n  padding: 2px 6px;\n  border-radius: 10px;\n  min-width: 20px;\n  text-align: center;\n}\n\n.left-sidebar-content::-webkit-scrollbar {\n  width: 6px;\n}\n\n.left-sidebar-content::-webkit-scrollbar-track {\n  background: transparent;\n}\n\n.left-sidebar-content::-webkit-scrollbar-thumb {\n  background: #cbd5e1;\n  border-radius: 3px;\n}\n\n.left-sidebar-content::-webkit-scrollbar-thumb:hover {\n  background: #94a3b8;\n}\n\n@media (max-width: 768px) {\n  .left-sidebar {\n    width: 280px;\n  }\n}\n\n@media (max-width: 640px) {\n  .left-sidebar {\n    width: 100%;\n    max-width: 300px;\n  }\n}\n\n.flow-item {\n  cursor: default;\n}\n\n.flow-parameters {\n  margin: 8px 0;\n  padding: 8px;\n  background: white;\n  border-radius: 6px;\n  border: 1px solid #e5e7eb;\n}\n\n.flow-function-signature {\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;\n  font-size: 11px;\n  color: #1e293b;\n  line-height: 1.6;\n}\n\n.flow-function-signature code {\n  background: transparent;\n  padding: 0;\n  color: inherit;\n  font-family: inherit;\n}\n\n.flow-params-list {\n  margin-left: 12px;\n  margin-top: 4px;\n}\n\n.flow-param {\n  display: flex;\n  align-items: center;\n  gap: 4px;\n  margin-bottom: 2px;\n}\n\n.param-name {\n  color: #667eea;\n  font-weight: 600;\n}\n\n.param-type {\n  color: #64748b;\n  font-style: italic;\n}\n\n.param-default {\n  color: #10b981;\n}\n\n.param-required {\n  color: #ef4444;\n  font-weight: 600;\n  margin-left: 2px;\n}\n\n.flow-info-icon-wrapper {\n  position: relative;\n  display: flex;\n  align-items: center;\n  margin-left: auto;\n}\n\n.flow-info-icon {\n  color: #94a3b8;\n  cursor: pointer;\n  transition: all 0.2s;\n  flex-shrink: 0;\n}\n\n.flow-info-icon:hover {\n  color: #667eea;\n  transform: scale(1.1);\n}\n\n.flow-info-tooltip {\n  position: fixed;\n  width: 300px;\n  background: #1e293b;\n  color: white;\n  padding: 14px 16px;\n  border-radius: 8px;\n  font-size: 12px;\n  line-height: 1.5;\n  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);\n  z-index: 99999;\n  animation: tooltipFadeIn 0.2s ease;\n  pointer-events: auto;\n}\n\n.flow-info-tooltip::before {\n  content: '';\n  position: absolute;\n  right: 100%;\n  top: 20px;\n  border: 8px solid transparent;\n  border-right-color: #1e293b;\n}\n\n@keyframes tooltipFadeIn {\n  from {\n    opacity: 0;\n    transform: translateY(4px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n\n", "",{"version":3,"sources":["webpack://./../agentic_chat/src/LeftSidebar.css"],"names":[],"mappings":"AAAA;EACE,eAAe;EACf,OAAO;EACP,SAAS;EACT,SAAS;EACT,iBAAiB;EACjB,+BAA+B;EAC/B,aAAa;EACb,aAAa;EACb,sBAAsB;EACtB,6EAA6E;EAC7E,yCAAyC;EACzC,YAAY;EACZ,wBAAwB;EACxB,iBAAiB;AACnB;;AAEA;EACE,4BAA4B;EAC5B,gBAAgB;AAClB;;AAEA;EACE,wBAAwB;EACxB,4DAA4D;AAC9D;;AAEA;EACE;IACE,4BAA4B;EAC9B;EACA;IACE,wBAAwB;EAC1B;AACF;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,8BAA8B;EAC9B,aAAa;EACb,gCAAgC;EAChC,QAAQ;EACR,mBAAmB;EACnB,gBAAgB;AAClB;;AAEA;EACE,aAAa;EACb,QAAQ;EACR,OAAO;EACP,YAAY;EACZ,iBAAiB;EACjB,kBAAkB;EAClB,YAAY;EACZ,yBAAyB;EACzB,gBAAgB;AAClB;;AAEA;EACE,OAAO;EACP,aAAa;EACb,mBAAmB;EACnB,uBAAuB;EACvB,QAAQ;EACR,gBAAgB;EAChB,uBAAuB;EACvB,YAAY;EACZ,kBAAkB;EAClB,cAAc;EACd,iBAAiB;EACjB,gBAAgB;EAChB,eAAe;EACf,oBAAoB;EACpB,mBAAmB;EACnB,YAAY;AACd;;AAEA;EACE,mBAAmB;AACrB;;AAEA;EACE,mBAAmB;EACnB,cAAc;AAChB;;AAEA;EACE,6DAA6D;EAC7D,YAAY;EACZ,8CAA8C;AAChD;;AAEA;EACE,kBAAkB;EAClB,aAAa;EACb,mBAAmB;EACnB,gBAAgB;EAChB,YAAY;AACd;;AAEA;EACE,cAAc;EACd,YAAY;EACZ,2BAA2B;EAC3B,cAAc;EACd,oBAAoB;AACtB;;AAEA;EACE,cAAc;AAChB;;AAEA;EACE,YAAY;AACd;;AAEA;EACE,yBAAyB;AAC3B;;AAEA;EACE,yBAAyB;AAC3B;;AAEA;EACE,eAAe;EACf,SAAS;EACT,UAAU;EACV,YAAY;EACZ,6BAA6B;EAC7B,kBAAkB;EAClB,6DAA6D;EAC7D,yBAAyB;EACzB,mBAAmB;EACnB,gFAAgF;EAChF,eAAe;EACf,gBAAgB;EAChB,cAAc;EACd,UAAU;EACV,kBAAkB;EAClB,wEAAwE;EACxE,aAAa;EACb,oBAAoB;EACpB,0BAA0B;EAC1B,gBAAgB;EAChB,mBAAmB;EACnB,qBAAqB;EACrB,sBAAsB;EACtB,yBAAyB;EACzB,aAAa;AACf;;AAEA;EACE,mBAAmB;EACnB,cAAc;EACd,gBAAgB;EAChB,kBAAkB;EAClB,oEAAoE;EACpE,eAAe;EACf,gBAAgB;EAChB,mBAAmB;AACrB;;AAEA;EACE,UAAU;EACV,mBAAmB;EACnB,oBAAoB;EACpB,0BAA0B;AAC5B;;AAEA;EACE,WAAW;EACX,eAAe;EACf,YAAY;EACZ,aAAa;EACb,mBAAmB;EACnB,uBAAuB;EACvB,iBAAiB;EACjB,yBAAyB;EACzB,kBAAkB;EAClB,eAAe;EACf,cAAc;EACd,oBAAoB;EACpB,cAAc;AAChB;;AAEA;EACE,mBAAmB;EACnB,qBAAqB;EACrB,cAAc;AAChB;;AAEA;EACE,OAAO;EACP,gBAAgB;EAChB,mBAAmB;EACnB,aAAa;EACb,sBAAsB;AACxB;;AAEA;EACE,aAAa;EACb,gCAAgC;EAChC,mBAAmB;AACrB;;AAEA;EACE,WAAW;EACX,aAAa;EACb,mBAAmB;EACnB,uBAAuB;EACvB,QAAQ;EACR,kBAAkB;EAClB,6DAA6D;EAC7D,YAAY;EACZ,kBAAkB;EAClB,YAAY;EACZ,eAAe;EACf,gBAAgB;EAChB,eAAe;EACf,oBAAoB;EACpB,8CAA8C;AAChD;;AAEA;EACE,2BAA2B;EAC3B,8CAA8C;AAChD;;AAEA;EACE,wBAAwB;AAC1B;;AAEA;EACE,aAAa;EACb,sBAAsB;EACtB,QAAQ;EACR,aAAa;EACb,OAAO;EACP,iBAAiB;AACnB;;AAEA;EACE,mBAAmB;EACnB,yBAAyB;EACzB,kBAAkB;EAClB,aAAa;EACb,eAAe;EACf,oBAAoB;EACpB,kBAAkB;EAClB,iBAAiB;AACnB;;AAEA;EACE,mBAAmB;EACnB,qBAAqB;EACrB,2BAA2B;EAC3B,8CAA8C;AAChD;;AAEA;EACE,mBAAmB;EACnB,qBAAqB;EACrB,8CAA8C;AAChD;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,QAAQ;EACR,kBAAkB;EAClB,kBAAkB;EAClB,iBAAiB;AACnB;;AAEA;EACE,cAAc;EACd,cAAc;AAChB;;AAEA;EACE,eAAe;EACf,gBAAgB;EAChB,cAAc;EACd,OAAO;EACP,gBAAgB;EAChB,uBAAuB;EACvB,mBAAmB;AACrB;;AAEA;EACE,gBAAgB;EAChB,YAAY;EACZ,cAAc;EACd,eAAe;EACf,YAAY;EACZ,aAAa;EACb,mBAAmB;EACnB,kBAAkB;EAClB,oBAAoB;EACpB,UAAU;AACZ;;AAEA;EACE,UAAU;AACZ;;AAEA;EACE,mBAAmB;EACnB,cAAc;AAChB;;AAEA;EACE,eAAe;EACf,cAAc;EACd,gBAAgB;EAChB,kBAAkB;EAClB,oBAAoB;EACpB,qBAAqB;EACrB,4BAA4B;EAC5B,gBAAgB;AAClB;;AAEA;EACE,eAAe;EACf,cAAc;AAChB;;AAEA;EACE,aAAa;EACb,sBAAsB;EACtB,mBAAmB;EACnB,uBAAuB;EACvB,kBAAkB;EAClB,kBAAkB;EAClB,cAAc;EACd,QAAQ;AACV;;AAEA;EACE,YAAY;AACd;;AAEA;EACE,SAAS;EACT,eAAe;EACf,gBAAgB;EAChB,cAAc;AAChB;;AAEA;EACE,eAAe;AACjB;;AAEA;EACE,OAAO;EACP,gBAAgB;EAChB,kBAAkB;AACpB;;AAEA;EACE,6BAA6B;EAC7B,kBAAkB;EAClB,iBAAiB;EACjB,YAAY;EACZ,WAAW;EACX,kBAAkB;EAClB,gBAAgB;EAChB,0BAA0B;AAC5B;;AAEA;EACE,aAAa;AACf;;AAEA;EACE,eAAe;EACf,OAAO;EACP,QAAQ;EACR,2BAA2B;EAC3B,WAAW;EACX,YAAY;EACZ,iBAAiB;EACjB,yBAAyB;EACzB,iBAAiB;EACjB,0BAA0B;EAC1B,wCAAwC;EACxC,eAAe;EACf,aAAa;EACb,sBAAsB;EACtB,mBAAmB;EACnB,uBAAuB;EACvB,QAAQ;EACR,YAAY;EACZ,iDAAiD;EACjD,cAAc;EACd,0DAA0D;AAC5D;;AAEA;EACE,mBAAmB;EACnB,cAAc;EACd,+CAA+C;EAC/C,2CAA2C;AAC7C;;AAEA;EACE;IACE,6CAA6C;IAC7C,UAAU;EACZ;EACA;IACE,yCAAyC;IACzC,UAAU;EACZ;AACF;;AAEA;EACE,eAAe;EACf,gBAAgB;EAChB,6DAA6D;EAC7D,YAAY;EACZ,gBAAgB;EAChB,mBAAmB;EACnB,eAAe;EACf,kBAAkB;AACpB;;AAEA;EACE,UAAU;AACZ;;AAEA;EACE,uBAAuB;AACzB;;AAEA;EACE,mBAAmB;EACnB,kBAAkB;AACpB;;AAEA;EACE,mBAAmB;AACrB;;AAEA;EACE;IACE,YAAY;EACd;AACF;;AAEA;EACE;IACE,WAAW;IACX,gBAAgB;EAClB;AACF;;AAEA;EACE,eAAe;AACjB;;AAEA;EACE,aAAa;EACb,YAAY;EACZ,iBAAiB;EACjB,kBAAkB;EAClB,yBAAyB;AAC3B;;AAEA;EACE,wDAAwD;EACxD,eAAe;EACf,cAAc;EACd,gBAAgB;AAClB;;AAEA;EACE,uBAAuB;EACvB,UAAU;EACV,cAAc;EACd,oBAAoB;AACtB;;AAEA;EACE,iBAAiB;EACjB,eAAe;AACjB;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,QAAQ;EACR,kBAAkB;AACpB;;AAEA;EACE,cAAc;EACd,gBAAgB;AAClB;;AAEA;EACE,cAAc;EACd,kBAAkB;AACpB;;AAEA;EACE,cAAc;AAChB;;AAEA;EACE,cAAc;EACd,gBAAgB;EAChB,gBAAgB;AAClB;;AAEA;EACE,kBAAkB;EAClB,aAAa;EACb,mBAAmB;EACnB,iBAAiB;AACnB;;AAEA;EACE,cAAc;EACd,eAAe;EACf,oBAAoB;EACpB,cAAc;AAChB;;AAEA;EACE,cAAc;EACd,qBAAqB;AACvB;;AAEA;EACE,eAAe;EACf,YAAY;EACZ,mBAAmB;EACnB,YAAY;EACZ,kBAAkB;EAClB,kBAAkB;EAClB,eAAe;EACf,gBAAgB;EAChB,0CAA0C;EAC1C,cAAc;EACd,kCAAkC;EAClC,oBAAoB;AACtB;;AAEA;EACE,WAAW;EACX,kBAAkB;EAClB,WAAW;EACX,SAAS;EACT,6BAA6B;EAC7B,2BAA2B;AAC7B;;AAEA;EACE;IACE,UAAU;IACV,0BAA0B;EAC5B;EACA;IACE,UAAU;IACV,wBAAwB;EAC1B;AACF","sourcesContent":[".left-sidebar {\n  position: fixed;\n  left: 0;\n  top: 48px;\n  bottom: 0;\n  background: white;\n  border-right: 1px solid #e5e7eb;\n  z-index: 1000;\n  display: flex;\n  flex-direction: column;\n  transition: transform 0.4s cubic-bezier(0.4, 0, 0.2, 1), box-shadow 0.4s ease;\n  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.05);\n  width: 320px;\n  transform: translateX(0);\n  overflow: visible;\n}\n\n.left-sidebar.collapsed {\n  transform: translateX(-100%);\n  box-shadow: none;\n}\n\n.left-sidebar.expanded {\n  transform: translateX(0);\n  animation: slideInFromLeft 0.4s cubic-bezier(0.4, 0, 0.2, 1);\n}\n\n@keyframes slideInFromLeft {\n  from {\n    transform: translateX(-100%);\n  }\n  to {\n    transform: translateX(0);\n  }\n}\n\n.left-sidebar-header {\n  display: flex;\n  align-items: center;\n  justify-content: space-between;\n  padding: 10px;\n  border-bottom: 1px solid #e5e7eb;\n  gap: 6px;\n  background: #f9fafb;\n  min-height: 52px;\n}\n\n.left-sidebar-tabs {\n  display: flex;\n  gap: 3px;\n  flex: 1;\n  min-width: 0;\n  background: white;\n  border-radius: 8px;\n  padding: 3px;\n  border: 1px solid #e5e7eb;\n  overflow: hidden;\n}\n\n.sidebar-tab {\n  flex: 1;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  gap: 4px;\n  padding: 6px 6px;\n  background: transparent;\n  border: none;\n  border-radius: 6px;\n  color: #64748b;\n  font-size: 10.5px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s;\n  white-space: nowrap;\n  min-width: 0;\n}\n\n.sidebar-tab span {\n  white-space: nowrap;\n}\n\n.sidebar-tab:hover {\n  background: #f8fafc;\n  color: #4e00ec;\n}\n\n.sidebar-tab.active {\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n  box-shadow: 0 2px 4px rgba(102, 126, 234, 0.3);\n}\n\n.sidebar-tab-info-tooltip-wrapper {\n  position: relative;\n  display: flex;\n  align-items: center;\n  margin-left: 4px;\n  cursor: help;\n}\n\n.sidebar-info-icon {\n  color: #94a3b8;\n  cursor: help;\n  transition: color 0.2s ease;\n  flex-shrink: 0;\n  pointer-events: auto;\n}\n\n.sidebar-tab:hover .sidebar-info-icon {\n  color: #4e00ec;\n}\n\n.sidebar-tab.active .sidebar-info-icon {\n  color: white;\n}\n\n.sidebar-tab-info-tooltip-wrapper:hover .sidebar-info-icon {\n  color: #4e00ec !important;\n}\n\n.sidebar-tab.active .sidebar-tab-info-tooltip-wrapper:hover .sidebar-info-icon {\n  color: #e0e7ff !important;\n}\n\n.sidebar-tab-info-tooltip {\n  position: fixed;\n  top: 60px;\n  left: 20px;\n  width: 320px;\n  max-width: calc(100vw - 40px);\n  padding: 14px 16px;\n  background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);\n  border: 2px solid #e0e7ff;\n  border-radius: 12px;\n  box-shadow: 0 8px 32px rgba(102, 126, 234, 0.25), 0 4px 12px rgba(0, 0, 0, 0.15);\n  font-size: 13px;\n  line-height: 1.6;\n  color: #334155;\n  opacity: 0;\n  visibility: hidden;\n  transition: opacity 0.3s ease, visibility 0.3s ease, transform 0.3s ease;\n  z-index: 9999;\n  pointer-events: none;\n  transform-origin: top left;\n  font-weight: 500;\n  white-space: normal;\n  word-wrap: break-word;\n  word-break: break-word;\n  overflow-wrap: break-word;\n  hyphens: auto;\n}\n\n.sidebar-tab-info-tooltip code {\n  background: #e0e7ff;\n  color: #4f46e5;\n  padding: 2px 6px;\n  border-radius: 4px;\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;\n  font-size: 12px;\n  font-weight: 600;\n  white-space: nowrap;\n}\n\n.sidebar-tab-info-tooltip-wrapper:hover .sidebar-tab-info-tooltip {\n  opacity: 1;\n  visibility: visible;\n  pointer-events: auto;\n  transform: translateY(2px);\n}\n\n.left-sidebar-toggle {\n  width: 32px;\n  min-width: 32px;\n  height: 32px;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 6px;\n  cursor: pointer;\n  color: #64748b;\n  transition: all 0.2s;\n  flex-shrink: 0;\n}\n\n.left-sidebar-toggle:hover {\n  background: #f8fafc;\n  border-color: #cbd5e1;\n  color: #4e00ec;\n}\n\n.left-sidebar-content {\n  flex: 1;\n  overflow-y: auto;\n  overflow-x: visible;\n  display: flex;\n  flex-direction: column;\n}\n\n.conversations-actions {\n  padding: 12px;\n  border-bottom: 1px solid #e5e7eb;\n  background: #f9fafb;\n}\n\n.new-conversation-btn {\n  width: 100%;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  gap: 8px;\n  padding: 10px 16px;\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  border: none;\n  border-radius: 8px;\n  color: white;\n  font-size: 13px;\n  font-weight: 600;\n  cursor: pointer;\n  transition: all 0.2s;\n  box-shadow: 0 2px 4px rgba(102, 126, 234, 0.2);\n}\n\n.new-conversation-btn:hover {\n  transform: translateY(-1px);\n  box-shadow: 0 4px 8px rgba(102, 126, 234, 0.3);\n}\n\n.new-conversation-btn:active {\n  transform: translateY(0);\n}\n\n.conversations-list {\n  display: flex;\n  flex-direction: column;\n  gap: 8px;\n  padding: 12px;\n  flex: 1;\n  overflow: visible;\n}\n\n.conversation-item {\n  background: #f8fafc;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  padding: 12px;\n  cursor: pointer;\n  transition: all 0.2s;\n  position: relative;\n  overflow: visible;\n}\n\n.conversation-item:hover {\n  background: #f1f5f9;\n  border-color: #cbd5e1;\n  transform: translateY(-1px);\n  box-shadow: 0 2px 8px rgba(102, 126, 234, 0.1);\n}\n\n.conversation-item.selected {\n  background: #eef2ff;\n  border-color: #667eea;\n  box-shadow: 0 0 0 2px rgba(102, 126, 234, 0.1);\n}\n\n.conversation-header {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  margin-bottom: 6px;\n  position: relative;\n  overflow: visible;\n}\n\n.conversation-header svg {\n  color: #667eea;\n  flex-shrink: 0;\n}\n\n.conversation-title {\n  font-size: 13px;\n  font-weight: 600;\n  color: #1e293b;\n  flex: 1;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.delete-conversation-btn {\n  background: none;\n  border: none;\n  color: #94a3b8;\n  cursor: pointer;\n  padding: 4px;\n  display: flex;\n  align-items: center;\n  border-radius: 4px;\n  transition: all 0.2s;\n  opacity: 0;\n}\n\n.conversation-item:hover .delete-conversation-btn {\n  opacity: 1;\n}\n\n.delete-conversation-btn:hover {\n  background: #fee2e2;\n  color: #ef4444;\n}\n\n.conversation-preview {\n  font-size: 12px;\n  color: #64748b;\n  line-height: 1.4;\n  margin-bottom: 6px;\n  display: -webkit-box;\n  -webkit-line-clamp: 2;\n  -webkit-box-orient: vertical;\n  overflow: hidden;\n}\n\n.conversation-date {\n  font-size: 11px;\n  color: #94a3b8;\n}\n\n.empty-state {\n  display: flex;\n  flex-direction: column;\n  align-items: center;\n  justify-content: center;\n  padding: 40px 20px;\n  text-align: center;\n  color: #94a3b8;\n  gap: 8px;\n}\n\n.empty-state svg {\n  opacity: 0.5;\n}\n\n.empty-state p {\n  margin: 0;\n  font-size: 14px;\n  font-weight: 600;\n  color: #64748b;\n}\n\n.empty-state small {\n  font-size: 12px;\n}\n\n.variables-wrapper {\n  flex: 1;\n  overflow: hidden;\n  position: relative;\n}\n\n.variables-wrapper .variables-sidebar {\n  position: relative !important;\n  left: 0 !important;\n  top: 0 !important;\n  height: 100%;\n  width: 100%;\n  border-right: none;\n  box-shadow: none;\n  transform: none !important;\n}\n\n.variables-wrapper .variables-sidebar-floating-toggle {\n  display: none;\n}\n\n.left-sidebar-floating-toggle {\n  position: fixed;\n  left: 0;\n  top: 50%;\n  transform: translateY(-50%);\n  width: 48px;\n  height: 64px;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-left: none;\n  border-radius: 0 8px 8px 0;\n  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.1);\n  cursor: pointer;\n  display: flex;\n  flex-direction: column;\n  align-items: center;\n  justify-content: center;\n  gap: 4px;\n  z-index: 999;\n  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);\n  color: #64748b;\n  animation: slideInToggle 0.5s cubic-bezier(0.4, 0, 0.2, 1);\n}\n\n.left-sidebar-floating-toggle:hover {\n  background: #f8fafc;\n  color: #4e00ec;\n  box-shadow: 2px 0 12px rgba(102, 126, 234, 0.2);\n  transform: translateY(-50%) translateX(4px);\n}\n\n@keyframes slideInToggle {\n  from {\n    transform: translateY(-50%) translateX(-100%);\n    opacity: 0;\n  }\n  to {\n    transform: translateY(-50%) translateX(0);\n    opacity: 1;\n  }\n}\n\n.sidebar-floating-count {\n  font-size: 11px;\n  font-weight: 600;\n  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n  color: white;\n  padding: 2px 6px;\n  border-radius: 10px;\n  min-width: 20px;\n  text-align: center;\n}\n\n.left-sidebar-content::-webkit-scrollbar {\n  width: 6px;\n}\n\n.left-sidebar-content::-webkit-scrollbar-track {\n  background: transparent;\n}\n\n.left-sidebar-content::-webkit-scrollbar-thumb {\n  background: #cbd5e1;\n  border-radius: 3px;\n}\n\n.left-sidebar-content::-webkit-scrollbar-thumb:hover {\n  background: #94a3b8;\n}\n\n@media (max-width: 768px) {\n  .left-sidebar {\n    width: 280px;\n  }\n}\n\n@media (max-width: 640px) {\n  .left-sidebar {\n    width: 100%;\n    max-width: 300px;\n  }\n}\n\n.flow-item {\n  cursor: default;\n}\n\n.flow-parameters {\n  margin: 8px 0;\n  padding: 8px;\n  background: white;\n  border-radius: 6px;\n  border: 1px solid #e5e7eb;\n}\n\n.flow-function-signature {\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;\n  font-size: 11px;\n  color: #1e293b;\n  line-height: 1.6;\n}\n\n.flow-function-signature code {\n  background: transparent;\n  padding: 0;\n  color: inherit;\n  font-family: inherit;\n}\n\n.flow-params-list {\n  margin-left: 12px;\n  margin-top: 4px;\n}\n\n.flow-param {\n  display: flex;\n  align-items: center;\n  gap: 4px;\n  margin-bottom: 2px;\n}\n\n.param-name {\n  color: #667eea;\n  font-weight: 600;\n}\n\n.param-type {\n  color: #64748b;\n  font-style: italic;\n}\n\n.param-default {\n  color: #10b981;\n}\n\n.param-required {\n  color: #ef4444;\n  font-weight: 600;\n  margin-left: 2px;\n}\n\n.flow-info-icon-wrapper {\n  position: relative;\n  display: flex;\n  align-items: center;\n  margin-left: auto;\n}\n\n.flow-info-icon {\n  color: #94a3b8;\n  cursor: pointer;\n  transition: all 0.2s;\n  flex-shrink: 0;\n}\n\n.flow-info-icon:hover {\n  color: #667eea;\n  transform: scale(1.1);\n}\n\n.flow-info-tooltip {\n  position: fixed;\n  width: 300px;\n  background: #1e293b;\n  color: white;\n  padding: 14px 16px;\n  border-radius: 8px;\n  font-size: 12px;\n  line-height: 1.5;\n  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);\n  z-index: 99999;\n  animation: tooltipFadeIn 0.2s ease;\n  pointer-events: auto;\n}\n\n.flow-info-tooltip::before {\n  content: '';\n  position: absolute;\n  right: 100%;\n  top: 20px;\n  border: 8px solid transparent;\n  border-right-color: #1e293b;\n}\n\n@keyframes tooltipFadeIn {\n  from {\n    opacity: 0;\n    transform: translateY(4px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n\n"],"sourceRoot":""}]);
// Exports
/* harmony default export */ __webpack_exports__["default"] = (___CSS_LOADER_EXPORT___);


/***/ })

}]);
//# sourceMappingURL=main-agentic_chat_src_D.e1b98f2c32244d988f1a.js.map