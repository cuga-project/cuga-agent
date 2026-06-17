import React from "react";

type TodoItem = {
  text: string;
  status: string;
};

function getStatusIcon(status: string) {
  const normalized = status.toLowerCase();
  if (normalized === "completed") return "✅";
  if (normalized === "in_progress" || normalized === "in-progress") return "🔄";
  return "⏳";
}

function getStatusColor(status: string) {
  const normalized = status.toLowerCase();
  if (normalized === "completed") return "bg-green-100 text-green-700";
  if (normalized === "in_progress" || normalized === "in-progress") return "bg-blue-100 text-blue-700";
  return "bg-gray-100 text-gray-700";
}

export default function TaskTodosComponent({ todosData }: { todosData: { todos?: TodoItem[] } }) {
  const todos = todosData?.todos || [];
  if (!todos.length) return null;

  const completed = todos.filter((item) => item.status?.toLowerCase() === "completed").length;
  const progressPercentage = (completed / todos.length) * 100;

  return (
    <div className="p-3">
      <div className="max-w-3xl mx-auto">
        <div className="bg-white rounded-lg border border-gray-200 p-3">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-gray-700 flex items-center gap-2">
              <span className="text-sm">📋</span>
              Current Plan
            </h3>
            <span className="px-2 py-1 rounded text-xs bg-indigo-100 text-indigo-700">
              {completed}/{todos.length} done
            </span>
          </div>

          <div className="mb-3">
            <div className="flex-1 bg-gray-200 rounded-full h-1.5">
              <div
                className="bg-indigo-500 h-1.5 rounded-full transition-all duration-300"
                style={{ width: `${progressPercentage}%` }}
              />
            </div>
          </div>

          <div className="space-y-2">
            {todos.map((item, index) => (
              <div
                key={`${item.text}-${index}`}
                className="flex items-start gap-2 p-2 bg-gray-50 rounded border border-gray-100"
              >
                <span className="text-sm mt-0.5">{getStatusIcon(item.status)}</span>
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-gray-700 leading-relaxed">{item.text}</p>
                </div>
                <span className={`px-1.5 py-0.5 rounded text-xs ${getStatusColor(item.status)}`}>
                  {item.status}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
