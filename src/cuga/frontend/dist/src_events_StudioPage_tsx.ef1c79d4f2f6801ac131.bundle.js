"use strict";
(self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] = self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] || []).push([["src_events_StudioPage_tsx"],{

/***/ "../node_modules/.pnpm/css-loader@7.1.4_webpack@5.104.1/node_modules/css-loader/dist/cjs.js!./src/events/StudioPage.css":
/*!******************************************************************************************************************************!*\
  !*** ../node_modules/.pnpm/css-loader@7.1.4_webpack@5.104.1/node_modules/css-loader/dist/cjs.js!./src/events/StudioPage.css ***!
  \******************************************************************************************************************************/
/***/ (function(module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_css_loader_7_1_4_webpack_5_104_1_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../../node_modules/.pnpm/css-loader@7.1.4_webpack@5.104.1/node_modules/css-loader/dist/runtime/sourceMaps.js */ "../node_modules/.pnpm/css-loader@7.1.4_webpack@5.104.1/node_modules/css-loader/dist/runtime/sourceMaps.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_4_webpack_5_104_1_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_4_webpack_5_104_1_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_4_webpack_5_104_1_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../../node_modules/.pnpm/css-loader@7.1.4_webpack@5.104.1/node_modules/css-loader/dist/runtime/api.js */ "../node_modules/.pnpm/css-loader@7.1.4_webpack@5.104.1/node_modules/css-loader/dist/runtime/api.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_4_webpack_5_104_1_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_4_webpack_5_104_1_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__);
// Imports


var ___CSS_LOADER_EXPORT___ = _node_modules_pnpm_css_loader_7_1_4_webpack_5_104_1_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default()((_node_modules_pnpm_css_loader_7_1_4_webpack_5_104_1_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default()));
// Module
___CSS_LOADER_EXPORT___.push([module.id, ".studio-page {\n  width: 100%;\n  display: flex;\n  flex-direction: column;\n  height: 100vh;\n}\n\n.studio-content {\n  flex: 1;\n  overflow: auto;\n  padding: 2rem 3rem;\n  margin-top: 3rem;\n  width: 100%;\n  max-width: 1200px;\n}\n\n.studio-heading-row {\n  display: flex;\n  justify-content: space-between;\n  align-items: flex-start;\n  margin-bottom: 1.5rem;\n}\n\n.studio-title {\n  font-size: 1.5rem;\n  font-weight: 400;\n  margin-bottom: 0.25rem;\n}\n\n.studio-muted {\n  color: #525252;\n  line-height: 1.5;\n  font-size: 0.875rem;\n}\n\n.studio-scope code,\n.studio-muted code {\n  background: #f4f4f4;\n  padding: 0 0.25rem;\n  border-radius: 2px;\n  font-size: 0.8125rem;\n}\n\n/* card grids for Channels / Integrations / Flows / Examples */\n.studio-grid {\n  display: grid;\n  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));\n  gap: 1rem;\n  margin-top: 1rem;\n}\n\n.studio-card {\n  display: flex;\n  flex-direction: column;\n  padding: 1.25rem;\n  min-height: 140px;\n}\n\n.studio-card-head {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  margin-bottom: 0.5rem;\n  gap: 0.5rem;\n}\n\n.studio-card-title {\n  display: flex;\n  align-items: center;\n  gap: 0.5rem;\n  font-weight: 600;\n}\n\n.studio-card-foot {\n  display: flex;\n  gap: 0.5rem;\n  align-items: center;\n  flex-wrap: wrap;\n  margin-top: auto;\n  padding-top: 0.75rem;\n}\n\n.studio-example-utterance {\n  font-style: italic;\n  color: #161616;\n  margin: 0.25rem 0 0.5rem;\n  line-height: 1.4;\n}\n\n/* per-agent example chips (Agents tab) — click to load into the Concierge */\n.studio-example-chip {\n  display: inline-block;\n  margin: 0 6px 6px 0;\n  padding: 3px 10px;\n  font-size: 12px;\n  font-style: italic;\n  color: #0f62fe;\n  background: #edf5ff;\n  border: 1px solid #d0e2ff;\n  border-radius: 14px;\n  cursor: pointer;\n  text-align: left;\n  line-height: 1.35;\n}\n.studio-example-chip:hover {\n  background: #d0e2ff;\n}\n\n/* concierge chat */\n.studio-chat {\n  display: flex;\n  flex-direction: column;\n  height: calc(100vh - 320px);\n  min-height: 360px;\n  margin-top: 1rem;\n}\n\n.studio-chat-log {\n  flex: 1;\n  overflow: auto;\n  padding: 0.5rem;\n  border: 1px solid #e0e0e0;\n  background: #f4f4f4;\n  margin-bottom: 1rem;\n}\n\n.studio-msg {\n  margin-bottom: 1rem;\n}\n\n.studio-msg-role {\n  font-weight: 600;\n  font-size: 0.8125rem;\n  margin-bottom: 0.25rem;\n  display: flex;\n  align-items: center;\n}\n\n.studio-msg-text {\n  margin: 0;\n  white-space: pre-wrap;\n  word-break: break-word;\n  font-family: inherit;\n  background: #ffffff;\n  padding: 0.75rem;\n  border-radius: 4px;\n  line-height: 1.5;\n}\n\n.studio-msg-user .studio-msg-text {\n  background: #edf5ff;\n}\n\n.studio-chat-input {\n  display: flex;\n  flex-direction: column;\n  gap: 0.5rem;\n}\n\n.studio-chat-actions {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n}\n\n/* ── HITL arming: the CONFIRM gate ────────────────────────────────────────────\n   Deliberately louder than a normal reply. This is the last moment a human sees\n   the instruction the agent will be handed on EVERY fire, so the prompt is the\n   visual anchor and the facts sit underneath it. Carbon blue-60 rail to read as\n   \"action needed\", not \"error\". */\n.studio-arm-card {\n  background: #ffffff;\n  border: 1px solid #c6c6c6;\n  border-left: 3px solid #0f62fe;\n  border-radius: 4px;\n  padding: 0.875rem 1rem;\n}\n\n.studio-arm-title {\n  font-weight: 600;\n  margin-bottom: 0.625rem;\n}\n\n.studio-arm-prompt-label {\n  font-size: 0.75rem;\n  letter-spacing: 0.02em;\n  text-transform: uppercase;\n  color: #525252;\n  margin-bottom: 0.25rem;\n}\n\n.studio-arm-prompt {\n  margin: 0 0 0.875rem;\n  padding: 0.625rem 0.75rem;\n  background: #f4f4f4;\n  border-left: 2px solid #8d8d8d;\n  font-style: italic;\n  line-height: 1.5;\n  white-space: pre-wrap;\n  word-break: break-word;\n}\n\n.studio-arm-facts {\n  display: grid;\n  grid-template-columns: auto 1fr;\n  gap: 0.25rem 0.75rem;\n  margin: 0 0 0.875rem;\n  font-size: 0.875rem;\n}\n\n.studio-arm-facts dt {\n  color: #525252;\n}\n\n.studio-arm-facts dd {\n  margin: 0;\n  word-break: break-word;\n}\n\n.studio-arm-actions {\n  display: flex;\n  flex-wrap: wrap;\n  gap: 0.5rem;\n}\n\n.studio-arm-stale {\n  margin: 0;\n  font-size: 0.75rem;\n}\n", "",{"version":3,"sources":["webpack://./src/events/StudioPage.css"],"names":[],"mappings":"AAAA;EACE,WAAW;EACX,aAAa;EACb,sBAAsB;EACtB,aAAa;AACf;;AAEA;EACE,OAAO;EACP,cAAc;EACd,kBAAkB;EAClB,gBAAgB;EAChB,WAAW;EACX,iBAAiB;AACnB;;AAEA;EACE,aAAa;EACb,8BAA8B;EAC9B,uBAAuB;EACvB,qBAAqB;AACvB;;AAEA;EACE,iBAAiB;EACjB,gBAAgB;EAChB,sBAAsB;AACxB;;AAEA;EACE,cAAc;EACd,gBAAgB;EAChB,mBAAmB;AACrB;;AAEA;;EAEE,mBAAmB;EACnB,kBAAkB;EAClB,kBAAkB;EAClB,oBAAoB;AACtB;;AAEA,8DAA8D;AAC9D;EACE,aAAa;EACb,4DAA4D;EAC5D,SAAS;EACT,gBAAgB;AAClB;;AAEA;EACE,aAAa;EACb,sBAAsB;EACtB,gBAAgB;EAChB,iBAAiB;AACnB;;AAEA;EACE,aAAa;EACb,8BAA8B;EAC9B,mBAAmB;EACnB,qBAAqB;EACrB,WAAW;AACb;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,WAAW;EACX,gBAAgB;AAClB;;AAEA;EACE,aAAa;EACb,WAAW;EACX,mBAAmB;EACnB,eAAe;EACf,gBAAgB;EAChB,oBAAoB;AACtB;;AAEA;EACE,kBAAkB;EAClB,cAAc;EACd,wBAAwB;EACxB,gBAAgB;AAClB;;AAEA,4EAA4E;AAC5E;EACE,qBAAqB;EACrB,mBAAmB;EACnB,iBAAiB;EACjB,eAAe;EACf,kBAAkB;EAClB,cAAc;EACd,mBAAmB;EACnB,yBAAyB;EACzB,mBAAmB;EACnB,eAAe;EACf,gBAAgB;EAChB,iBAAiB;AACnB;AACA;EACE,mBAAmB;AACrB;;AAEA,mBAAmB;AACnB;EACE,aAAa;EACb,sBAAsB;EACtB,2BAA2B;EAC3B,iBAAiB;EACjB,gBAAgB;AAClB;;AAEA;EACE,OAAO;EACP,cAAc;EACd,eAAe;EACf,yBAAyB;EACzB,mBAAmB;EACnB,mBAAmB;AACrB;;AAEA;EACE,mBAAmB;AACrB;;AAEA;EACE,gBAAgB;EAChB,oBAAoB;EACpB,sBAAsB;EACtB,aAAa;EACb,mBAAmB;AACrB;;AAEA;EACE,SAAS;EACT,qBAAqB;EACrB,sBAAsB;EACtB,oBAAoB;EACpB,mBAAmB;EACnB,gBAAgB;EAChB,kBAAkB;EAClB,gBAAgB;AAClB;;AAEA;EACE,mBAAmB;AACrB;;AAEA;EACE,aAAa;EACb,sBAAsB;EACtB,WAAW;AACb;;AAEA;EACE,aAAa;EACb,8BAA8B;EAC9B,mBAAmB;AACrB;;AAEA;;;;kCAIkC;AAClC;EACE,mBAAmB;EACnB,yBAAyB;EACzB,8BAA8B;EAC9B,kBAAkB;EAClB,sBAAsB;AACxB;;AAEA;EACE,gBAAgB;EAChB,uBAAuB;AACzB;;AAEA;EACE,kBAAkB;EAClB,sBAAsB;EACtB,yBAAyB;EACzB,cAAc;EACd,sBAAsB;AACxB;;AAEA;EACE,oBAAoB;EACpB,yBAAyB;EACzB,mBAAmB;EACnB,8BAA8B;EAC9B,kBAAkB;EAClB,gBAAgB;EAChB,qBAAqB;EACrB,sBAAsB;AACxB;;AAEA;EACE,aAAa;EACb,+BAA+B;EAC/B,oBAAoB;EACpB,oBAAoB;EACpB,mBAAmB;AACrB;;AAEA;EACE,cAAc;AAChB;;AAEA;EACE,SAAS;EACT,sBAAsB;AACxB;;AAEA;EACE,aAAa;EACb,eAAe;EACf,WAAW;AACb;;AAEA;EACE,SAAS;EACT,kBAAkB;AACpB","sourcesContent":[".studio-page {\n  width: 100%;\n  display: flex;\n  flex-direction: column;\n  height: 100vh;\n}\n\n.studio-content {\n  flex: 1;\n  overflow: auto;\n  padding: 2rem 3rem;\n  margin-top: 3rem;\n  width: 100%;\n  max-width: 1200px;\n}\n\n.studio-heading-row {\n  display: flex;\n  justify-content: space-between;\n  align-items: flex-start;\n  margin-bottom: 1.5rem;\n}\n\n.studio-title {\n  font-size: 1.5rem;\n  font-weight: 400;\n  margin-bottom: 0.25rem;\n}\n\n.studio-muted {\n  color: #525252;\n  line-height: 1.5;\n  font-size: 0.875rem;\n}\n\n.studio-scope code,\n.studio-muted code {\n  background: #f4f4f4;\n  padding: 0 0.25rem;\n  border-radius: 2px;\n  font-size: 0.8125rem;\n}\n\n/* card grids for Channels / Integrations / Flows / Examples */\n.studio-grid {\n  display: grid;\n  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));\n  gap: 1rem;\n  margin-top: 1rem;\n}\n\n.studio-card {\n  display: flex;\n  flex-direction: column;\n  padding: 1.25rem;\n  min-height: 140px;\n}\n\n.studio-card-head {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  margin-bottom: 0.5rem;\n  gap: 0.5rem;\n}\n\n.studio-card-title {\n  display: flex;\n  align-items: center;\n  gap: 0.5rem;\n  font-weight: 600;\n}\n\n.studio-card-foot {\n  display: flex;\n  gap: 0.5rem;\n  align-items: center;\n  flex-wrap: wrap;\n  margin-top: auto;\n  padding-top: 0.75rem;\n}\n\n.studio-example-utterance {\n  font-style: italic;\n  color: #161616;\n  margin: 0.25rem 0 0.5rem;\n  line-height: 1.4;\n}\n\n/* per-agent example chips (Agents tab) — click to load into the Concierge */\n.studio-example-chip {\n  display: inline-block;\n  margin: 0 6px 6px 0;\n  padding: 3px 10px;\n  font-size: 12px;\n  font-style: italic;\n  color: #0f62fe;\n  background: #edf5ff;\n  border: 1px solid #d0e2ff;\n  border-radius: 14px;\n  cursor: pointer;\n  text-align: left;\n  line-height: 1.35;\n}\n.studio-example-chip:hover {\n  background: #d0e2ff;\n}\n\n/* concierge chat */\n.studio-chat {\n  display: flex;\n  flex-direction: column;\n  height: calc(100vh - 320px);\n  min-height: 360px;\n  margin-top: 1rem;\n}\n\n.studio-chat-log {\n  flex: 1;\n  overflow: auto;\n  padding: 0.5rem;\n  border: 1px solid #e0e0e0;\n  background: #f4f4f4;\n  margin-bottom: 1rem;\n}\n\n.studio-msg {\n  margin-bottom: 1rem;\n}\n\n.studio-msg-role {\n  font-weight: 600;\n  font-size: 0.8125rem;\n  margin-bottom: 0.25rem;\n  display: flex;\n  align-items: center;\n}\n\n.studio-msg-text {\n  margin: 0;\n  white-space: pre-wrap;\n  word-break: break-word;\n  font-family: inherit;\n  background: #ffffff;\n  padding: 0.75rem;\n  border-radius: 4px;\n  line-height: 1.5;\n}\n\n.studio-msg-user .studio-msg-text {\n  background: #edf5ff;\n}\n\n.studio-chat-input {\n  display: flex;\n  flex-direction: column;\n  gap: 0.5rem;\n}\n\n.studio-chat-actions {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n}\n\n/* ── HITL arming: the CONFIRM gate ────────────────────────────────────────────\n   Deliberately louder than a normal reply. This is the last moment a human sees\n   the instruction the agent will be handed on EVERY fire, so the prompt is the\n   visual anchor and the facts sit underneath it. Carbon blue-60 rail to read as\n   \"action needed\", not \"error\". */\n.studio-arm-card {\n  background: #ffffff;\n  border: 1px solid #c6c6c6;\n  border-left: 3px solid #0f62fe;\n  border-radius: 4px;\n  padding: 0.875rem 1rem;\n}\n\n.studio-arm-title {\n  font-weight: 600;\n  margin-bottom: 0.625rem;\n}\n\n.studio-arm-prompt-label {\n  font-size: 0.75rem;\n  letter-spacing: 0.02em;\n  text-transform: uppercase;\n  color: #525252;\n  margin-bottom: 0.25rem;\n}\n\n.studio-arm-prompt {\n  margin: 0 0 0.875rem;\n  padding: 0.625rem 0.75rem;\n  background: #f4f4f4;\n  border-left: 2px solid #8d8d8d;\n  font-style: italic;\n  line-height: 1.5;\n  white-space: pre-wrap;\n  word-break: break-word;\n}\n\n.studio-arm-facts {\n  display: grid;\n  grid-template-columns: auto 1fr;\n  gap: 0.25rem 0.75rem;\n  margin: 0 0 0.875rem;\n  font-size: 0.875rem;\n}\n\n.studio-arm-facts dt {\n  color: #525252;\n}\n\n.studio-arm-facts dd {\n  margin: 0;\n  word-break: break-word;\n}\n\n.studio-arm-actions {\n  display: flex;\n  flex-wrap: wrap;\n  gap: 0.5rem;\n}\n\n.studio-arm-stale {\n  margin: 0;\n  font-size: 0.75rem;\n}\n"],"sourceRoot":""}]);
// Exports
/* harmony default export */ __webpack_exports__["default"] = (___CSS_LOADER_EXPORT___);


/***/ }),

/***/ "./src/events/ConciergeChat.tsx":
/*!**************************************!*\
  !*** ./src/events/ConciergeChat.tsx ***!
  \**************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   ConciergeChat: function() { return /* binding */ ConciergeChat; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _carbon_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! @carbon/react */ "../node_modules/.pnpm/@carbon+react@1.107.1_react-dom@18.3.1_react@18.3.1__react-is@19.2.6_react@18.3.1_sass@1.99.0/node_modules/@carbon/react/es/index.js");
/* harmony import */ var _carbon_icons_react__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! @carbon/icons-react */ "../node_modules/.pnpm/@carbon+icons-react@11.80.0_react@18.3.1/node_modules/@carbon/icons-react/es/index.js");
/* harmony import */ var _api__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ../api */ "./src/api.ts");





/**
 * ConciergeChat — a DUMB chat surface over POST /api/concierge.
 *
 * It carries no business logic: it sends the text, shows the reply (or the
 * dry-run plan), and lists the exchange. All reuse/create/classify/arm
 * decisions happen server-side in the concierge. ``draft``/``setDraft`` are
 * lifted so the Examples tab can prefill this box.
 */

/** The thread this surface talks on. It is also the delivery address a fire comes back to. */
const THREAD_ID = "web:studio";
function ConciergeChat({
  draft,
  setDraft
}) {
  const [messages, setMessages] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [busy, setBusy] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [error, setError] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [dryRun, setDryRun] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  /** Mailbox cursor — the ts of the last fire rendered. Ref, not state: it must not re-trigger the poll. */
  const cursor = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)(0);

  // ── Asynchronous flow fires ─────────────────────────────────────────────────
  // Arming is only half the loop. The flow fires later — a cron tick at 09:05, a poll that finally
  // saw a change — with no request in flight to answer. Slack gets a push; a browser can only be
  // drained, so the server delivers into a per-thread mailbox and we poll it. Without this the flow
  // ran, the dashboard knew, and this chat never heard back.
  //
  // `since=0` on the first pass is deliberate: it recovers everything that fired while the tab was
  // closed, so a reopened Studio shows the fires it missed instead of losing them.
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    let cancelled = false;
    const drain = async () => {
      try {
        const res = await _api__WEBPACK_IMPORTED_MODULE_3__.getEventsInbox(THREAD_ID, cursor.current);
        if (!res.ok || cancelled) return;
        const data = await res.json();
        const msgs = data?.messages ?? [];
        if (!msgs.length || cancelled) return;
        cursor.current = data.cursor ?? cursor.current;
        setMessages(m => [...m, ...msgs.map(x => ({
          role: "concierge",
          text: String(x.text ?? ""),
          meta: x.flow_name ? `flow · ${x.flow_name}` : "flow fired",
          fire: true
        }))]);
      } catch {
        /* a mailbox that is unreachable must never break the chat */
      }
    };
    drain();
    const id = setInterval(drain, 15000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);
  const send = async override => {
    const text = (override ?? draft).trim();
    if (!text || busy) return;
    setError(null);
    setBusy(true);
    setMessages(m => [...m, {
      role: "user",
      text
    }]);
    if (override === undefined) setDraft("");
    try {
      const res = await _api__WEBPACK_IMPORTED_MODULE_3__.postConcierge(text, {
        threadId: THREAD_ID,
        dryRun
      });
      const data = await res.json();
      if (!res.ok && !data?.plan && !data?.reply) {
        throw new Error(data?.error || res.statusText);
      }
      // The server owns the shape: live → {reply}, dry-run → {decision, ...}.
      let reply;
      let meta;
      if (dryRun) {
        const mode = data?.decision?.mode ?? data?.plan?.decision?.mode ?? "?";
        reply = "```json\n" + JSON.stringify(data.decision ?? data.plan ?? data, null, 2) + "\n```";
        meta = `dry-run · mode=${mode}`;
      } else {
        reply = typeof data.reply === "string" ? data.reply : JSON.stringify(data.reply ?? data, null, 2);
        meta = data?.scope ? `scope=${data.scope}` : undefined;
      }
      // HITL arming: `state` says whether this thread is mid-dialogue. When the server is at the
      // CONFIRM gate it also sends the proposal, which we render as a card with real buttons —
      // the whole point is that the human sees the exact fire-time prompt before anything arms.
      setMessages(m => [...m, {
        role: "concierge",
        text: reply,
        meta,
        state: data?.state,
        summary: data?.summary
      }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Concierge request failed");
    } finally {
      setBusy(false);
    }
  };

  // The newest message that is part of the arming dialogue (fires arrive out-of-band and don't count).
  const lastDialogueIndex = messages.reduce((best, m, i) => m.fire ? best : i, -1);
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-chat"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-chat-log"
  }, messages.length === 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted"
  }, "Tell the concierge what you want \u2014 e.g. ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("em", null, "\"every 1 minute send me new arXiv papers on mixture-of-experts\""), ". It reuses or creates a worker and arms the trigger. Toggle ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", null, "Preview"), " to see the plan without side effects.", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("br", null), "Or type ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, "/automate <what>"), " \u2014 one command whose router picks push / cron / poll for you: ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("em", null, "\"/automate summarize new emails\""), " (push),", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("em", null, "\"/automate the market brief every weekday 8am\""), " (cron),", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("em", null, "\"/automate check bitcoin every 5 min on a move\""), " (poll)."), messages.map((m, i) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: i,
    className: `studio-msg studio-msg-${m.role}${m.fire ? " studio-msg-fire" : ""}`
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-msg-role"
  }, m.role === "user" ? "You" : m.fire ? "⚡ Flow" : "Concierge", m.meta && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_1__.Tag, {
    type: m.role === "user" ? "gray" : m.fire ? "purple" : "green",
    size: "sm",
    style: {
      marginLeft: 8
    }
  }, m.meta)), m.state === "confirm" && m.summary ?
  /*#__PURE__*/
  // "live" is the newest message of the DIALOGUE — a flow firing mid-arming is not a
  // reply and must not retire the card the human is still looking at.
  react__WEBPACK_IMPORTED_MODULE_0___default().createElement(ArmConfirmCard, {
    summary: m.summary,
    busy: busy,
    live: i === lastDialogueIndex,
    onSay: t => send(t),
    setDraft: setDraft
  }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("pre", {
    className: "studio-msg-text"
  }, m.text))), busy && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_1__.InlineLoading, {
    description: "Concierge is thinking\u2026"
  })), error && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_1__.InlineNotification, {
    kind: "error",
    title: "Error",
    subtitle: error,
    lowContrast: true,
    onCloseButtonClick: () => setError(null)
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-chat-input"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_1__.TextArea, {
    labelText: "",
    hideLabel: true,
    placeholder: "Ask the concierge\u2026  (or /automate <what> to arm a flow)",
    rows: 2,
    value: draft,
    onChange: e => setDraft(e.target.value),
    onKeyDown: e => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        send();
      }
    }
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-chat-actions"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_1__.Toggle, {
    id: "studio-dryrun",
    size: "sm",
    labelText: "",
    labelA: "Live",
    labelB: "Preview",
    toggled: dryRun,
    onToggle: v => setDryRun(v)
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_1__.Button, {
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_2__.Send,
    onClick: () => send(),
    disabled: busy || !draft.trim()
  }, "Send"))));
}

/**
 * ArmConfirmCard — the CONFIRM gate, rendered.
 *
 * Nothing is armed until the human approves the exact prompt the agent will be handed on every
 * fire (events/docs/plans/SPLIT_AND_HITL_ARMING_SPEC.md §5). The card exists so that prompt is
 * impossible to miss: it is the one thing a bad automation gets wrong, forever, silently.
 *
 * The buttons are shortcuts, not a separate protocol — each sends the same plain text a user could
 * type ("yes" / "cancel"), so web, Slack, Discord and Telegram all drive one dialogue. Only the
 * newest card stays interactive; older ones are history.
 */
function ArmConfirmCard({
  summary,
  busy,
  live,
  onSay,
  setDraft
}) {
  const rows = [["When", summary.trigger], ["Results go to", summary.delivery], ["Agent", summary.agent]];
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-arm-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-arm-title"
  }, "Ready to arm \u2014 check this first"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-arm-prompt-label"
  }, "The agent will be asked, every time:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("blockquote", {
    className: "studio-arm-prompt"
  }, summary.prompt), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("dl", {
    className: "studio-arm-facts"
  }, rows.map(([k, v]) => v ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), {
    key: k
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("dt", null, k), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("dd", null, v)) : null)), live ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-arm-actions"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_1__.Button, {
    size: "sm",
    disabled: busy,
    onClick: () => onSay("yes")
  }, "Arm it"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_1__.Button, {
    size: "sm",
    kind: "tertiary",
    disabled: busy,
    onClick: () => setDraft(`change the prompt to ${summary.prompt ?? ""}`)
  }, "Edit prompt"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_1__.Button, {
    size: "sm",
    kind: "ghost",
    disabled: busy,
    onClick: () => onSay("cancel")
  }, "Cancel")) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted studio-arm-stale"
  }, "Superseded by a later message."));
}

/***/ }),

/***/ "./src/events/StudioPage.css":
/*!***********************************!*\
  !*** ./src/events/StudioPage.css ***!
  \***********************************/
/***/ (function(__unused_webpack_module, __unused_webpack___webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! !../../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.104.1/node_modules/style-loader/dist/runtime/injectStylesIntoStyleTag.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.104.1/node_modules/style-loader/dist/runtime/injectStylesIntoStyleTag.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! !../../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.104.1/node_modules/style-loader/dist/runtime/styleDomAPI.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.104.1/node_modules/style-loader/dist/runtime/styleDomAPI.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! !../../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.104.1/node_modules/style-loader/dist/runtime/insertBySelector.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.104.1/node_modules/style-loader/dist/runtime/insertBySelector.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! !../../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.104.1/node_modules/style-loader/dist/runtime/setAttributesWithoutAttributes.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.104.1/node_modules/style-loader/dist/runtime/setAttributesWithoutAttributes.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! !../../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.104.1/node_modules/style-loader/dist/runtime/insertStyleElement.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.104.1/node_modules/style-loader/dist/runtime/insertStyleElement.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! !../../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.104.1/node_modules/style-loader/dist/runtime/styleTagTransform.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.104.1/node_modules/style-loader/dist/runtime/styleTagTransform.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_4_webpack_5_104_1_node_modules_css_loader_dist_cjs_js_StudioPage_css__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! !!../../../node_modules/.pnpm/css-loader@7.1.4_webpack@5.104.1/node_modules/css-loader/dist/cjs.js!./StudioPage.css */ "../node_modules/.pnpm/css-loader@7.1.4_webpack@5.104.1/node_modules/css-loader/dist/cjs.js!./src/events/StudioPage.css");

      
      
      
      
      
      
      
      
      

var options = {};

options.styleTagTransform = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default());
options.setAttributes = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default());
options.insert = _node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default().bind(null, "head");
options.domAPI = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default());
options.insertStyleElement = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default());

var update = _node_modules_pnpm_style_loader_4_0_0_webpack_5_104_1_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default()(_node_modules_pnpm_css_loader_7_1_4_webpack_5_104_1_node_modules_css_loader_dist_cjs_js_StudioPage_css__WEBPACK_IMPORTED_MODULE_6__["default"], options);




       /* unused harmony default export */ var __WEBPACK_DEFAULT_EXPORT__ = (_node_modules_pnpm_css_loader_7_1_4_webpack_5_104_1_node_modules_css_loader_dist_cjs_js_StudioPage_css__WEBPACK_IMPORTED_MODULE_6__["default"] && _node_modules_pnpm_css_loader_7_1_4_webpack_5_104_1_node_modules_css_loader_dist_cjs_js_StudioPage_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals ? _node_modules_pnpm_css_loader_7_1_4_webpack_5_104_1_node_modules_css_loader_dist_cjs_js_StudioPage_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals : undefined);


/***/ }),

/***/ "./src/events/StudioPage.tsx":
/*!***********************************!*\
  !*** ./src/events/StudioPage.tsx ***!
  \***********************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   StudioPage: function() { return /* binding */ StudioPage; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var react_router_dom__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! react-router-dom */ "../node_modules/.pnpm/react-router-dom@7.18.2_react-dom@18.3.1_react@18.3.1__react@18.3.1/node_modules/react-router-dom/dist/index.mjs");
/* harmony import */ var _carbon_react__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! @carbon/react */ "../node_modules/.pnpm/@carbon+react@1.107.1_react-dom@18.3.1_react@18.3.1__react-is@19.2.6_react@18.3.1_sass@1.99.0/node_modules/@carbon/react/es/index.js");
/* harmony import */ var _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! @carbon/icons-react */ "../node_modules/.pnpm/@carbon+icons-react@11.80.0_react@18.3.1/node_modules/@carbon/icons-react/es/index.js");
/* harmony import */ var _api__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! ../api */ "./src/api.ts");
/* harmony import */ var _CugaHeader__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! ../CugaHeader */ "./src/CugaHeader.tsx");
/* harmony import */ var _ConciergeChat__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! ./ConciergeChat */ "./src/events/ConciergeChat.tsx");
/* harmony import */ var _StudioPage_css__WEBPACK_IMPORTED_MODULE_7__ = __webpack_require__(/*! ./StudioPage.css */ "./src/events/StudioPage.css");









// ---- small dumb render helpers ------------------------------------------------
const STATUS_TAG = {
  connected: {
    type: "green",
    label: "connected"
  },
  not_connected: {
    type: "gray",
    label: "not connected"
  },
  auto_connect_pending: {
    type: "cyan",
    label: "auto-connect pending"
  },
  not_configured: {
    type: "gray",
    label: "not configured"
  },
  ap_not_configured: {
    type: "gray",
    label: "AP not configured"
  },
  unknown: {
    type: "cool-gray",
    label: "unknown"
  }
};
function StatusTag({
  status
}) {
  const s = STATUS_TAG[status] ?? {
    type: "gray",
    label: status
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: s.type,
    size: "sm"
  }, s.label);
}
const MODE_TAG = {
  NOW: "blue",
  CRON: "purple",
  POLL: "teal",
  PUSH: "magenta"
};
const RUN_STATUS_TAG = {
  SUCCEEDED: "green",
  FAILED: "red",
  RUNNING: "blue",
  PAUSED: "gray",
  STOPPED: "gray",
  TIMEOUT: "red",
  INTERNAL_ERROR: "red"
};
function fmtTime(s) {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}

// subscription.created_at is epoch SECONDS (a float); AP timestamps are ISO strings — hence two helpers.
function fmtEpoch(secs) {
  if (!secs) return "—";
  try {
    return new Date(secs * 1000).toLocaleString();
  } catch {
    return String(secs);
  }
}

// router outcomes shown on Examples
const OUTCOME_TAG = {
  "answer-now": "blue",
  "flow-cron": "purple",
  "flow-poll": "teal",
  connect: "cyan",
  decline: "gray"
};

// ---- data hook (dumb fetch → render) -----------------------------------------
function useEndpoint(fn, pick, dep) {
  const [data, setData] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [loading, setLoading] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(true);
  const [error, setError] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fn()
    // Name the STATUS and the PATH. "error fetching" on its own sent us to the browser console
    // and then to the server logs to discover a 500 from a dropped database connection; the
    // status code alone would have said "server-side, not your session" immediately.
    .then(r => {
      if (!r.ok) {
        const where = (() => {
          try {
            return new URL(r.url).pathname;
          } catch {
            return "";
          }
        })();
        throw new Error(`HTTP ${r.status}${r.statusText ? " " + r.statusText : ""}${where ? ` — ${where}` : ""}`);
      }
      return r.json();
    }).then(d => {
      if (!cancelled) setData(pick(d));
    }).catch(e => {
      if (cancelled) return;
      const msg = e instanceof Error ? e.message : "Failed to load";
      // A bare TypeError from fetch means the request never completed: CORS, DNS, or the events
      // service being down — all of which look identical to the user without saying so.
      setError(/failed to fetch|networkerror|load failed/i.test(msg) ? `${msg} — the eventing service may be unreachable (check EVENTS_API_URL and CORS)` : msg);
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dep]);
  return {
    data,
    loading,
    error
  };
}
function Loader({
  loading,
  error
}) {
  if (loading) return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.InlineLoading, {
    description: "Loading\u2026"
  });
  if (error) return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.InlineNotification, {
    kind: "error",
    title: "Error",
    subtitle: error,
    lowContrast: true,
    hideCloseButton: true
  });
  return null;
}

// ---- tabs --------------------------------------------------------------------
const KNOWN_CHANNELS = ["web", "telegram", "slack", "discord"];
// Fallback only — the editor derives the live list (and each app's triggers) from
// /api/events/triggers, the backend registry, so the UI can never drift from the code.
const KNOWN_INTEGRATIONS = ["box", "discord", "github", "gmail", "slack", "telegram", "webhook"];
// Fallback tool catalog so the picker is NEVER empty (used if /api/events/mcp-servers is
// unreachable, e.g. an older running server). Enriched with live hints when the fetch succeeds.
const FALLBACK_MCP = [{
  name: "cuga-web",
  hint: "web search / browse / weather / wiki"
}, {
  name: "cuga-finance",
  hint: "stock quote / crypto price"
}, {
  name: "cuga-knowledge",
  hint: "search arXiv (recent papers)"
}, {
  name: "cuga-geo",
  hint: "country capital / population / region"
}, {
  name: "cuga-text",
  hint: "summarize / translate / text utilities"
}, {
  name: "cuga-code",
  hint: "explain / analyze code"
}, {
  name: "cuga-local",
  hint: "local / system operations"
}];

// The Add/Edit agent form. A builder defines an agent = skill (prompt) + tools (mcp_servers) +
// the connectors it may use (channels to converse on, integrations to watch/act on). Saving
// upserts by name (POST for new, PUT for existing).
function AgentEditor({
  open,
  editing,
  onClose
}) {
  const isEdit = !!editing;
  const [name, setName] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("");
  const [backend, setBackend] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("cuga");
  const [prompt, setPrompt] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("");
  const [mcp, setMcp] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [channels, setChannels] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(["web"]);
  const [integrations, setIntegrations] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({});
  // trigger-grain: app → the trigger events this agent handles ([] = ALL of the app's triggers).
  // This used to be dropped on save, silently widening an agent to every trigger of the app.
  const [trigs, setTrigs] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({});
  // the registry (app → its triggers) from /api/events/triggers; drives the picker below
  const [registry, setRegistry] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({});
  const [access, setAccess] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("");
  const [servers, setServers] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [saving, setSaving] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [err, setErr] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    if (!open) return;
    setErr(null);
    setSaving(false);
    setName(editing?.name ?? "");
    setBackend(editing?.backend || "cuga");
    setPrompt(editing?.prompt ?? "");
    setMcp(editing?.mcp_servers ?? []);
    setChannels(editing?.channels?.length ? editing.channels : ["web"]);
    const im = {};
    const tm = {};
    (editing?.integrations ?? []).forEach(i => {
      im[i.app] = i.ownership || "per-user";
      tm[i.app] = Array.isArray(i.triggers) ? i.triggers : [];
    });
    setIntegrations(im);
    setTrigs(tm);
    setAccess((editing?.access ?? []).join(", "));
    setServers(FALLBACK_MCP); // always have a catalog; enrich from the server if reachable
    _api__WEBPACK_IMPORTED_MODULE_4__.getEventsMcpServers().then(r => r.json()).then(d => {
      if (d.servers?.length) setServers(d.servers);
    }).catch(() => {});
    _api__WEBPACK_IMPORTED_MODULE_4__.getEventsTriggers().then(r => r.json()).then(d => {
      const reg = {};
      (d.apps ?? []).forEach(a => {
        reg[a.app] = a.triggers ?? [];
      });
      setRegistry(reg);
    }).catch(() => {});
  }, [open, editing]);
  const toggle = (list, set, v) => set(list.includes(v) ? list.filter(x => x !== v) : [...list, v]);
  const save = () => {
    setSaving(true);
    setErr(null);
    const spec = {
      name: name.trim(),
      backend,
      prompt,
      mcp_servers: mcp,
      channels,
      integrations: Object.entries(integrations).map(([app, ownership]) => ({
        app,
        ownership,
        ...(trigs[app]?.length ? {
          triggers: trigs[app]
        } : {})
      })),
      access: access.split(",").map(s => s.trim()).filter(Boolean)
    };
    const req = isEdit ? _api__WEBPACK_IMPORTED_MODULE_4__.putEventsAgent(editing.name, spec) : _api__WEBPACK_IMPORTED_MODULE_4__.postEventsAgent(spec);
    req.then(r => r.json()).then(res => {
      if (res.ok) {
        onClose(true);
      } else {
        setErr(res.error || "save failed");
        setSaving(false);
      }
    }).catch(e => {
      setErr(String(e));
      setSaving(false);
    });
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Modal, {
    open: open,
    modalHeading: isEdit ? `Edit agent · ${editing?.name}` : "Add agent",
    primaryButtonText: saving ? "Saving…" : "Save",
    secondaryButtonText: "Cancel",
    primaryButtonDisabled: saving || !name.trim(),
    onRequestClose: () => onClose(false),
    onRequestSubmit: save
  }, err && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.InlineNotification, {
    kind: "error",
    title: "Error",
    subtitle: err,
    lowContrast: true,
    hideCloseButton: true
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 16
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TextInput, {
    id: "agent-name",
    labelText: "Name",
    value: name,
    disabled: isEdit,
    placeholder: "e.g. support_digest",
    onChange: e => setName(e.target.value)
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Select, {
    id: "agent-backend",
    labelText: "Backend",
    value: backend,
    onChange: e => setBackend(e.target.value)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.SelectItem, {
    value: "cuga",
    text: "cuga \u2014 full CUGA worker (tools + web)"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.SelectItem, {
    value: "react",
    text: "react \u2014 lightweight ReAct agent"
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TextArea, {
    id: "agent-prompt",
    labelText: "Skill (prompt)",
    rows: 4,
    value: prompt,
    placeholder: "What this agent does and how it should answer.",
    onChange: e => setPrompt(e.target.value)
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "cds--label"
  }, "Tools (MCP servers)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: "2px 16px"
    }
  }, servers.map(s => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Checkbox, {
    key: s.name,
    id: `mcp-${s.name}`,
    checked: mcp.includes(s.name),
    labelText: `${s.name}${s.hint ? " — " + s.hint : ""}`,
    onChange: () => toggle(mcp, setMcp, s.name)
  })))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "cds--label"
  }, "Channels (converse on)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      gap: 16,
      flexWrap: "wrap"
    }
  }, KNOWN_CHANNELS.map(ch => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Checkbox, {
    key: ch,
    id: `ch-${ch}`,
    labelText: ch,
    checked: channels.includes(ch),
    onChange: () => toggle(channels, setChannels, ch)
  })))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "cds--label"
  }, "Integrations (watch / act on)"), (Object.keys(registry).length ? Object.keys(registry).sort() : KNOWN_INTEGRATIONS).map(app => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: app,
    style: {
      marginBottom: 4
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 12
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Checkbox, {
    id: `int-${app}`,
    labelText: app,
    checked: app in integrations,
    onChange: (_, {
      checked
    }) => setIntegrations(prev => {
      const next = {
        ...prev
      };
      if (checked) next[app] = next[app] || "per-user";else delete next[app];
      return next;
    })
  }), app in integrations && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Select, {
    id: `own-${app}`,
    labelText: "",
    size: "sm",
    value: integrations[app],
    inline: true,
    onChange: e => setIntegrations(prev => ({
      ...prev,
      [app]: e.target.value
    }))
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.SelectItem, {
    value: "per-user",
    text: "per-user (each user logs in)"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.SelectItem, {
    value: "shared",
    text: "shared (one service account)"
  }))), app in integrations && (registry[app]?.length ?? 0) > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      margin: "2px 0 6px 28px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-muted",
    style: {
      fontSize: 12,
      marginBottom: 2
    }
  }, "Triggers \u2014 ", trigs[app]?.length ? `${trigs[app].length} of ${registry[app].length} selected` : `all ${registry[app].length} (none selected = all)`), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: "0 16px"
    }
  }, registry[app].map(t => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Checkbox, {
    key: t.event,
    id: `trg-${app}-${t.event}`,
    labelText: `${t.event} — ${t.title}`,
    checked: (trigs[app] ?? []).includes(t.event),
    onChange: () => setTrigs(prev => {
      const cur = prev[app] ?? [];
      const next = cur.includes(t.event) ? cur.filter(e => e !== t.event) : [...cur, t.event];
      return {
        ...prev,
        [app]: next
      };
    })
  }))))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TextInput, {
    id: "agent-access",
    labelText: "Access (roles / user-ids, comma-separated \xB7 blank = everyone)",
    value: access,
    placeholder: "e.g. builder, admin",
    onChange: e => setAccess(e.target.value)
  })));
}
function AgentsTab({
  refresh,
  onTry
}) {
  const [localBump, setLocalBump] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(0);
  const {
    data,
    loading,
    error
  } = useEndpoint(_api__WEBPACK_IMPORTED_MODULE_4__.getEventsAgents, d => d.agents ?? [], refresh + localBump);
  const mcp = useEndpoint(_api__WEBPACK_IMPORTED_MODULE_4__.getEventsMcpServers, d => d, refresh);
  const explorerUrl = mcp.data?.explorer_url || "http://localhost:8001/docs";
  const mcpServers = mcp.data?.servers || [];
  const [editorOpen, setEditorOpen] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [editing, setEditing] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const openAdd = () => {
    setEditing(null);
    setEditorOpen(true);
  };
  const openEdit = a => {
    setEditing(a);
    setEditorOpen(true);
  };
  const onClose = saved => {
    setEditorOpen(false);
    if (saved) setLocalBump(n => n + 1);
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      marginBottom: 10
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      margin: 0
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, "One agent \u2014 CUGA."), " These are its sub-agents (the roster), defined in", " ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, "events/examples/rosters/default.yaml"), " \u2014 edit the file and ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, "make reload"), " to change them. CUGA routes to the right specialist internally; nothing here is addressed directly.")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-muted",
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8,
      flexWrap: "wrap",
      marginBottom: 16,
      fontSize: 13,
      padding: "8px 12px",
      border: "1px solid var(--cds-border-subtle, #e0e0e0)",
      borderRadius: 6
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Plug, {
    size: 16
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Agents draw on ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, "MCP tool servers"), " \u2014 browse every server's tools (and try them) in the"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    kind: "ghost",
    size: "sm",
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Launch,
    href: explorerUrl,
    target: "_blank"
  }, "MCP tool explorer"), mcpServers.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    style: {
      opacity: 0.85
    }
  }, "\xB7 ", mcpServers.map(s => s.name).join(", "))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(Loader, {
    loading: loading,
    error: error
  }), !loading && !error && (data?.length ?? 0) === 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.InlineNotification, {
    kind: "info",
    lowContrast: true,
    hideCloseButton: true,
    title: "No sub-agents loaded",
    subtitle: "The roster lives in events/examples/rosters/default.yaml \u2014 edit it and run make reload. (There is one agent, CUGA; these are its sub-agents.)"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-grid"
  }, data?.map(a => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tile, {
    key: a.name,
    className: "studio-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-card-head"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "studio-card-title"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Bot, {
    size: 18
  }), " ", a.name), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: a.backend === "cuga" ? "blue" : "cool-gray",
    size: "sm"
  }, a.backend)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted"
  }, a.prompt || "—"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-card-foot"
  }, a.mcp_servers?.map(m => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    key: m,
    type: "teal",
    size: "sm"
  }, m)), a.channels?.map(c => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    key: c,
    type: "outline",
    size: "sm"
  }, c)), a.integrations?.map(i => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    key: i.app,
    type: "purple",
    size: "sm",
    title: i.triggers?.length ? i.triggers.join(", ") : "all triggers"
  }, i.app, " (", i.ownership, ")", i.triggers?.length ? ` · ${i.triggers.length} trigger${i.triggers.length > 1 ? "s" : ""}` : "")), a.restricted && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: a.can_use ? "green" : "red",
    size: "sm"
  }, a.can_use ? "restricted · you can use" : "restricted")), (a.examples?.length ?? 0) > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      marginTop: 10
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-muted",
    style: {
      fontSize: 12,
      marginBottom: 4
    }
  }, "Try:"), a.examples.map(u => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    key: u,
    className: "studio-example-chip",
    title: "Load into Concierge",
    onClick: () => onTry(u)
  }, "\"", u, "\"")))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(AgentEditor, {
    open: editorOpen,
    editing: editing,
    onClose: onClose
  }));
}
function ChannelsTab({
  refresh
}) {
  const {
    data,
    loading,
    error
  } = useEndpoint(_api__WEBPACK_IMPORTED_MODULE_4__.getEventsChannels, d => d.channels ?? [], refresh);
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-grid"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(Loader, {
    loading: loading,
    error: error
  }), data?.map(c => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tile, {
    key: c.name,
    className: "studio-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-card-head"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "studio-card-title"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Chat, {
    size: 18
  }), " ", c.label), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(StatusTag, {
    status: c.status
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted"
  }, c.note), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-card-foot"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: "outline",
    size: "sm"
  }, "converse"), c.backend && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: c.backend === "direct" ? "cyan" : "teal",
    size: "sm"
  }, c.backend === "direct" ? "direct" : "Activepieces"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: "blue",
    size: "sm"
  }, "TENANT \u2014 shared bot")))));
}
function IntegrationsTab({
  refresh
}) {
  const {
    data,
    loading,
    error
  } = useEndpoint(_api__WEBPACK_IMPORTED_MODULE_4__.getEventsIntegrations, d => d.integrations ?? [], refresh);
  // the trigger registry, so each card can say WHAT the integration can watch
  const reg = useEndpoint(_api__WEBPACK_IMPORTED_MODULE_4__.getEventsTriggers, d => d.apps ?? [], refresh);
  const trigsFor = app => (reg.data ?? []).find(a => a.app === app)?.triggers ?? [];

  // "log in with your own account" — OAuth apps open the consent flow; token apps paste a secret.
  const connect = i => {
    if (i.auth === "oauth") {
      window.open(_api__WEBPACK_IMPORTED_MODULE_4__.eventsConnectUrl(i.name), "_blank", "noreferrer");
    } else {
      const token = window.prompt(`Paste your ${i.label} token:`);
      if (token) {
        _api__WEBPACK_IMPORTED_MODULE_4__.postEventsConnectToken(i.name, token).then(r => r.json()).then(res => {
          alert(res.ok ? `${i.label} connected.` : `Failed: ${res.error || "error"}`);
        });
      }
    }
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-grid"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(Loader, {
    loading: loading,
    error: error
  }), data?.map(i => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tile, {
    key: i.name,
    className: "studio-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-card-head"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "studio-card-title"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Application, {
    size: 18
  }), " ", i.label), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(StatusTag, {
    status: i.status
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted"
  }, i.note), trigsFor(i.name).length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("details", {
    style: {
      marginBottom: 8
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("summary", {
    className: "studio-muted",
    style: {
      cursor: "pointer",
      fontSize: 12
    }
  }, trigsFor(i.name).length, " trigger", trigsFor(i.name).length > 1 ? "s" : ""), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      flexWrap: "wrap",
      gap: 4,
      marginTop: 6
    }
  }, trigsFor(i.name).map(t => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    key: t.event,
    type: t.backend === "direct" ? "cyan" : "gray",
    size: "sm",
    title: t.title
  }, t.event, t.default ? " ★" : "")))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-card-foot"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: i.backend === "direct" ? "teal" : "blue",
    size: "sm"
  }, i.backend === "direct" ? "direct backend" : "via Activepieces"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: "outline",
    size: "sm"
  }, i.auth === "none" ? "no auth" : i.backend === "direct" ? "token" : i.auth === "oauth" ? "OAuth" : "token"), i.needs_connection === false || i.auth === "none" ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: "green",
    size: "sm"
  }, "ready \xB7 no connection needed") : i.status !== "ap_not_configured" ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    kind: i.status === "connected" ? "ghost" : "tertiary",
    size: "sm",
    renderIcon: i.auth === "oauth" ? _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Launch : _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Plug,
    onClick: () => connect(i)
  }, i.status === "connected" ? "Reconnect" : "Connect") : null))));
}

// The connect SETUP GUIDE — per connector: required creds (+ present?), ownership (per-user vs
// tenant), and the concrete steps. Dumb: it renders the server's guides + drives the connect action.
function SetupTab({
  refresh
}) {
  const {
    data,
    loading,
    error
  } = useEndpoint(_api__WEBPACK_IMPORTED_MODULE_4__.getEventsSetupGuides, d => d.guides ?? [], refresh);
  const [own, setOwn] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({});
  const connect = g => {
    const ownership = own[g.app] || g.ownership_default || (g.ownership || [])[0] || "per_user";
    if (g.connect === "oauth") {
      window.open(_api__WEBPACK_IMPORTED_MODULE_4__.eventsConnectUrl(g.app, ownership), "_blank", "noreferrer");
    } else if (g.connect === "token") {
      const token = window.prompt(`Paste your ${g.label} token/secret:`);
      if (token) _api__WEBPACK_IMPORTED_MODULE_4__.postEventsConnectToken(g.app, token, ownership).then(r => r.json()).then(res => alert(res.ok ? `${g.label} connected (${ownership}).` : `Failed: ${res.error || "error"}`));
    }
  };
  // TENANT-level OAuth app registration (client id/secret) — the org-wide credential that must exist
  // before per-user OAuth connect works. Consolidated here (was a separate Admin panel) so all
  // credential setup lives in ONE place.
  const setOAuthApp = g => {
    const cid = window.prompt(`${g.label} — OAuth app Client ID:`);
    if (!cid) return;
    const sec = window.prompt(`${g.label} — OAuth app Client Secret:`);
    if (!sec) return;
    _api__WEBPACK_IMPORTED_MODULE_4__.postEventsAdminOAuthApp({
      app: g.app,
      client_id: cid,
      client_secret: sec
    }).then(r => r.json()).then(res => alert(res.ok ? `${g.label} OAuth app saved (tenant).` : `Failed: ${res.error || "error"}`));
  };
  // Edit ANY connector credential (its .env variable) in place — persists to .env + applies live where
  // the value is read at use-time (Slack/Box/OAuth); flags a reload for the boot-time ones (Discord).
  const setCred = c => {
    const v = window.prompt(`Set ${c.key} — ${c.label}\n(value is written to .env)`, "");
    if (v == null || v === "") return;
    _api__WEBPACK_IMPORTED_MODULE_4__.postEventsSetCredential(c.key, v).then(r => r.json()).then(res => alert(res.ok ? `${c.key} saved.\n${res.note}` : `Failed: ${res.error || "error"}`));
  };
  const pill = (label, on, click) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    onClick: click,
    style: {
      cursor: click ? "pointer" : "default",
      fontSize: 12,
      fontWeight: 600,
      padding: "2px 10px",
      borderRadius: 20,
      marginRight: 6,
      background: on ? "#0f62fe" : "#e0e0e0",
      color: on ? "#fff" : "#525252"
    }
  }, label);
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(Loader, {
    loading: loading,
    error: error
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      marginBottom: 12
    }
  }, "How to connect each channel & integration \u2014 required credentials, where to store them (", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, "per-user"), " vs ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, "per-tenant"), "), and the steps."), data?.map(g => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tile, {
    key: g.app,
    className: "studio-card",
    style: {
      marginBottom: 12
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-card-head"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "studio-card-title"
  }, g.label), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: g.kind === "channel" ? "blue" : "teal",
    size: "sm"
  }, g.kind), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: "outline",
    size: "sm"
  }, g.wiring))), g.needs_connection && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8,
      margin: "6px 0 10px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: g.connected ? "green" : "red",
    size: "md"
  }, g.connected ? "● Connected" : "○ Not connected"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: g.connection_scope === "tenant" ? "blue" : "purple",
    size: "sm"
  }, g.connection_scope === "tenant" ? "TENANT (one per org)" : "USER (per-person login)"), (g.connect === "oauth" || g.connect === "token") && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    kind: g.connected ? "ghost" : "tertiary",
    size: "sm",
    renderIcon: g.connect === "oauth" ? _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Launch : _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Plug,
    onClick: () => connect(g)
  }, g.connected ? "Reconnect" : "Connect", " ", g.label), g.connect === "oauth" && g.connection_scope === "user" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    kind: "ghost",
    size: "sm",
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Settings,
    onClick: () => setOAuthApp(g)
  }, "OAuth app creds (tenant)")), (g.creds || []).length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      fontSize: 13
    }
  }, "No credentials needed.") : (g.creds || []).map(c => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: c.key,
    style: {
      fontSize: 13,
      margin: "3px 0",
      display: "flex",
      alignItems: "center",
      gap: 4,
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: c.present ? "green" : c.required ? "red" : "gray",
    size: "sm"
  }, c.present ? "configured ✓" : c.required ? "set up →" : "optional"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: c.scope === "tenant" ? "blue" : "purple",
    size: "sm"
  }, c.scope === "tenant" ? "TENANT" : "USER"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, c.key), " \u2014 ", c.label, " ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "studio-muted"
  }, "(", c.where, ")"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    kind: "ghost",
    size: "sm",
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Edit,
    onClick: () => setCred(c)
  }, c.present ? "Edit" : "Set"))), (g.ownership || []).length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      fontSize: 13,
      margin: "8px 0 4px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, "Store credential:"), " ", (g.ownership || []).map(o => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), {
    key: o
  }, pill(o === "per_user" ? "per-user" : "per-tenant", (own[g.app] || g.ownership_default) === o, (g.ownership || []).length > 1 ? () => setOwn({
    ...own,
    [g.app]: o
  }) : undefined)))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("ol", {
    style: {
      fontSize: 13,
      margin: "6px 0",
      paddingLeft: 18
    }
  }, (g.steps || []).map((s, i) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("li", {
    key: i,
    style: {
      margin: "3px 0"
    }
  }, s))), g.note && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      fontSize: 12.5
    }
  }, "\u26A0 ", g.note))));
}

// Walk the AP flow's trigger→nextAction chain into a flat step list, then show the CUGA
// Source→Agent→Sink model + those AP steps. This is the "see it like AP" view, in-Studio.
function FlowDetail({
  detail
}) {
  const s = detail?.subscription || {};
  const ap = detail?.ap_flow || null;
  const steps = [];
  let node = ap?.version?.trigger;
  while (node) {
    const set = node.settings || {};
    steps.push({
      name: node.displayName || node.name,
      piece: set.pieceName,
      action: set.actionName || set.triggerName
    });
    node = node.nextAction;
  }
  // The post-agent ACTION. For a DIRECT trigger (slack/discord/telegram) it lives in
  // config.action_plan (the executor, Option A) — there's no AP flow to walk, so this is the only
  // place it shows. For an AP-push flow the action also appears as an AP step below.
  const plan = s.config?.action_plan;
  const actionLabel = plan?.steps?.length ? plan.steps.map(x => `${x.app}/${x.ap_action} (executor)`).join(" + ") : plan?.branches?.length ? "branched: " + plan.branches.map(b => b.tag || `${b.step?.app}/${b.step?.ap_action}`).join(" / ") : "";
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      marginBottom: 10
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", null, "Source"), " ", s.source_connector || s.source_type, " \u2192 ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", null, "Agent"), " ", s.target_agent, actionLabel && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, " \u2192 ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", null, "Action"), " ", actionLabel), " ", "\u2192 ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", null, "Sink"), " ", (s.deliver_to || []).join(", ") || "web"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    style: {
      margin: "0 0 8px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: MODE_TAG[s.mode] ?? "gray",
    size: "sm"
  }, s.mode), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: s.status === "paused" ? "gray" : "green",
    size: "sm"
  }, s.status), s.flow_name && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: "outline",
    size: "sm"
  }, s.flow_name)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      fontSize: 13
    }
  }, s.prompt), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h5", {
    style: {
      margin: "16px 0 6px"
    }
  }, "Activepieces flow steps"), ap ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("ol", {
    style: {
      paddingLeft: 18,
      margin: 0
    }
  }, steps.map((st, i) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("li", {
    key: i,
    style: {
      fontSize: 13,
      marginBottom: 4
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("strong", null, st.name), " ", st.piece && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, String(st.piece).replace("@activepieces/piece-", "")), st.action && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "studio-muted"
  }, " \xB7 ", st.action)))) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted"
  }, actionLabel ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, "Direct trigger \u2014 no AP watcher flow; the action runs via an executor flow: ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, actionLabel)) : "No live AP flow (a direct/no-AP flow, or AP unreachable)."));
}
function FlowsTab({
  refresh
}) {
  const [tick, setTick] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(0);
  const {
    data,
    loading,
    error
  } = useEndpoint(_api__WEBPACK_IMPORTED_MODULE_4__.getEventsSubscriptions, d => d.subscriptions ?? [], refresh + tick);
  const [busy, setBusy] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [actionError, setActionError] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [detail, setDetail] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [detailLoading, setDetailLoading] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const act = async (id, fn) => {
    setBusy(id);
    setActionError(null);
    try {
      const res = await fn(id);
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.error || res.statusText);
      setTick(t => t + 1);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "action failed");
    } finally {
      setBusy(null);
    }
  };
  const del = async (id, name) => {
    if (!window.confirm(`Delete flow "${name}"? This removes it from Activepieces too.`)) return;
    await act(id, _api__WEBPACK_IMPORTED_MODULE_4__.deleteFlow);
  };
  const view = async id => {
    setDetailLoading(true);
    setDetail({
      id
    });
    setActionError(null);
    try {
      const res = await _api__WEBPACK_IMPORTED_MODULE_4__.getEventsFlowDetail(id);
      const d = await res.json();
      if (!res.ok) throw new Error(d?.error || res.statusText);
      setDetail(d);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "could not load flow");
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(Loader, {
    loading: loading,
    error: error
  }), actionError && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.InlineNotification, {
    kind: "error",
    lowContrast: true,
    title: "Error",
    subtitle: actionError,
    onCloseButtonClick: () => setActionError(null)
  }), !loading && !error && (data?.length ?? 0) === 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.InlineNotification, {
    kind: "info",
    lowContrast: true,
    hideCloseButton: true,
    title: "No armed flows yet",
    subtitle: "Ask the concierge to watch or schedule something \u2014 or type /automate <what>. It appears here."
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-grid"
  }, data?.map(s => {
    const paused = s.status === "paused";
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tile, {
      key: s.id,
      className: "studio-card"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "studio-card-head"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      className: "studio-card-title"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Flow, {
      size: 18
    }), " ", s.target_agent), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
      type: MODE_TAG[s.mode] ?? "gray",
      size: "sm"
    }, s.mode)), s.flow_name && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
      className: "studio-muted",
      style: {
        fontSize: 12,
        margin: "0 0 4px"
      }
    }, "flow ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, s.flow_name)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
      className: "studio-muted"
    }, s.prompt || `${s.source_type}/${s.source_connector}`), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
      className: "studio-muted",
      style: {
        fontSize: 11,
        margin: "4px 0 0"
      }
    }, paused ? "paused" : "enabled", " \xB7 armed ", fmtEpoch(s.created_at)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "studio-card-foot"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
      type: "outline",
      size: "sm"
    }, s.backend), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
      type: paused ? "gray" : "green",
      size: "sm"
    }, s.status), s.deliver_to?.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
      type: "blue",
      size: "sm"
    }, "\u2192 ", s.deliver_to.join(", "))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      style: {
        display: "flex",
        gap: 4,
        marginTop: 12,
        flexWrap: "wrap"
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
      size: "sm",
      kind: "ghost",
      renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.View,
      onClick: () => view(s.id),
      disabled: busy === s.id
    }, "View"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
      size: "sm",
      kind: "ghost",
      renderIcon: paused ? _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Play : _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Pause,
      onClick: () => act(s.id, paused ? _api__WEBPACK_IMPORTED_MODULE_4__.resumeFlow : _api__WEBPACK_IMPORTED_MODULE_4__.pauseFlow),
      disabled: busy === s.id
    }, paused ? "Resume" : "Pause"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
      size: "sm",
      kind: "danger--ghost",
      renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.TrashCan,
      onClick: () => del(s.id, s.flow_name || s.id),
      disabled: busy === s.id
    }, "Delete")));
  })), detail && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Modal, {
    open: true,
    passiveModal: true,
    modalHeading: `Flow — ${detail?.subscription?.flow_name || detail?.id || ""}`,
    onRequestClose: () => setDetail(null)
  }, detailLoading ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.InlineLoading, {
    description: "Loading flow\u2026"
  }) : detail?.subscription ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(FlowDetail, {
    detail: detail
  }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted"
  }, "No detail.")));
}

// The execution log — every standing-flow run (cron/poll/push), filterable by agent / integration /
// channel / trigger / status, with the agent's output on demand.
function RunDetail({
  detail
}) {
  if (detail?.error) {
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.InlineNotification, {
      kind: "error",
      lowContrast: true,
      hideCloseButton: true,
      title: "Error",
      subtitle: String(detail.error)
    });
  }
  const run = detail?.run || {};
  const trig = detail?.trigger_payload;
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    style: {
      margin: "0 0 6px",
      display: "flex",
      gap: 8,
      alignItems: "center"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: RUN_STATUS_TAG[run.status] ?? "gray",
    size: "sm"
  }, run.status), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "studio-muted",
    style: {
      fontSize: 12
    }
  }, fmtTime(run.started_at))), detail?.utterance && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      fontSize: 12,
      margin: "0 0 8px"
    }
  }, "Flow: ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("i", null, detail.utterance)), detail?.error_msg && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.InlineNotification, {
    kind: "error",
    lowContrast: true,
    hideCloseButton: true,
    title: "Flow error",
    subtitle: String(detail.error_msg)
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h5", {
    style: {
      margin: "8px 0 4px"
    }
  }, "Agent output"), detail?.answer ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("pre", {
    className: "studio-msg-text",
    style: {
      whiteSpace: "pre-wrap",
      fontSize: 13
    }
  }, detail.answer) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted"
  }, "No answer captured (a failed run, or the flow doesn't return one)."), trig != null && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("details", {
    style: {
      marginTop: 10
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("summary", {
    className: "studio-muted",
    style: {
      cursor: "pointer",
      fontSize: 12
    }
  }, "Trigger payload"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("pre", {
    className: "studio-msg-text",
    style: {
      whiteSpace: "pre-wrap",
      fontSize: 12,
      maxHeight: 220,
      overflow: "auto"
    }
  }, JSON.stringify(trig, null, 2).slice(0, 4000))));
}

// The Dashboard — the events control plane at a glance: summary tiles, every watcher (with
// pause/resume/delete), and recent runs (agent · type · tools · answer). Carbon-styled to match the
// rest of the Studio; reads the same /api/events/* endpoints as the standalone dashboard page.
const MODE_TAG_D = {
  CRON: "green",
  POLL: "teal",
  PUSH: "magenta",
  NOW: "gray"
};
function DashboardTab({
  refresh
}) {
  const [tick, setTick] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(0);
  const subsE = useEndpoint(_api__WEBPACK_IMPORTED_MODULE_4__.getEventsSubscriptions, d => d, refresh + tick);
  const runsE = useEndpoint(_api__WEBPACK_IMPORTED_MODULE_4__.getEventsRuns, d => d.runs ?? [], refresh + tick);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    const id = setInterval(() => setTick(t => t + 1), 10000);
    return () => clearInterval(id);
  }, []);
  const data = subsE.data || {};
  const summary = data.summary || {
    by_mode: {}
  };
  const watchers = data.subscriptions || [];
  const runs = runsE.data || [];
  const bkTag = b => b === "native" ? "green" : b === "ap" ? "purple" : "cool-gray";
  const act = async (id, what) => {
    if (what === "delete" && !window.confirm("Delete watcher " + id + "?")) return;
    if (what === "pause") await _api__WEBPACK_IMPORTED_MODULE_4__.pauseFlow(id);else if (what === "resume") await _api__WEBPACK_IMPORTED_MODULE_4__.resumeFlow(id);else await _api__WEBPACK_IMPORTED_MODULE_4__.deleteFlow(id);
    setTick(t => t + 1);
  };
  const cadence = s => s.interval_seconds ? s.interval_seconds % 60 === 0 ? `${s.interval_seconds / 60} min` : `${s.interval_seconds}s` : s.cron_expr ? `cron ${s.cron_expr}` : s.mode === "PUSH" ? "on event" : "—";
  const watches = s => s.mode === "PUSH" ? `${s.source_connector || ""} · ${s.event || ""}` : ["cron", "interval"].includes(s.source_connector) ? "the clock" : s.source_connector || "—";
  // the actual task/utterance, with the scheduler framing stripped so you see WHAT it watches for
  const taskOf = s => {
    let p = s.prompt || "";
    const i = p.indexOf("report:\n");
    if (i >= 0) p = p.slice(i + 8);
    return p.split("\nThis is a POLL:")[0].replace(/^["“]|["”]$/g, "").trim() || watches(s);
  };
  const tiles = [["watchers", summary.total], ["crons", summary.by_mode?.CRON], ["polls", summary.by_mode?.POLL], ["pushes", summary.by_mode?.PUSH], ["native · no AP", summary.native_no_ap], ["AP flows", summary.ap_flows], ["paused", summary.paused]];
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(Loader, {
    loading: subsE.loading,
    error: subsE.error
  }), !subsE.loading && !subsE.error && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      margin: "0 0 12px",
      fontSize: 13
    }
  }, "The events control plane \u2014 watchers, runs & channels at a glance. Auto-refreshes.", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    kind: "ghost",
    size: "sm",
    onClick: () => setTick(t => t + 1)
  }, "Refresh")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      gap: 12,
      flexWrap: "wrap",
      marginBottom: 18
    }
  }, tiles.map(([l, v]) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tile, {
    key: l,
    className: "studio-card",
    style: {
      minWidth: 108,
      padding: "12px 16px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      fontSize: "1.7rem",
      fontWeight: 600,
      lineHeight: 1.1
    }
  }, v ?? 0), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-muted",
    style: {
      fontSize: ".78rem",
      textTransform: "uppercase",
      letterSpacing: ".04em"
    }
  }, l)))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h4", {
    style: {
      margin: "0 0 .5rem"
    }
  }, "Watchers"), watchers.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.InlineNotification, {
    kind: "info",
    lowContrast: true,
    hideCloseButton: true,
    title: "No watchers armed",
    subtitle: "Arm one from the Concierge tab \u2014 e.g. \u201Cevery 5 minutes give me a tip\u201D or \u201Cwatch bitcoin every 2 min\u201D."
  }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Table, {
    size: "sm"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHead, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableRow, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Watching for"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Agent"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Source"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Type"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Cadence"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Next fire"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Status"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "\xA0"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableBody, null, watchers.map(s => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableRow, {
    key: s.id
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, {
    title: s.prompt || "",
    style: {
      maxWidth: 300
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, taskOf(s).slice(0, 90)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      fontFamily: "monospace",
      fontSize: ".7rem",
      opacity: .5
    }
  }, s.id)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, s.target_agent)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, watches(s)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: MODE_TAG_D[s.mode] ?? "gray",
    size: "sm"
  }, s.mode), " ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: bkTag(s.backend),
    size: "sm"
  }, s.backend === "native" ? "native" : "AP")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, cadence(s), s.fire_count ? ` · ×${s.fire_count}` : ""), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, {
    style: {
      whiteSpace: "nowrap"
    }
  }, s.next_fire ? new Date(s.next_fire * 1000).toLocaleString() : "—"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: s.status === "paused" ? "warm-gray" : "green",
    size: "sm"
  }, s.status)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      gap: 2
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    kind: "ghost",
    size: "sm",
    hasIconOnly: true,
    renderIcon: s.status === "paused" ? _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Play : _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Pause,
    iconDescription: s.status === "paused" ? "resume" : "pause",
    tooltipPosition: "left",
    onClick: () => act(s.id, s.status === "paused" ? "resume" : "pause")
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    kind: "danger--ghost",
    size: "sm",
    hasIconOnly: true,
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.TrashCan,
    iconDescription: "delete",
    tooltipPosition: "left",
    onClick: () => act(s.id, "delete")
  }))))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h4", {
    style: {
      margin: "1.6rem 0 .5rem"
    }
  }, "Recent runs"), runs.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.InlineNotification, {
    kind: "info",
    lowContrast: true,
    hideCloseButton: true,
    title: "No runs yet",
    subtitle: "Every agent run lands here \u2014 a direct chat message as well as each fire of an armed flow. The Source column tells them apart."
  }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Table, {
    size: "sm"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHead, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableRow, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "When"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Source"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Agent"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Type"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Tools"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Answer"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Status"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableBody, null, runs.slice(0, 30).map(r => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableRow, {
    key: r.id
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, {
    style: {
      whiteSpace: "nowrap"
    }
  }, fmtTime(r.started_at)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, {
    style: {
      whiteSpace: "nowrap",
      fontFamily: "monospace",
      fontSize: ".72rem"
    }
  }, r.subscription_id || r.flow_id ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    title: r.flow_name || undefined
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: "blue",
    size: "sm"
  }, "flow"), " ", r.subscription_id || r.flow_id) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    title: `direct ${r.channel || "web"} message — no flow involved`,
    style: {
      opacity: .6
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: "gray",
    size: "sm"
  }, "chat"), " ", r.channel || "web")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, r.agent)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: MODE_TAG_D[r.mode] ?? "gray",
    size: "sm"
  }, r.mode), " ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: bkTag(r.backend),
    size: "sm"
  }, r.backend)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, {
    style: {
      fontFamily: "monospace",
      fontSize: ".76rem"
    }
  }, (r.tools || []).join(", ") || "—"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, {
    title: r.answer || r.utterance || "",
    style: {
      maxWidth: 320
    }
  }, (r.answer || r.utterance || "").slice(0, 130)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: r.status === "FAILED" ? "red" : "green",
    size: "sm"
  }, r.status))))))));
}
function RunsTab({
  refresh
}) {
  const [tick, setTick] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(0);
  const {
    data,
    loading,
    error
  } = useEndpoint(_api__WEBPACK_IMPORTED_MODULE_4__.getEventsRuns, d => d.runs ?? [], refresh + tick);
  const [f, setF] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({
    agent: "all",
    backend: "all",
    integration: "all",
    channel: "all",
    mode: "all",
    status: "all"
  });
  const [sort, setSort] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({
    col: "started_at",
    dir: -1
  });
  const [detail, setDetail] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [detailLoading, setDetailLoading] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const runs = data ?? [];
  const uniq = k => Array.from(new Set(runs.map(r => r[k]).filter(Boolean))).sort();
  const filtered = runs.filter(r => ["agent", "backend", "integration", "channel", "mode", "status"].every(k => f[k] === "all" || String(r[k]) === f[k]));
  const sorted = [...filtered].sort((a, b) => {
    const av = a[sort.col] ?? "",
      bv = b[sort.col] ?? "";
    return (av < bv ? -1 : av > bv ? 1 : 0) * sort.dir;
  });
  const view = async row => {
    setDetailLoading(true);
    setDetail({
      id: row.id,
      utterance: row.utterance
    });
    try {
      const res = await _api__WEBPACK_IMPORTED_MODULE_4__.getEventsRunDetail(row.id);
      const d = await res.json();
      setDetail(res.ok ? {
        ...d,
        utterance: row.utterance,
        error_msg: d.error
      } : {
        error: d?.error || res.statusText
      });
    } catch (e) {
      setDetail({
        error: e instanceof Error ? e.message : "failed"
      });
    } finally {
      setDetailLoading(false);
    }
  };
  const th = (col, label) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, {
    onClick: () => setSort(s => ({
      col,
      dir: s.col === col ? s.dir * -1 : 1
    })),
    style: {
      cursor: "pointer"
    }
  }, label, sort.col === col ? sort.dir === 1 ? " ▲" : " ▼" : "");
  const filterSel = (key, label) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Select, {
    id: `run-f-${key}`,
    labelText: label,
    size: "sm",
    value: f[key],
    onChange: e => setF(p => ({
      ...p,
      [key]: e.target.value
    }))
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.SelectItem, {
    value: "all",
    text: `All`
  }), uniq(key).map(v => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.SelectItem, {
    key: String(v),
    value: String(v),
    text: String(v)
  })));
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(Loader, {
    loading: loading,
    error: error
  }), !loading && !error && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      margin: "0 0 10px",
      fontSize: 13
    }
  }, "Every execution, newest first \u2014 ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, "NOW"), " answers (Studio chat + channel DMs) and standing flows (cron / poll / push). Click a column to sort; filter with the dropdowns.", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    kind: "ghost",
    size: "sm",
    onClick: () => setTick(t => t + 1)
  }, "Refresh")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      gap: 12,
      flexWrap: "wrap",
      marginBottom: 12,
      alignItems: "flex-end"
    }
  }, filterSel("agent", "Agent"), filterSel("integration", "Integration"), filterSel("channel", "Channel"), filterSel("mode", "Trigger"), filterSel("backend", "Backend"), filterSel("status", "Status")), runs.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.InlineNotification, {
    kind: "info",
    lowContrast: true,
    hideCloseButton: true,
    title: "No runs yet",
    subtitle: "Chat with the Concierge or arm a flow \u2014 every answer and flow-fire shows up here with status and output."
  }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Table, {
    size: "sm"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHead, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableRow, null, th("started_at", "Time"), th("agent", "Agent"), th("utterance", "Flow (utterance)"), th("mode", "Trigger"), th("backend", "Backend"), th("integration", "Integration"), th("channel", "Channel"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Tools"), th("flow_id", "Flow ID"), th("status", "Status"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableHeader, null, "Output"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableBody, null, sorted.map(r => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableRow, {
    key: r.id
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, fmtTime(r.started_at)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, r.agent), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, {
    title: r.utterance || "",
    style: {
      maxWidth: 260
    }
  }, r.utterance ? r.utterance.length > 52 ? r.utterance.slice(0, 52) + "…" : r.utterance : "—"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: MODE_TAG[r.mode] ?? "gray",
    size: "sm"
  }, r.mode)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: r.backend === "native" ? "green" : r.backend === "ap" ? "purple" : "cool-gray",
    size: "sm"
  }, r.backend || "—")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, r.integration), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, r.channel), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, {
    title: (r.tools || []).join(", "),
    style: {
      fontFamily: "monospace",
      fontSize: 12,
      maxWidth: 150
    }
  }, r.tools && r.tools.length ? r.tools.join(", ") : "—"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, {
    title: r.flow_id ? `AP flow ${r.flow_id}${r.flow_name ? ` · ${r.flow_name}` : ""} — click to copy` : "",
    style: {
      fontFamily: "monospace",
      fontSize: 12,
      cursor: r.flow_id ? "copy" : "default",
      whiteSpace: "nowrap"
    },
    onClick: () => r.flow_id && navigator.clipboard?.writeText(r.flow_id)
  }, r.flow_id ? String(r.flow_id).length > 12 ? String(r.flow_id).slice(0, 10) + "…" : r.flow_id : "—"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: RUN_STATUS_TAG[r.status] ?? "gray",
    size: "sm"
  }, r.status)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TableCell, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    kind: "ghost",
    size: "sm",
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.View,
    onClick: () => view(r)
  }, "View")))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      fontSize: 12,
      marginTop: 8
    }
  }, "Showing ", sorted.length, " of ", runs.length, ". NOW = an immediate chat/DM answer; CRON/POLL/PUSH = a standing flow firing.")), detail && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Modal, {
    open: true,
    passiveModal: true,
    modalHeading: "Run output",
    onRequestClose: () => setDetail(null)
  }, detailLoading ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.InlineLoading, {
    description: "Loading\u2026"
  }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(RunDetail, {
    detail: detail
  })));
}
function ExamplesTab({
  refresh,
  onTry
}) {
  const {
    data,
    loading,
    error
  } = useEndpoint(_api__WEBPACK_IMPORTED_MODULE_4__.getEventsExamples, d => d.examples ?? [], refresh);
  const [starOnly, setStarOnly] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [q, setQ] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("");
  const query = q.trim().toLowerCase();
  const items = (data ?? []).filter(e => (!starOnly || e.star) && (!query || `${e.title} ${e.utterance} ${e.agent} ${e.note} ${e.action || ""}`.toLowerCase().includes(query)));

  // group by agent → a long, copy-pasteable list per agent
  const groups = new Map();
  for (const e of items) {
    const k = e.agent || "—";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(e);
  }
  // agents with a ⭐ example first, then alphabetical
  const agents = [...groups.keys()].sort((a, b) => {
    const sa = groups.get(a).some(e => e.star) ? 0 : 1;
    const sb = groups.get(b).some(e => e.star) ? 0 : 1;
    return sa - sb || a.localeCompare(b);
  });
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 16,
      margin: "0 0 14px",
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      margin: 0,
      fontSize: 13,
      flex: "1 1 320px"
    }
  }, "Prompts grouped by ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, "agent"), ". ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, "Copy"), " one to paste anywhere, or ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, "Try it"), " to load it into the Concierge. ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, "\u2B50 = recommended starter"), "."), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TextInput, {
    id: "ex-search",
    labelText: "",
    size: "sm",
    placeholder: "Filter examples\u2026",
    value: q,
    onChange: e => setQ(e.target.value),
    style: {
      maxWidth: 220
    }
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Checkbox, {
    id: "ex-star-only",
    labelText: "\u2B50 Recommended only",
    checked: starOnly,
    onChange: (_e, d) => setStarOnly(!!d?.checked)
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(Loader, {
    loading: loading,
    error: error
  }), !loading && !error && agents.length === 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted"
  }, "No examples match."), agents.map(agent => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: agent,
    style: {
      marginBottom: 22
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h4", {
    style: {
      margin: "0 0 6px",
      display: "flex",
      alignItems: "center",
      gap: 8
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Bot, {
    size: 18
  }), " ", agent, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: "cool-gray",
    size: "sm"
  }, groups.get(agent).length)), groups.get(agent).sort((a, b) => (b.star ? 1 : 0) - (a.star ? 1 : 0)).map(e => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: e.id,
    style: {
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "7px 0",
      borderBottom: "1px solid var(--cds-border-subtle, #e0e0e0)"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "studio-example-utterance",
    style: {
      fontSize: 13
    }
  }, e.star && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    title: "recommended"
  }, "\u2B50 "), "\"", e.utterance, "\""), e.note && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      margin: "2px 0 0",
      fontSize: 11
    }
  }, e.note)), e.action && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: "purple",
    size: "sm",
    title: `post-agent action: ${e.action}`
  }, "ACTIONS"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    type: OUTCOME_TAG[e.outcome] ?? "gray",
    size: "sm"
  }, e.trigger || e.outcome), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.CopyButton, {
    iconDescription: "Copy prompt",
    feedback: "Copied!",
    onClick: () => {
      try {
        navigator.clipboard.writeText(e.utterance);
      } catch {/* noop */}
    }
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    kind: "tertiary",
    size: "sm",
    onClick: () => onTry(e.utterance)
  }, "Try it"))))));
}
function ProfileTab({
  refresh
}) {
  const {
    data,
    loading,
    error
  } = useEndpoint(_api__WEBPACK_IMPORTED_MODULE_4__.getEventsMe, d => d, refresh);
  const link = channel => {
    _api__WEBPACK_IMPORTED_MODULE_4__.postEventsLinkChannel(channel).then(r => r.json()).then(res => {
      alert(res.ok ? `To link ${channel}: ${res.how}` : `Failed: ${res.error || "error"}`);
    });
  };
  if (loading || error) return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(Loader, {
    loading: loading,
    error: error
  });
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tile, {
    className: "studio-card",
    style: {
      marginBottom: "1rem"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-card-head"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "studio-card-title"
  }, data?.user_id), (data?.roles ?? []).map(r => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    key: r,
    type: "cyan",
    size: "sm"
  }, r))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted"
  }, data?.email, " \xB7 scope ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, data?.scope))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h4", {
    style: {
      margin: "1rem 0 0.5rem"
    }
  }, "My channels"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-grid"
  }, ["telegram", "discord", "slack"].map(ch => {
    const linked = (data?.linked_channels ?? []).some(l => l.channel === ch);
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tile, {
      key: ch,
      className: "studio-card"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "studio-card-head"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      className: "studio-card-title"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Chat, {
      size: 18
    }), " ", ch), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(StatusTag, {
      status: linked ? "connected" : "not_connected"
    })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "studio-card-foot"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
      kind: "tertiary",
      size: "sm",
      onClick: () => link(ch)
    }, linked ? "Re-link" : "Link my account")));
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h4", {
    style: {
      margin: "1.5rem 0 0.5rem"
    }
  }, "My connected integrations"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      fontSize: 13,
      marginTop: 0
    }
  }, "Read-only \u2014 this is who you are. To connect or reconnect an app, use the ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, "Setup"), " tab."), (data?.connections ?? []).length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted"
  }, "None yet.") : data.connections.map(c => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    key: c.externalId,
    type: "green",
    size: "sm"
  }, c.externalId)));
}
function AdminTab({
  refresh
}) {
  const {
    data,
    loading,
    error
  } = useEndpoint(_api__WEBPACK_IMPORTED_MODULE_4__.getEventsAdminUsers, d => d.users ?? [], refresh);
  const add = () => {
    const id = window.prompt("New user id (e.g. carol):");
    if (!id) return;
    const email = window.prompt("Email:") || "";
    _api__WEBPACK_IMPORTED_MODULE_4__.postEventsAdminUser({
      user_id: id,
      email,
      roles: ["user"]
    }).then(r => r.json()).then(res => alert(res.ok ? `Added ${id}` : `Failed: ${res.error}`));
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(Loader, {
    loading: loading,
    error: error
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      marginBottom: 12
    }
  }, "Users & roles for this tenant. ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, "Credentials & app connections"), " (OAuth app client id/secret, tokens, connect/reconnect) all live in the ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("b", null, "Setup"), " tab \u2014 one place."), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    kind: "tertiary",
    size: "sm",
    onClick: add,
    style: {
      marginBottom: "1rem"
    }
  }, "Add user"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-grid"
  }, data?.map(u => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tile, {
    key: u.user_id,
    className: "studio-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-card-head"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "studio-card-title"
  }, u.user_id), (u.roles ?? []).map(r => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tag, {
    key: r,
    type: "gray",
    size: "sm"
  }, r))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted"
  }, u.email)))));
}

// The API reference, embedded — served by the backend (/api/events/docs/{api,examples})
// so it renders inside the Studio.
function ApiTab() {
  // The pages are served by the EVENTS service, not by whoever served this SPA. `getApiBaseUrl()`
  // returns window.location.origin — CUGA's — so in a split deployment the iframe asked CUGA for
  // /api/events/docs/api, hit the SPA catch-all, and rendered a 490-byte copy of CUGA itself
  // inside the tab. Resolved asynchronously and held in state rather than read through the sync
  // helper, because on first mount that cache is cold and would fall back to the same wrong origin.
  const [base, setBase] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    let live = true;
    _api__WEBPACK_IMPORTED_MODULE_4__.getEventsBaseUrl().then(b => {
      if (live) setBase(b);
    });
    return () => {
      live = false;
    };
  }, []);
  const pages = [{
    key: "api",
    label: "API guide"
  },
  // No "OpenAPI spec" tab: it embedded events/docs/api/api_spec.html, 204 KB of generated markup
  // carried in git so a --check test could diff against it. Both the page and its generator are
  // gone; FastAPI publishes the real contract at /docs and /openapi.json.
  {
    key: "examples",
    label: "Examples board"
  }
  // No "NL→Flow" or "Slides" tabs: events/docs/slides.html exists on no branch, and the NL→Flow
  // page is events/docs/runbook/nl-to-flow.html — a different directory and spelling than the
  // route looked for, so both 404'd.
  ];
  const [page, setPage] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("api");
  const url = base ? `${base}/api/events/docs/${page}` : "";
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8,
      marginBottom: 12,
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      margin: 0,
      fontSize: 13,
      flex: "1 1 240px"
    }
  }, "The events API reference, embedded. The machine-readable contract is at /docs and /openapi.json on the events service."), pages.map(p => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    key: p.key,
    size: "sm",
    kind: page === p.key ? "primary" : "tertiary",
    onClick: () => setPage(p.key),
    disabled: !base
  }, p.label)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    size: "sm",
    kind: "ghost",
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Launch,
    href: url,
    target: "_blank",
    disabled: !base
  }, "Open full page")), base ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("iframe", {
    title: "API reference",
    src: url,
    style: {
      width: "100%",
      height: "72vh",
      border: "1px solid var(--cds-border-subtle, #e0e0e0)",
      borderRadius: 6,
      background: "#fff"
    }
  }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted",
    style: {
      fontSize: 13
    }
  }, "Loading the API reference\u2026"));
}

// ---- page --------------------------------------------------------------------
function StudioPage() {
  const navigate = (0,react_router_dom__WEBPACK_IMPORTED_MODULE_1__.useNavigate)();
  const [status, setStatus] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [checked, setChecked] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [selected, setSelected] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(0);
  const [draft, setDraft] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("");
  const [refresh, setRefresh] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(0);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    _api__WEBPACK_IMPORTED_MODULE_4__.getEventsStatus().then(s => {
      setStatus(s);
      setChecked(true);
    });
  }, []);

  // refresh Flows/Integrations after a concierge action or manual refresh
  const bump = () => setRefresh(n => n + 1);
  if (checked && !status) {
    // events layer not mounted → this route shouldn't be reachable; guide back.
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "studio-page"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_CugaHeader__WEBPACK_IMPORTED_MODULE_5__.CugaHeader, {
      title: "CUGA Agent",
      navItems: [{
        label: "Chat",
        href: "/chat"
      }]
    }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "studio-content"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.InlineNotification, {
      kind: "info",
      lowContrast: true,
      hideCloseButton: true,
      title: "Studio is off",
      subtitle: "The events service isn't reachable. Start it (python -m cuga.backend.events.service) and set EVENTS_API_URL."
    }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
      kind: "tertiary",
      onClick: () => navigate("/manage"),
      style: {
        marginTop: 16
      }
    }, "Back to dashboard")));
  }
  const onTry = utterance => {
    setDraft(utterance);
    setSelected(0);
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-page"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_CugaHeader__WEBPACK_IMPORTED_MODULE_5__.CugaHeader, {
    title: "CUGA Studio",
    prefix: "Events",
    navItems: [{
      label: "Chat",
      href: "/chat"
    }, {
      label: "Agents",
      href: "/manage"
    }]
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-content"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "studio-heading-row"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h2", {
    className: "studio-title"
  }, "Event Studio"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "studio-muted"
  }, "Turn natural language into worker agents + triggers. Configuration & visibility only \u2014 all decisions run server-side.", status && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "studio-scope"
  }, " \xB7 scope ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, status.scope), " · workers ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, status.worker_backend ?? "cuga"), " · concierge ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, status.concierge_backend ?? "react"), " · AP ", status.ap_configured ? "connected" : "off"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Button, {
    kind: "ghost",
    size: "sm",
    onClick: bump
  }, "Refresh")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tabs, {
    selectedIndex: selected,
    onChange: e => setSelected(e.selectedIndex)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TabList, {
    "aria-label": "Studio sections",
    contained: true
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tab, {
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Dashboard
  }, "Dashboard"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tab, {
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Chat
  }, "Concierge"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tab, {
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Bot
  }, "Agents"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tab, {
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Chat
  }, "Channels"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tab, {
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Plug
  }, "Integrations"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tab, {
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Settings
  }, "Setup"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tab, {
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Flow
  }, "Flows"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tab, {
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Activity
  }, "Runs"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tab, {
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Idea
  }, "Examples"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tab, {
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Launch
  }, "API"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tab, {
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.User
  }, "Profile"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.Tab, {
    renderIcon: _carbon_icons_react__WEBPACK_IMPORTED_MODULE_3__.Settings
  }, "Admin")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TabPanels, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TabPanel, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(DashboardTab, {
    refresh: refresh
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TabPanel, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_ConciergeChat__WEBPACK_IMPORTED_MODULE_6__.ConciergeChat, {
    draft: draft,
    setDraft: setDraft
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TabPanel, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(AgentsTab, {
    refresh: refresh,
    onTry: onTry
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TabPanel, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(ChannelsTab, {
    refresh: refresh
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TabPanel, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(IntegrationsTab, {
    refresh: refresh
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TabPanel, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(SetupTab, {
    refresh: refresh
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TabPanel, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(FlowsTab, {
    refresh: refresh
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TabPanel, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(RunsTab, {
    refresh: refresh
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TabPanel, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(ExamplesTab, {
    refresh: refresh,
    onTry: onTry
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TabPanel, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(ApiTab, null)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TabPanel, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(ProfileTab, {
    refresh: refresh
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_carbon_react__WEBPACK_IMPORTED_MODULE_2__.TabPanel, null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(AdminTab, {
    refresh: refresh
  }))))));
}

/***/ })

}]);
//# sourceMappingURL=src_events_StudioPage_tsx.ef1c79d4f2f6801ac131.bundle.js.map