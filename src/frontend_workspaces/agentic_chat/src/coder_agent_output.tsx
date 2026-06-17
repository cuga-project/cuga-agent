import React, { useState } from "react";
import Markdown from "react-markdown";

const REFLECTION_SEPARATOR = "\n\n---\n\nSummary:\n";

function splitExecutionAndReflection(text: string) {
  if (!text || !text.includes(REFLECTION_SEPARATOR)) {
    return { execution: text || "", reflection: "" };
  }
  const [execution, reflection] = text.split(REFLECTION_SEPARATOR, 2);
  return { execution: execution.trim(), reflection: reflection.trim() };
}

export default function CoderAgentOutput({ coderData }) {
  const [showFullCode, setShowFullCode] = useState(false);
  const [showFullOutput, setShowFullOutput] = useState(false);
  const [showFullReflection, setShowFullReflection] = useState(false);

  const { code = "", summary, execution_output, reflection, variables } = coderData;
  const rawOutput = execution_output || summary || "";
  const splitOutput = splitExecutionAndReflection(rawOutput);
  const output = reflection ? rawOutput : splitOutput.execution;
  const reflectionText = reflection || splitOutput.reflection;

  function getCodeSnippet(fullCode, maxLines = 4) {
    if (!fullCode) return "";
    const lines = fullCode.split("\n");
    if (lines.length <= maxLines) return fullCode;
    return lines.slice(0, maxLines).join("\n") + "\n...";
  }

  function truncateOutput(text, maxLength = 400) {
    if (!text) return "";
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + "...";
  }

  const codeLines = code ? code.split("\n").length : 0;
  const outputLength = output ? output.length : 0;
  const hasVariables = variables && Object.keys(variables).length > 0;

  return (
    <div className="p-3">
      <div className="max-w-3xl mx-auto">
        <div className="bg-white rounded-lg border border-gray-200 p-3">
          {/* Header */}
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-gray-700 flex items-center gap-2">
              <span className="text-sm">💻</span>
              Coder Agent
            </h3>
            <span className="px-2 py-1 rounded text-xs bg-purple-100 text-purple-700">
              {output ? "Complete" : "In Progress"}
            </span>
          </div>

          {/* Code Section - Only show if we have code */}
          {code && (
            <div className="mb-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-gray-600">Code ({codeLines} lines)</span>
                {codeLines > 4 && (
                  <button
                    onClick={() => setShowFullCode(!showFullCode)}
                    className="text-xs text-purple-600 hover:text-purple-800"
                  >
                    {showFullCode ? "▲ Less" : "▼ More"}
                  </button>
                )}
              </div>

              <div className="bg-gray-900 rounded p-3" style={{ overflowX: "auto" }}>
                <pre className="text-green-400 text-xs font-mono leading-relaxed">{showFullCode ? code : getCodeSnippet(code)}</pre>
              </div>
            </div>
          )}

          {/* Output Section - Only show if we have output */}
          {output && (
            <div className="mb-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-gray-600">Execution Output ({outputLength} chars)</span>
                {outputLength > 400 && (
                  <button
                    onClick={() => setShowFullOutput(!showFullOutput)}
                    className="text-xs text-green-600 hover:text-green-800"
                  >
                    {showFullOutput ? "▲ Less" : "▼ More"}
                  </button>
                )}
              </div>

              <div className="bg-green-50 rounded p-3 border border-green-200" style={{ overflowY: "auto", maxHeight: showFullOutput ? "none" : "300px" }}>
                <div className="text-xs text-green-800 leading-relaxed">
                  <Markdown>{showFullOutput ? output : truncateOutput(output)}</Markdown>
                </div>
              </div>
            </div>
          )}

          {reflectionText && (
            <div className="mb-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-gray-600">Code Reflection ({reflectionText.length} chars)</span>
                {reflectionText.length > 400 && (
                  <button
                    onClick={() => setShowFullReflection(!showFullReflection)}
                    className="text-xs text-blue-600 hover:text-blue-800"
                  >
                    {showFullReflection ? "▲ Less" : "▼ More"}
                  </button>
                )}
              </div>

              <div className="bg-blue-50 rounded p-3 border border-blue-200" style={{ overflowY: "auto", maxHeight: showFullReflection ? "none" : "300px" }}>
                <div className="text-xs text-blue-800 leading-relaxed">
                  <Markdown>{showFullReflection ? reflectionText : truncateOutput(reflectionText)}</Markdown>
                </div>
              </div>
            </div>
          )}

          {/* Variables Section - Only show if we have variables */}
          {hasVariables && (
            <div className="mb-3">
              <div className="mb-2">
                <span className="text-xs text-gray-600">Variables Created ({Object.keys(variables).length})</span>
              </div>
              <div className="bg-blue-50 rounded p-3 border border-blue-200">
                <div className="text-xs text-blue-800 space-y-1">
                  {Object.entries(variables).slice(0, 3).map(([key, value]: [string, any]) => (
                    <div key={key} className="font-mono">
                      <span className="font-semibold">{key}</span>: {JSON.stringify(value).substring(0, 60)}{JSON.stringify(value).length > 60 ? '...' : ''}
                    </div>
                  ))}
                  {Object.keys(variables).length > 3 && (
                    <div className="text-gray-500 italic">+ {Object.keys(variables).length - 3} more...</div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Quick Stats */}
          <div className="flex gap-3 text-xs text-gray-500">
            {code && <span>📊 {codeLines} lines</span>}
            {output && <span>📝 {outputLength} chars</span>}
            {hasVariables && <span>🔢 {Object.keys(variables).length} vars</span>}
            <span>🎯 {output ? "Complete" : "In Progress"}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
