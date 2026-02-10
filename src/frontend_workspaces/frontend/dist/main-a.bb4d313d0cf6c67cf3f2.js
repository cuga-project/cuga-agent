/******/ (function() { // webpackBootstrap
/******/ 	"use strict";
/******/ 	var __webpack_modules__ = ({

/***/ "../agentic_chat/src/mockApi.ts":
/*!**************************************!*\
  !*** ../agentic_chat/src/mockApi.ts ***!
  \**************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* unused harmony exports setupMockApi, USE_FAKE_STREAM */
const USE_FAKE_STREAM =  true ? !!false : 0;
const mockWorkspaceTree = {
  tree: [{
    name: "src",
    path: "/workspace/src",
    type: "directory",
    children: [{
      name: "main.py",
      path: "/workspace/src/main.py",
      type: "file"
    }, {
      name: "utils.py",
      path: "/workspace/src/utils.py",
      type: "file"
    }, {
      name: "config.json",
      path: "/workspace/src/config.json",
      type: "file"
    }]
  }, {
    name: "data",
    path: "/workspace/data",
    type: "directory",
    children: [{
      name: "accounts.csv",
      path: "/workspace/data/accounts.csv",
      type: "file"
    }, {
      name: "contacts.txt",
      path: "/workspace/data/contacts.txt",
      type: "file"
    }]
  }, {
    name: "README.md",
    path: "/workspace/README.md",
    type: "file"
  }, {
    name: "requirements.txt",
    path: "/workspace/requirements.txt",
    type: "file"
  }]
};
const mockFileContents = {
  "/workspace/src/main.py": `# Main Application Entry Point
import asyncio
from utils import get_accounts, process_data

async def main():
    """Main application function"""
    print("Starting application...")
    accounts = await get_accounts()
    results = process_data(accounts)
    print(f"Processed {len(results)} accounts")
    return results

if __name__ == "__main__":
    asyncio.run(main())
`,
  "/workspace/src/utils.py": `# Utility Functions
from typing import List, Dict, Any

async def get_accounts() -> List[Dict[str, Any]]:
    """Fetch accounts from the API"""
    # Mock implementation
    return [
        {"name": "Acme Corp", "revenue": 1000000},
        {"name": "Tech Innovations", "revenue": 750000},
        {"name": "Global Solutions", "revenue": 500000},
    ]

def process_data(accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Process account data"""
    return sorted(accounts, key=lambda x: x["revenue"], reverse=True)
`,
  "/workspace/src/config.json": `{
  "api": {
    "endpoint": "https://api.example.com",
    "timeout": 30,
    "retry_count": 3
  },
  "database": {
    "host": "localhost",
    "port": 5432,
    "name": "crm_db"
  },
  "features": {
    "enable_caching": true,
    "enable_analytics": true,
    "max_concurrent_requests": 10
  }
}`,
  "/workspace/data/accounts.csv": `name,state,revenue,industry
Acme Corporation,New York,1000000,Technology
Tech Innovations Ltd.,California,750000,Software
Global Solutions Inc.,Texas,500000,Consulting
Pioneer Investments,Massachusetts,450000,Finance
Sunrise Industries,Florida,300000,Manufacturing`,
  "/workspace/data/contacts.txt": `John Doe - john.doe@acme.com - CEO
Jane Smith - jane.smith@techinnovations.com - CTO
Bob Johnson - bob.johnson@globalsolutions.com - VP Sales
Alice Williams - alice.williams@pioneer.com - Director
Charlie Brown - charlie.brown@sunrise.com - Manager`,
  "/workspace/README.md": `# Workspace Project

This is a sample workspace containing various files created by the agent.

## Structure

- \`src/\` - Source code files
  - \`main.py\` - Main application entry point
  - \`utils.py\` - Utility functions
  - \`config.json\` - Configuration settings

- \`data/\` - Data files
  - \`accounts.csv\` - Account information
  - \`contacts.txt\` - Contact details

## Usage

Run the main application:

\`\`\`bash
python src/main.py
\`\`\`

## Requirements

See \`requirements.txt\` for Python dependencies.
`,
  "/workspace/requirements.txt": `aiohttp==3.9.1
requests==2.31.0
pandas==2.1.4
pydantic==2.5.3
python-dotenv==1.0.0
fastapi==0.109.0
uvicorn==0.27.0
`
};
const originalFetch = window.fetch;
function setupMockApi() {
  if (!USE_FAKE_STREAM) {
    return;
  }
  console.log("🎭 Mock API initialized - intercepting workspace API calls");
  window.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;

    // Intercept workspace tree API
    if (url.includes("/api/workspace/tree")) {
      //   console.log("🎭 Mock: Returning workspace tree");
      await delay(300);
      return new Response(JSON.stringify(mockWorkspaceTree), {
        status: 200,
        headers: {
          "Content-Type": "application/json"
        }
      });
    }

    // Intercept workspace file content API
    if (url.includes("/api/workspace/file")) {
      const urlObj = new URL(url, window.location.origin);
      const path = urlObj.searchParams.get("path");
      console.log("🎭 Mock: Returning file content for", path);
      await delay(200);
      if (path && mockFileContents[path]) {
        return new Response(JSON.stringify({
          content: mockFileContents[path]
        }), {
          status: 200,
          headers: {
            "Content-Type": "application/json"
          }
        });
      } else {
        return new Response(JSON.stringify({
          error: "File not found"
        }), {
          status: 404,
          headers: {
            "Content-Type": "application/json"
          }
        });
      }
    }

    // Intercept workspace file download API
    if (url.includes("/api/workspace/download")) {
      const urlObj = new URL(url, window.location.origin);
      const path = urlObj.searchParams.get("path");
      console.log("🎭 Mock: Returning file download for", path);
      await delay(200);
      if (path && mockFileContents[path]) {
        const blob = new Blob([mockFileContents[path]], {
          type: "text/plain"
        });
        return new Response(blob, {
          status: 200,
          headers: {
            "Content-Type": "application/octet-stream",
            "Content-Disposition": `attachment; filename="${path.split("/").pop()}"`
          }
        });
      } else {
        return new Response(JSON.stringify({
          error: "File not found"
        }), {
          status: 404,
          headers: {
            "Content-Type": "application/json"
          }
        });
      }
    }

    // Intercept config API endpoints
    if (url.includes("/api/config/")) {
      console.log("🎭 Mock: Returning config response for", url);
      await delay(200);

      // Return empty config for all config endpoints
      const configType = url.split("/api/config/")[1];
      const mockConfigs = {
        memory: {
          enableMemory: true,
          memoryType: "both",
          contextWindow: 4096,
          maxMemoryItems: 100,
          semanticSearch: true,
          autoSummarization: true
        },
        knowledge: {
          sources: [],
          embeddingModel: "text-embedding-3-small",
          chunkSize: 1000,
          chunkOverlap: 200
        },
        tools: {
          mcpServers: {},
          services: []
        },
        subagents: {
          mode: "supervisor",
          subAgents: [],
          supervisorStrategy: "adaptive",
          availableTools: []
        },
        model: {
          provider: "anthropic",
          model: "claude-3-5-sonnet-20241022",
          temperature: 0.7,
          maxTokens: 4096,
          topP: 1.0
        },
        policies: {
          enablePolicies: true,
          intentPolicies: [],
          sopPolicies: [],
          subAgentPolicies: [],
          appPolicies: [],
          strictMode: false,
          logViolations: true
        }
      };
      if (init?.method === "POST") {
        // Save config (just return success)
        return new Response(JSON.stringify({
          success: true
        }), {
          status: 200,
          headers: {
            "Content-Type": "application/json"
          }
        });
      } else {
        // Get config
        const config = mockConfigs[configType] || {};
        return new Response(JSON.stringify(config), {
          status: 200,
          headers: {
            "Content-Type": "application/json"
          }
        });
      }
    }

    // For all other requests, use the original fetch
    return originalFetch(input, init);
  };
}
function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// Initialize mock API if FAKE_STREAM is enabled
if (USE_FAKE_STREAM) {
  setupMockApi();
}


/***/ }),

/***/ "../agentic_chat/src/qa_agent.tsx":
/*!****************************************!*\
  !*** ../agentic_chat/src/qa_agent.tsx ***!
  \****************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ QaAgentComponent; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);

function QaAgentComponent({
  qaData
}) {
  const [showFullThoughts, setShowFullThoughts] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [showFullAnswer, setShowFullAnswer] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);

  // Sample data for demonstration

  // Use props if provided, otherwise use sample data
  const {
    thoughts,
    name,
    answer
  } = qaData;
  function truncateThoughts(thoughtsArray, maxLength = 120) {
    const firstThought = thoughtsArray[0] || "";
    if (firstThought.length <= maxLength) return firstThought;
    return firstThought.substring(0, maxLength) + "...";
  }
  function truncateAnswer(answer, maxLength = 500) {
    if (answer.length <= maxLength) return answer;
    return answer.substring(0, maxLength) + "...";
  }
  function getAnswerPreview(answer) {
    const truncated = truncateAnswer(answer, 500);
    return truncated;
  }
  function getAnswerIcon(answer) {
    if (answer.length < 50) return "💡";
    if (answer.length < 200) return "📝";
    return "📄";
  }
  function getAnswerColor(answer) {
    if (answer.length < 50) return "bg-green-100 text-green-800 border-green-300";
    if (answer.length < 200) return "bg-blue-100 text-blue-800 border-blue-300";
    return "bg-purple-100 text-purple-800 border-purple-300";
  }
  const isAnswerTruncated = answer.length > 500;
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "max-w-4xl mx-auto"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-white rounded-lg border border-gray-200 p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
    className: "text-sm font-medium text-gray-700 flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, "\uD83D\uDD0D"), "QA Agent Response"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "px-2 py-1 rounded text-xs bg-emerald-100 text-emerald-700"
  }, "Analysis Complete")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2 mb-1"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-500"
  }, "Question:")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h4", {
    className: "font-medium text-gray-800 text-xs bg-gray-50 rounded p-2 border"
  }, name)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-3 border rounded p-2 hover:shadow-sm transition-shadow"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-start justify-between mb-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, getAnswerIcon(answer)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs font-medium text-gray-700"
  }, "Answer"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2 mt-1"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: `px-1.5 py-0.5 rounded text-xs font-medium ${getAnswerColor(answer)}`
  }, answer.length, " chars"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-500"
  }, answer.split(" ").length, " words"))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "pl-5"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-blue-50 border border-blue-200 rounded p-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-700 leading-relaxed font-mono whitespace-pre-wrap"
  }, showFullAnswer ? answer : getAnswerPreview(answer)), isAnswerTruncated && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mt-2 text-center"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: () => setShowFullAnswer(!showFullAnswer),
    className: "px-2 py-1 bg-blue-100 hover:bg-blue-200 text-blue-700 rounded text-xs font-medium transition-colors flex items-center gap-1 mx-auto"
  }, showFullAnswer ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Show less"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs"
  }, "\u25B2")) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Show full answer"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs"
  }, "\u25BC"))))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "grid grid-cols-3 gap-2 mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-center p-2 bg-blue-50 rounded"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-sm font-bold text-blue-700"
  }, thoughts.length), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-xs text-blue-600"
  }, "Analysis Steps")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-center p-2 bg-green-50 rounded"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-sm font-bold text-green-700"
  }, answer.length), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-xs text-green-600"
  }, "Answer Length")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-center p-2 bg-purple-50 rounded"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-sm font-bold text-purple-700"
  }, answer.split(" ").length), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-xs text-purple-600"
  }, "Words"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "border-t border-gray-100 pt-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-400"
  }, "\uD83D\uDCAD"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-500"
  }, "QA Analysis (", thoughts.length, ")"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: () => setShowFullThoughts(!showFullThoughts),
    className: "text-xs text-gray-400 hover:text-gray-600"
  }, showFullThoughts ? "▲" : "▼"))), !showFullThoughts && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-400 italic mt-1"
  }, truncateThoughts(thoughts, 80)), showFullThoughts && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mt-2 space-y-1"
  }, thoughts.map((thought, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    className: "flex items-start gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-300 mt-0.5 font-mono"
  }, index + 1, "."), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-500 leading-relaxed"
  }, thought))))))));
}

/***/ }),

/***/ "../agentic_chat/src/shortlister.tsx":
/*!*******************************************!*\
  !*** ../agentic_chat/src/shortlister.tsx ***!
  \*******************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ ShortlisterComponent; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);

function ShortlisterComponent({
  shortlisterData
}) {
  const [showFullThoughts, setShowFullThoughts] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [showAllApis, setShowAllApis] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);

  // Sample data for demonstration

  // Use props if provided, otherwise use sample data
  const {
    thoughts,
    result
  } = shortlisterData;
  const displayedApis = showAllApis ? result : result.slice(0, 2);
  const remainingCount = result.length - 2;
  function getScoreColor(score) {
    if (score >= 0.95) return "bg-green-100 text-green-800 border-green-300";
    if (score >= 0.9) return "bg-blue-100 text-blue-800 border-blue-300";
    if (score >= 0.8) return "bg-yellow-100 text-yellow-800 border-yellow-300";
    return "bg-gray-100 text-gray-800 border-gray-300";
  }
  function getScoreIcon(score) {
    if (score >= 0.95) return "🎯";
    if (score >= 0.9) return "✅";
    if (score >= 0.8) return "👍";
    return "📝";
  }
  function truncateApiName(name, maxLength = 30) {
    if (name.length <= maxLength) return name;
    return name.substring(0, maxLength) + "...";
  }
  function truncateThoughts(thoughtsArray, maxLength = 120) {
    const firstThought = thoughtsArray[0] || "";
    if (firstThought.length <= maxLength) return firstThought;
    return firstThought.substring(0, maxLength) + "...";
  }
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "max-w-4xl mx-auto"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-white rounded-lg border border-gray-200 p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
    className: "text-sm font-medium text-gray-700 flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, "\uD83D\uDD0D"), "API Shortlist"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "px-2 py-1 rounded text-xs bg-purple-100 text-purple-700"
  }, result.length, " APIs selected")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "space-y-2 mb-3"
  }, displayedApis.map((api, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    className: "border rounded p-2 hover:shadow-sm transition-shadow"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-start justify-between mb-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, getScoreIcon(api.relevance_score)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h4", {
    className: "font-medium text-gray-800 text-xs"
  }, truncateApiName(api.name, 25)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2 mt-1"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: `px-1.5 py-0.5 rounded text-xs font-medium ${getScoreColor(api.relevance_score)}`
  }, (api.relevance_score * 100).toFixed(0), "%"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-500"
  }, "#", index + 1))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-600 leading-relaxed pl-5"
  }, api.reasoning)))), result.length > 2 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-center mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: () => setShowAllApis(!showAllApis),
    className: "px-3 py-1 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded text-xs font-medium transition-colors flex items-center gap-1 mx-auto"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, showAllApis ? "Show less" : `Show ${remainingCount} more`), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs"
  }, showAllApis ? "▲" : "▼"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "grid grid-cols-3 gap-2 mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-center p-2 bg-green-50 rounded"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-sm font-bold text-green-700"
  }, result.filter(api => api.relevance_score >= 0.95).length), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-xs text-green-600"
  }, "High Priority")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-center p-2 bg-blue-50 rounded"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-sm font-bold text-blue-700"
  }, (result.reduce((sum, api) => sum + api.relevance_score, 0) / result.length * 100).toFixed(0), "%"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-xs text-blue-600"
  }, "Avg Score")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-center p-2 bg-purple-50 rounded"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-sm font-bold text-purple-700"
  }, result.length), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-xs text-purple-600"
  }, "APIs Found"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "border-t border-gray-100 pt-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-400"
  }, "\uD83D\uDCAD"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-500"
  }, "Analysis (", thoughts.length, ")"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: () => setShowFullThoughts(!showFullThoughts),
    className: "text-xs text-gray-400 hover:text-gray-600"
  }, showFullThoughts ? "▲" : "▼"))), !showFullThoughts && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-400 italic mt-1"
  }, truncateThoughts(thoughts, 80)), showFullThoughts && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mt-2 space-y-1"
  }, thoughts.map((thought, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    className: "flex items-start gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-300 mt-0.5 font-mono"
  }, index + 1, "."), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-500 leading-relaxed"
  }, thought))))))));
}

/***/ }),

/***/ "../agentic_chat/src/task_decomposition.tsx":
/*!**************************************************!*\
  !*** ../agentic_chat/src/task_decomposition.tsx ***!
  \**************************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ TaskDecompositionComponent; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);

function TaskDecompositionComponent({
  decompositionData
}) {
  const [showFullThoughts, setShowFullThoughts] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);

  // Extract data from props
  const {
    thoughts,
    task_decomposition
  } = decompositionData;
  function getAppIcon(appName) {
    switch (appName?.toLowerCase()) {
      case "gmail":
        return "📧";
      case "phone":
        return "📱";
      case "venmo":
        return "💰";
      case "calendar":
        return "📅";
      case "drive":
        return "📁";
      case "sheets":
        return "📊";
      case "slack":
        return "💬";
      default:
        return "🔧";
    }
  }
  function getAppColor(appName) {
    switch (appName?.toLowerCase()) {
      case "gmail":
        return "bg-red-100 text-red-800 border-red-200";
      case "phone":
        return "bg-blue-100 text-blue-800 border-blue-200";
      case "venmo":
        return "bg-green-100 text-green-800 border-green-200";
      case "calendar":
        return "bg-purple-100 text-purple-800 border-purple-200";
      case "drive":
        return "bg-yellow-100 text-yellow-800 border-yellow-200";
      default:
        return "bg-gray-100 text-gray-800 border-gray-200";
    }
  }
  function getStepNumber(index) {
    return String(index + 1).padStart(2, "0");
  }
  function truncateThoughts(text, maxLength = 120) {
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + "...";
  }
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "max-w-4xl mx-auto"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-white rounded-lg border border-gray-200 p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
    className: "text-sm font-medium text-gray-700 flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, "\uD83D\uDCCB"), "Task Breakdown"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "px-2 py-1 rounded text-xs bg-blue-100 text-blue-700"
  }, task_decomposition.length, " steps planned")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "space-y-2 mb-3"
  }, task_decomposition.map((task, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    className: "relative"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-start gap-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex-shrink-0 w-6 h-6 bg-blue-500 text-white rounded-full flex items-center justify-center font-bold text-xs"
  }, getStepNumber(index)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex-1 bg-gray-50 rounded p-2 border"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2 mb-1"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: `px-2 py-0.5 rounded text-xs font-medium ${getAppColor(task.app)}`
  }, getAppIcon(task.app), " ", task.app), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "px-1.5 py-0.5 bg-white rounded text-xs text-gray-600 border"
  }, task.type)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-700 leading-relaxed"
  }, task.task)))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "border-t border-gray-100 pt-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-400"
  }, "\uD83D\uDCAD"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-500"
  }, "Analysis"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: () => setShowFullThoughts(!showFullThoughts),
    className: "text-xs text-gray-400 hover:text-gray-600"
  }, showFullThoughts ? "▲" : "▼"))), !showFullThoughts && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-400 italic mt-1"
  }, truncateThoughts(thoughts, 80)), showFullThoughts && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mt-2 space-y-1"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-500 leading-relaxed"
  }, thoughts))))));
}

/***/ }),

/***/ "../agentic_chat/src/task_status_component.tsx":
/*!*****************************************************!*\
  !*** ../agentic_chat/src/task_status_component.tsx ***!
  \*****************************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ TaskStatusDashboard; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);

function TaskStatusDashboard({
  taskData
}) {
  const [showFullThoughts, setShowFullThoughts] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);

  // Sample data - you can replace this with props

  const {
    thoughts,
    subtasks_progress,
    next_subtask,
    next_subtask_type,
    next_subtask_app,
    conclude_task,
    conclude_final_answer
  } = taskData;
  const total = subtasks_progress.length;
  const completed = subtasks_progress.filter(status => status === "completed").length;
  const progressPercentage = completed / total * 100;
  function getStatusIcon(status) {
    if (status === "completed") return "✅";
    if (status === "in-progress") return "🔄";
    if (status === "not-started") return "⏳";
    return "❓";
  }
  function getAppIcon(app) {
    if (!app) return "🔧";
    const appLower = app.toLowerCase();
    if (appLower === "gmail") return "📧";
    if (appLower === "calendar") return "📅";
    if (appLower === "drive") return "📁";
    if (appLower === "sheets") return "📊";
    return "🔧";
  }
  function getTypeColor(type) {
    if (type === "api") return "bg-blue-100 text-blue-800";
    if (type === "analysis") return "bg-purple-100 text-purple-800";
    if (type === "calculation") return "bg-green-100 text-green-800";
    return "bg-gray-100 text-gray-800";
  }
  function truncateText(text, maxLength = 80) {
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + "...";
  }

  // Create a summary of thoughts
  function getThoughtsSummary() {
    if (thoughts.length === 0) return "No thoughts recorded";
    const firstThought = truncateText(thoughts[0], 100);
    return firstThought;
  }
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "max-w-3xl mx-auto"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-white rounded-lg border border-gray-200 p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
    className: "text-sm font-medium text-gray-700"
  }, "Task Progress"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: `px-2 py-1 rounded text-xs font-medium ${conclude_task ? "bg-green-100 text-green-700" : "bg-yellow-100 text-yellow-700"}`
  }, conclude_task ? "Complete" : "Active")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-3 p-2 bg-gray-50 rounded border"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between mb-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-600"
  }, "Subtasks"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-500"
  }, completed, "/", total)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex-1 bg-gray-200 rounded-full h-1.5"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-green-500 h-1.5 rounded-full transition-all duration-300",
    style: {
      width: `${progressPercentage}%`
    }
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex gap-1"
  }, subtasks_progress.map((status, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    key: index,
    className: "text-sm hover:scale-110 transition-transform cursor-pointer",
    title: `Task ${index + 1}: ${status.replace("-", " ")}`
  }, getStatusIcon(status)))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-3 p-2 bg-blue-50 rounded border border-blue-200"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2 mb-1"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, "\uD83C\uDFAF"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-600"
  }, "Next:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: `px-1.5 py-0.5 rounded text-xs ${getTypeColor(next_subtask_type)}`
  }, next_subtask_type), next_subtask_app && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "flex items-center gap-1 px-1.5 py-0.5 bg-white rounded text-xs text-gray-600 border"
  }, getAppIcon(next_subtask_app), " ", next_subtask_app)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-700 leading-relaxed pl-5"
  }, next_subtask)), conclude_final_answer && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-3 p-2 bg-green-50 rounded border border-green-200"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2 mb-1"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, "\uD83C\uDF89"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-green-700 font-medium"
  }, "Result")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-green-600"
  }, conclude_final_answer)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "border-t border-gray-100 pt-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-400"
  }, "\uD83D\uDCAD"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-500"
  }, "Analysis (", thoughts.length, ")"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: () => setShowFullThoughts(!showFullThoughts),
    className: "text-xs text-gray-400 hover:text-gray-600"
  }, showFullThoughts ? "▲" : "▼"))), !showFullThoughts && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-400 italic mt-1"
  }, getThoughtsSummary()), showFullThoughts && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mt-2 space-y-1"
  }, thoughts.map((thought, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    className: "flex items-start gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-300 mt-0.5 font-mono"
  }, index + 1, "."), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-500 leading-relaxed"
  }, thought))))))));
}

/***/ }),

/***/ "../agentic_chat/src/useTour.ts":
/*!**************************************!*\
  !*** ../agentic_chat/src/useTour.ts ***!
  \**************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   useTour: function() { return /* binding */ useTour; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);

const TOUR_COMPLETED_KEY = "cuga_tour_completed";
function useTour() {
  const [isTourActive, setIsTourActive] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [hasSeenTour, setHasSeenTour] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    const tourCompleted = localStorage.getItem(TOUR_COMPLETED_KEY);
    if (tourCompleted === "true") {
      setHasSeenTour(true);
    }
  }, []);
  const startTour = () => {
    setIsTourActive(true);
  };
  const completeTour = () => {
    setIsTourActive(false);
    setHasSeenTour(true);
    localStorage.setItem(TOUR_COMPLETED_KEY, "true");
  };
  const skipTour = () => {
    setIsTourActive(false);
    setHasSeenTour(true);
    localStorage.setItem(TOUR_COMPLETED_KEY, "true");
  };
  const resetTour = () => {
    setHasSeenTour(false);
    localStorage.removeItem(TOUR_COMPLETED_KEY);
    setIsTourActive(true);
  };
  return {
    isTourActive,
    hasSeenTour,
    startTour,
    completeTour,
    skipTour,
    resetTour
  };
}

/***/ }),

/***/ "../agentic_chat/src/workspaceThrottle.ts":
/*!************************************************!*\
  !*** ../agentic_chat/src/workspaceThrottle.ts ***!
  \************************************************/
/***/ (function() {

// Global fetch interceptor to enforce throttling on workspace API calls
// This is a safety net to catch any direct fetch calls that bypass the service

let lastWorkspaceApiCall = 0;
const MIN_INTERVAL = 3000; // 3 seconds

// Store the original fetch
const originalFetch = window.fetch;

// Override fetch globally
window.fetch = function (...args) {
  const [resource] = args;
  const url = typeof resource === 'string' ? resource : resource.url;

  // Check if this is a workspace tree API call
  if (url.includes('/api/workspace/tree')) {
    const now = Date.now();
    const timeSinceLastCall = now - lastWorkspaceApiCall;

    // If called too soon, reject the request
    if (timeSinceLastCall < MIN_INTERVAL) {
      const remainingTime = MIN_INTERVAL - timeSinceLastCall;
      console.warn(`⚠️ Workspace API throttled! Request blocked. ` + `Last call was ${timeSinceLastCall}ms ago. ` + `Minimum interval is ${MIN_INTERVAL}ms. ` + `Wait ${remainingTime}ms before next call.`);

      // Return a rejected promise with a clear error
      return Promise.reject(new Error(`Workspace API call throttled. Wait ${remainingTime}ms before retrying.`));
    }

    // Update last call time
    lastWorkspaceApiCall = now;
    console.log(`✅ Workspace API call allowed (${timeSinceLastCall}ms since last call)`);
  }

  // Call original fetch
  return originalFetch.apply(this, args);
};
 // Make this a module

/***/ }),

/***/ "./src/App.tsx":
/*!*********************!*\
  !*** ./src/App.tsx ***!
  \*********************/
/***/ (function(__unused_webpack_module, __unused_webpack___webpack_exports__, __webpack_require__) {

/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var react_dom_client__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! react-dom/client */ "../node_modules/.pnpm/react-dom@18.3.1_react@18.3.1/node_modules/react-dom/client.js");
/* harmony import */ var agentic_chat__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! agentic_chat */ "../agentic_chat/src/App.tsx");



function renderApp() {
  const rootElement = document.getElementById("root");
  if (!rootElement) {
    throw new Error("Root element with id 'root' not found in index.html");
  }
  const root = (0,react_dom_client__WEBPACK_IMPORTED_MODULE_1__.createRoot)(rootElement);
  root.render(/*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(agentic_chat__WEBPACK_IMPORTED_MODULE_2__.App, null));
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderApp);
} else {
  renderApp();
}

/***/ })

/******/ 	});
/************************************************************************/
/******/ 	// The module cache
/******/ 	var __webpack_module_cache__ = {};
/******/ 	
/******/ 	// The require function
/******/ 	function __webpack_require__(moduleId) {
/******/ 		// Check if module is in cache
/******/ 		var cachedModule = __webpack_module_cache__[moduleId];
/******/ 		if (cachedModule !== undefined) {
/******/ 			return cachedModule.exports;
/******/ 		}
/******/ 		// Create a new module (and put it into the cache)
/******/ 		var module = __webpack_module_cache__[moduleId] = {
/******/ 			id: moduleId,
/******/ 			loaded: false,
/******/ 			exports: {}
/******/ 		};
/******/ 	
/******/ 		// Execute the module function
/******/ 		__webpack_modules__[moduleId].call(module.exports, module, module.exports, __webpack_require__);
/******/ 	
/******/ 		// Flag the module as loaded
/******/ 		module.loaded = true;
/******/ 	
/******/ 		// Return the exports of the module
/******/ 		return module.exports;
/******/ 	}
/******/ 	
/******/ 	// expose the modules object (__webpack_modules__)
/******/ 	__webpack_require__.m = __webpack_modules__;
/******/ 	
/************************************************************************/
/******/ 	/* webpack/runtime/chunk loaded */
/******/ 	!function() {
/******/ 		var deferred = [];
/******/ 		__webpack_require__.O = function(result, chunkIds, fn, priority) {
/******/ 			if(chunkIds) {
/******/ 				priority = priority || 0;
/******/ 				for(var i = deferred.length; i > 0 && deferred[i - 1][2] > priority; i--) deferred[i] = deferred[i - 1];
/******/ 				deferred[i] = [chunkIds, fn, priority];
/******/ 				return;
/******/ 			}
/******/ 			var notFulfilled = Infinity;
/******/ 			for (var i = 0; i < deferred.length; i++) {
/******/ 				var chunkIds = deferred[i][0];
/******/ 				var fn = deferred[i][1];
/******/ 				var priority = deferred[i][2];
/******/ 				var fulfilled = true;
/******/ 				for (var j = 0; j < chunkIds.length; j++) {
/******/ 					if ((priority & 1 === 0 || notFulfilled >= priority) && Object.keys(__webpack_require__.O).every(function(key) { return __webpack_require__.O[key](chunkIds[j]); })) {
/******/ 						chunkIds.splice(j--, 1);
/******/ 					} else {
/******/ 						fulfilled = false;
/******/ 						if(priority < notFulfilled) notFulfilled = priority;
/******/ 					}
/******/ 				}
/******/ 				if(fulfilled) {
/******/ 					deferred.splice(i--, 1)
/******/ 					var r = fn();
/******/ 					if (r !== undefined) result = r;
/******/ 				}
/******/ 			}
/******/ 			return result;
/******/ 		};
/******/ 	}();
/******/ 	
/******/ 	/* webpack/runtime/compat get default export */
/******/ 	!function() {
/******/ 		// getDefaultExport function for compatibility with non-harmony modules
/******/ 		__webpack_require__.n = function(module) {
/******/ 			var getter = module && module.__esModule ?
/******/ 				function() { return module['default']; } :
/******/ 				function() { return module; };
/******/ 			__webpack_require__.d(getter, { a: getter });
/******/ 			return getter;
/******/ 		};
/******/ 	}();
/******/ 	
/******/ 	/* webpack/runtime/create fake namespace object */
/******/ 	!function() {
/******/ 		var getProto = Object.getPrototypeOf ? function(obj) { return Object.getPrototypeOf(obj); } : function(obj) { return obj.__proto__; };
/******/ 		var leafPrototypes;
/******/ 		// create a fake namespace object
/******/ 		// mode & 1: value is a module id, require it
/******/ 		// mode & 2: merge all properties of value into the ns
/******/ 		// mode & 4: return value when already ns object
/******/ 		// mode & 16: return value when it's Promise-like
/******/ 		// mode & 8|1: behave like require
/******/ 		__webpack_require__.t = function(value, mode) {
/******/ 			if(mode & 1) value = this(value);
/******/ 			if(mode & 8) return value;
/******/ 			if(typeof value === 'object' && value) {
/******/ 				if((mode & 4) && value.__esModule) return value;
/******/ 				if((mode & 16) && typeof value.then === 'function') return value;
/******/ 			}
/******/ 			var ns = Object.create(null);
/******/ 			__webpack_require__.r(ns);
/******/ 			var def = {};
/******/ 			leafPrototypes = leafPrototypes || [null, getProto({}), getProto([]), getProto(getProto)];
/******/ 			for(var current = mode & 2 && value; (typeof current == 'object' || typeof current == 'function') && !~leafPrototypes.indexOf(current); current = getProto(current)) {
/******/ 				Object.getOwnPropertyNames(current).forEach(function(key) { def[key] = function() { return value[key]; }; });
/******/ 			}
/******/ 			def['default'] = function() { return value; };
/******/ 			__webpack_require__.d(ns, def);
/******/ 			return ns;
/******/ 		};
/******/ 	}();
/******/ 	
/******/ 	/* webpack/runtime/define property getters */
/******/ 	!function() {
/******/ 		// define getter functions for harmony exports
/******/ 		__webpack_require__.d = function(exports, definition) {
/******/ 			for(var key in definition) {
/******/ 				if(__webpack_require__.o(definition, key) && !__webpack_require__.o(exports, key)) {
/******/ 					Object.defineProperty(exports, key, { enumerable: true, get: definition[key] });
/******/ 				}
/******/ 			}
/******/ 		};
/******/ 	}();
/******/ 	
/******/ 	/* webpack/runtime/ensure chunk */
/******/ 	!function() {
/******/ 		__webpack_require__.f = {};
/******/ 		// This file contains only the entry chunk.
/******/ 		// The chunk loading function for additional chunks
/******/ 		__webpack_require__.e = function(chunkId) {
/******/ 			return Promise.all(Object.keys(__webpack_require__.f).reduce(function(promises, key) {
/******/ 				__webpack_require__.f[key](chunkId, promises);
/******/ 				return promises;
/******/ 			}, []));
/******/ 		};
/******/ 	}();
/******/ 	
/******/ 	/* webpack/runtime/get javascript chunk filename */
/******/ 	!function() {
/******/ 		// This function allow to reference async chunks
/******/ 		__webpack_require__.u = function(chunkId) {
/******/ 			// return url for filenames based on template
/******/ 			return "" + chunkId + "." + "c8f14ca9886c620ac587" + ".bundle.js";
/******/ 		};
/******/ 	}();
/******/ 	
/******/ 	/* webpack/runtime/global */
/******/ 	!function() {
/******/ 		__webpack_require__.g = (function() {
/******/ 			if (typeof globalThis === 'object') return globalThis;
/******/ 			try {
/******/ 				return this || new Function('return this')();
/******/ 			} catch (e) {
/******/ 				if (typeof window === 'object') return window;
/******/ 			}
/******/ 		})();
/******/ 	}();
/******/ 	
/******/ 	/* webpack/runtime/hasOwnProperty shorthand */
/******/ 	!function() {
/******/ 		__webpack_require__.o = function(obj, prop) { return Object.prototype.hasOwnProperty.call(obj, prop); }
/******/ 	}();
/******/ 	
/******/ 	/* webpack/runtime/load script */
/******/ 	!function() {
/******/ 		var inProgress = {};
/******/ 		var dataWebpackPrefix = "@carbon/ai-chat-examples-web-components-basic:";
/******/ 		// loadScript function to load a script via script tag
/******/ 		__webpack_require__.l = function(url, done, key, chunkId) {
/******/ 			if(inProgress[url]) { inProgress[url].push(done); return; }
/******/ 			var script, needAttach;
/******/ 			if(key !== undefined) {
/******/ 				var scripts = document.getElementsByTagName("script");
/******/ 				for(var i = 0; i < scripts.length; i++) {
/******/ 					var s = scripts[i];
/******/ 					if(s.getAttribute("src") == url || s.getAttribute("data-webpack") == dataWebpackPrefix + key) { script = s; break; }
/******/ 				}
/******/ 			}
/******/ 			if(!script) {
/******/ 				needAttach = true;
/******/ 				script = document.createElement('script');
/******/ 		
/******/ 				script.charset = 'utf-8';
/******/ 				script.timeout = 120;
/******/ 				if (__webpack_require__.nc) {
/******/ 					script.setAttribute("nonce", __webpack_require__.nc);
/******/ 				}
/******/ 				script.setAttribute("data-webpack", dataWebpackPrefix + key);
/******/ 		
/******/ 				script.src = url;
/******/ 			}
/******/ 			inProgress[url] = [done];
/******/ 			var onScriptComplete = function(prev, event) {
/******/ 				// avoid mem leaks in IE.
/******/ 				script.onerror = script.onload = null;
/******/ 				clearTimeout(timeout);
/******/ 				var doneFns = inProgress[url];
/******/ 				delete inProgress[url];
/******/ 				script.parentNode && script.parentNode.removeChild(script);
/******/ 				doneFns && doneFns.forEach(function(fn) { return fn(event); });
/******/ 				if(prev) return prev(event);
/******/ 			}
/******/ 			var timeout = setTimeout(onScriptComplete.bind(null, undefined, { type: 'timeout', target: script }), 120000);
/******/ 			script.onerror = onScriptComplete.bind(null, script.onerror);
/******/ 			script.onload = onScriptComplete.bind(null, script.onload);
/******/ 			needAttach && document.head.appendChild(script);
/******/ 		};
/******/ 	}();
/******/ 	
/******/ 	/* webpack/runtime/make namespace object */
/******/ 	!function() {
/******/ 		// define __esModule on exports
/******/ 		__webpack_require__.r = function(exports) {
/******/ 			if(typeof Symbol !== 'undefined' && Symbol.toStringTag) {
/******/ 				Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' });
/******/ 			}
/******/ 			Object.defineProperty(exports, '__esModule', { value: true });
/******/ 		};
/******/ 	}();
/******/ 	
/******/ 	/* webpack/runtime/node module decorator */
/******/ 	!function() {
/******/ 		__webpack_require__.nmd = function(module) {
/******/ 			module.paths = [];
/******/ 			if (!module.children) module.children = [];
/******/ 			return module;
/******/ 		};
/******/ 	}();
/******/ 	
/******/ 	/* webpack/runtime/publicPath */
/******/ 	!function() {
/******/ 		__webpack_require__.p = "/";
/******/ 	}();
/******/ 	
/******/ 	/* webpack/runtime/jsonp chunk loading */
/******/ 	!function() {
/******/ 		// no baseURI
/******/ 		
/******/ 		// object to store loaded and loading chunks
/******/ 		// undefined = chunk not loaded, null = chunk preloaded/prefetched
/******/ 		// [resolve, reject, Promise] = chunk loading, 0 = chunk loaded
/******/ 		var installedChunks = {
/******/ 			"main-a": 0
/******/ 		};
/******/ 		
/******/ 		__webpack_require__.f.j = function(chunkId, promises) {
/******/ 				// JSONP chunk loading for javascript
/******/ 				var installedChunkData = __webpack_require__.o(installedChunks, chunkId) ? installedChunks[chunkId] : undefined;
/******/ 				if(installedChunkData !== 0) { // 0 means "already installed".
/******/ 		
/******/ 					// a Promise means "currently loading".
/******/ 					if(installedChunkData) {
/******/ 						promises.push(installedChunkData[2]);
/******/ 					} else {
/******/ 						if(true) { // all chunks have JS
/******/ 							// setup Promise in chunk cache
/******/ 							var promise = new Promise(function(resolve, reject) { installedChunkData = installedChunks[chunkId] = [resolve, reject]; });
/******/ 							promises.push(installedChunkData[2] = promise);
/******/ 		
/******/ 							// start chunk loading
/******/ 							var url = __webpack_require__.p + __webpack_require__.u(chunkId);
/******/ 							// create error before stack unwound to get useful stacktrace later
/******/ 							var error = new Error();
/******/ 							var loadingEnded = function(event) {
/******/ 								if(__webpack_require__.o(installedChunks, chunkId)) {
/******/ 									installedChunkData = installedChunks[chunkId];
/******/ 									if(installedChunkData !== 0) installedChunks[chunkId] = undefined;
/******/ 									if(installedChunkData) {
/******/ 										var errorType = event && (event.type === 'load' ? 'missing' : event.type);
/******/ 										var realSrc = event && event.target && event.target.src;
/******/ 										error.message = 'Loading chunk ' + chunkId + ' failed.\n(' + errorType + ': ' + realSrc + ')';
/******/ 										error.name = 'ChunkLoadError';
/******/ 										error.type = errorType;
/******/ 										error.request = realSrc;
/******/ 										installedChunkData[1](error);
/******/ 									}
/******/ 								}
/******/ 							};
/******/ 							__webpack_require__.l(url, loadingEnded, "chunk-" + chunkId, chunkId);
/******/ 						}
/******/ 					}
/******/ 				}
/******/ 		};
/******/ 		
/******/ 		// no prefetching
/******/ 		
/******/ 		// no preloaded
/******/ 		
/******/ 		// no HMR
/******/ 		
/******/ 		// no HMR manifest
/******/ 		
/******/ 		__webpack_require__.O.j = function(chunkId) { return installedChunks[chunkId] === 0; };
/******/ 		
/******/ 		// install a JSONP callback for chunk loading
/******/ 		var webpackJsonpCallback = function(parentChunkLoadingFunction, data) {
/******/ 			var chunkIds = data[0];
/******/ 			var moreModules = data[1];
/******/ 			var runtime = data[2];
/******/ 			// add "moreModules" to the modules object,
/******/ 			// then flag all "chunkIds" as loaded and fire callback
/******/ 			var moduleId, chunkId, i = 0;
/******/ 			if(chunkIds.some(function(id) { return installedChunks[id] !== 0; })) {
/******/ 				for(moduleId in moreModules) {
/******/ 					if(__webpack_require__.o(moreModules, moduleId)) {
/******/ 						__webpack_require__.m[moduleId] = moreModules[moduleId];
/******/ 					}
/******/ 				}
/******/ 				if(runtime) var result = runtime(__webpack_require__);
/******/ 			}
/******/ 			if(parentChunkLoadingFunction) parentChunkLoadingFunction(data);
/******/ 			for(;i < chunkIds.length; i++) {
/******/ 				chunkId = chunkIds[i];
/******/ 				if(__webpack_require__.o(installedChunks, chunkId) && installedChunks[chunkId]) {
/******/ 					installedChunks[chunkId][0]();
/******/ 				}
/******/ 				installedChunks[chunkId] = 0;
/******/ 			}
/******/ 			return __webpack_require__.O(result);
/******/ 		}
/******/ 		
/******/ 		var chunkLoadingGlobal = self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] = self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] || [];
/******/ 		chunkLoadingGlobal.forEach(webpackJsonpCallback.bind(null, 0));
/******/ 		chunkLoadingGlobal.push = webpackJsonpCallback.bind(null, chunkLoadingGlobal.push.bind(chunkLoadingGlobal));
/******/ 	}();
/******/ 	
/******/ 	/* webpack/runtime/nonce */
/******/ 	!function() {
/******/ 		__webpack_require__.nc = undefined;
/******/ 	}();
/******/ 	
/************************************************************************/
/******/ 	
/******/ 	// startup
/******/ 	// Load entry module and return exports
/******/ 	// This entry module depends on other loaded chunks and execution need to be delayed
/******/ 	var __webpack_exports__ = __webpack_require__.O(undefined, ["carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-2f663faa","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-9f7fbe89","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-b5b18e5d","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-39f6723d","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-e85f78fe","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-b85a65c7","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-ba4fc5fb","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-9cc033bd","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-e8e2c39f","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-05aaa742","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-3b4f4b29","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-dd2b6fa4","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-cff9deb0","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-b023e42a","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-2a45e066","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-3ce26c61","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-aa45513c","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-c98adbbe","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-9b26fd87","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-a9d6fc8b","carbon-icons-node_modules_pnpm_carbon_icons-react_11_66_0_react_18_3_1_node_modules_carbon_icons-rea-a5720aa3","carbon-ai-node_modules_pnpm_carbon_ai-chat_0_5_1__carbon_icon-helpers_10_65_0__carbon_icons_11_66_0_-d4a1317f","carbon-ai-node_modules_pnpm_carbon_ai-chat_0_5_1__carbon_icon-helpers_10_65_0__carbon_icons_11_66_0_-d3cb8c66","carbon-ai-node_modules_pnpm_carbon_ai-chat_0_5_1__carbon_icon-helpers_10_65_0__carbon_icons_11_66_0_-8667ab37","react-vendor-node_modules_pnpm_react-dom_18_3_1_react_18_3_1_node_modules_react-dom_cjs_react-dom_de-759bfbd0","react-vendor-node_modules_pnpm_react-","vendors-node_modules_pnpm_b","vendors-node_modules_pnpm_carbon_l","vendors-node_modules_pnpm_carbon_react_1_90_0_react-dom_18_3_1_react_18_3_1__react-is_18_3_1_react_1-13b04b53","vendors-node_modules_pnpm_carbon_react_1_90_0_react-dom_18_3_1_react_18_3_1__react-is_18_3_1_react_1-839d2ccb","vendors-node_modules_pnpm_carbon_react_1_90_0_react-dom_18_3_1_react_18_3_1__react-is_18_3_1_react_1-31b657cd","vendors-node_modules_pnpm_carbon_react_1_90_0_react-dom_18_3_1_react_18_3_1__react-is_18_3_1_react_1-26731242","vendors-node_modules_pnpm_carbon_react_1_90_0_react-dom_18_3_1_react_18_3_1__react-is_18_3_1_react_1-797ff8f8","vendors-node_modules_pnpm_carbon_react_1_90_0_react-dom_18_3_1_react_18_3_1__react-is_18_3_1_react_1-edeb92d6","vendors-node_modules_pnpm_carbon_react_1_90_0_react-dom_18_3_1_react_18_3_1__react-is_18_3_1_react_1-6d6df33e","vendors-node_modules_pnpm_carbon_react_1_90_0_react-dom_18_3_1_react_18_3_1__react-is_18_3_1_react_1-a464c5e8","vendors-node_modules_pnpm_carbon_react_1_90_0_react-dom_18_3_1_react_18_3_1__react-is_18_3_1_react_1-41e67ea6","vendors-node_modules_pnpm_carbon_react_1_90_0_react-dom_18_3_1_react_18_3_1__react-is_18_3_1_react_1-b69a6df4","vendors-node_modules_pnpm_carbon_u","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-85df7579","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-cf0d06ed","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-79c59b4d","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-e7da3670","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-3a8f4a2e","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-1a92870d","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-4e4cd922","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-38e42bff","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-c7b73f71","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-a280c048","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-17ff82ae","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-a9110098","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-0d8eaa46","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-decc499d","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-c0a18861","vendors-node_modules_pnpm_carbon_web-components_2_37_0_sass_1_92_0_node_modules_carbon_web-component-df01a57a","vendors-node_modules_pnpm_cl","vendors-node_modules_pnpm_c","vendors-node_modules_pnpm_deb","vendors-node_modules_pnpm_dom","vendors-node_modules_pnpm_en","vendors-node_modules_pnpm_flatpickr_4_6_13_node_modules_flatpickr_dist_e","vendors-node_modules_pnpm_floating-ui_c","vendors-node_modules_pnpm_fl","vendors-node_modules_pnpm_focus-trap_7_6_5_node_modules_focus-trap_dist_focus-trap_esm_js-1d85fa01","vendors-node_modules_pnpm_formatjs_f","vendors-node_modules_pnpm_hast-util-t","vendors-node_modules_pnpm_highlight_js_11_11_1_node_modules_highlight_js_e","vendors-node_modules_pnpm_highlight_js_11_11_1_node_modules_highlight_js_lib_languages_b","vendors-node_modules_pnpm_highlight_js_11_11_1_node_modules_highlight_js_lib_languages_r","vendors-node_modules_pnpm_h","vendors-node_modules_pnpm_i","vendors-node_modules_pnpm_lin","vendors-node_modules_pnpm_li","vendors-node_modules_pnpm_lucide-react_0_525_0_react_18_3_1_node_modules_lucide-react_dist_esm_I","vendors-node_modules_pnpm_lucide-react_0_525_0_react_18_3_1_node_modules_lucide-react_dist_esm_icons-76dd0623","vendors-node_modules_pnpm_lucide-react_0_525_0_react_18_3_1_node_modules_lucide-react_dist_esm_icons-4481b133","vendors-node_modules_pnpm_lucide-react_0_525_0_react_18_3_1_node_modules_lucide-react_dist_esm_icons-5d2ec391","vendors-node_modules_pnpm_lucide-react_0_525_0_react_18_3_1_node_modules_lucide-react_dist_esm_icons-6717c296","vendors-node_modules_pnpm_lucide-react_0_525_0_react_18_3_1_node_modules_lucide-react_dist_esm_icons-d9ba084e","vendors-node_modules_pnpm_lucide-react_0_525_0_react_18_3_1_node_modules_lucide-react_dist_esm_icons-a09dce7f","vendors-node_modules_pnpm_lucide-react_0_525_0_react_18_3_1_node_modules_lucide-react_dist_esm_icons-6764a464","vendors-node_modules_pnpm_lucide-react_0_525_0_react_18_3_1_node_modules_lucide-react_dist_esm_l","vendors-node_modules_pnpm_markdown-it-attrs_4_3_1_markdown-it_14_1_0_node_modules_markdown-it-attrs_-5aa477fa","vendors-node_modules_pnpm_markd","vendors-node_modules_pnpm_mda","vendors-node_modules_pnpm_me","vendors-node_modules_pnpm_mi","vendors-node_modules_pnpm_o","vendors-node_modules_pnpm_react-f","vendors-node_modules_pnpm_redux_4_2_1_node_modules_redux_es_redux_js-db2ec3f0","vendors-node_modules_pnpm_r","vendors-node_modules_pnpm_si","vendors-node_modules_pnpm_swiper_11_2_10_node_modules_swiper_modules_a","vendors-node_modules_pnpm_swiper_11_2_10_node_modules_swiper_sh","vendors-node_modules_pnpm_ta","vendors-node_modules_pnpm_t","vendors-node_modules_pnpm_v","main-agentic_chat_src_Ad","main-agentic_chat_src_CardManager_c","main-agentic_chat_src_Co","main-agentic_chat_src_D","main-agentic_chat_src_Polici","main-agentic_chat_src_St","main-agentic_chat_src_T"], function() { return __webpack_require__("./src/App.tsx"); })
/******/ 	__webpack_exports__ = __webpack_require__.O(__webpack_exports__);
/******/ 	
/******/ })()
;
//# sourceMappingURL=main-a.bb4d313d0cf6c67cf3f2.js.map