/**
 * PlanProgress Sidebar Component
 * Shows current plan execution status, steps timeline, and progress
 */

import React from "react";
import { ChevronDown, CheckCircle2, Circle, AlertCircle, Zap, Target } from "lucide-react";
import { ExecutionPlan, PlanStep } from "../services/planNemoClient";

interface PlanProgressProps {
  objective: { title: string; description: string } | null;
  currentPlan: ExecutionPlan | null;
  activeStepId: string | null;
  planProgress: number;
  onStepClick: (stepId: string) => void;
}

export function PlanProgress({
  objective,
  currentPlan,
  activeStepId,
  planProgress,
  onStepClick,
}: PlanProgressProps) {
  if (!currentPlan || !objective) {
    return null;
  }

  const getStepIcon = (step: PlanStep) => {
    switch (step.status) {
      case "completed":
        return <CheckCircle2 size={16} className="text-green-400" />;
      case "in_progress":
        return <Zap size={16} className="text-blue-400 animate-pulse" />;
      case "failed":
        return <AlertCircle size={16} className="text-red-400" />;
      case "skipped":
        return <Circle size={16} className="text-gray-500" />;
      default:
        return <Circle size={16} className="text-gray-600" />;
    }
  };

  const getStepStatusColor = (status: string) => {
    switch (status) {
      case "completed":
        return "text-green-400";
      case "in_progress":
        return "text-blue-400";
      case "failed":
        return "text-red-400";
      case "skipped":
        return "text-gray-500";
      default:
        return "text-gray-400";
    }
  };

  return (
    <div className="plan-progress-panel bg-gray-900 border-l border-gray-700 flex flex-col max-h-[calc(100vh-100px)]">
      {/* Objective Section */}
      <div className="bg-gradient-to-r from-blue-900/30 to-blue-800/20 border-b border-gray-700 p-4">
        <div className="flex items-start gap-3">
          <Target size={20} className="text-blue-400 flex-shrink-0 mt-1" />
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-bold text-white truncate">{objective.title}</h3>
            <p className="text-xs text-gray-400 mt-1 line-clamp-2">{objective.description}</p>
          </div>
        </div>
      </div>

      {/* Progress Bar */}
      <div className="px-4 py-3 border-b border-gray-700">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold text-gray-300">Progreso</span>
          <span className="text-xs font-bold text-blue-400">{planProgress}%</span>
        </div>
        <div className="w-full bg-gray-800 rounded-full h-2 overflow-hidden">
          <div
            className="bg-gradient-to-r from-blue-500 to-cyan-400 h-full transition-all duration-300"
            style={{ width: `${planProgress}%` }}
          />
        </div>
      </div>

      {/* Plan Info */}
      <div className="px-4 py-2 border-b border-gray-700 text-xs text-gray-400">
        <p>
          <span className="text-gray-300 font-semibold">{currentPlan.steps.length}</span> pasos • v
          {currentPlan.version}
        </p>
        <p className="mt-1">
          Estado: <span className={`font-bold ${getStepStatusColor(currentPlan.status)}`}>{currentPlan.status}</span>
        </p>
      </div>

      {/* Steps Timeline */}
      <div className="flex-1 overflow-y-auto">
        <div className="p-4 space-y-2">
          {currentPlan.steps.map((step, index) => {
            const isActive = step.step_id === activeStepId;
            const isCompleted = step.status === "completed" || step.status === "skipped";

            return (
              <button
                key={step.step_id}
                onClick={() => onStepClick(step.step_id)}
                className={`w-full text-left p-3 rounded transition-all ${
                  isActive
                    ? "bg-blue-900/40 border border-blue-500 shadow-lg shadow-blue-500/20"
                    : isCompleted
                      ? "bg-gray-800/40 border border-gray-700 hover:bg-gray-800/60"
                      : "bg-gray-800/20 border border-gray-700 hover:bg-gray-800/40"
                }`}
              >
                {/* Step Header */}
                <div className="flex items-start gap-3">
                  <div className="mt-1">{getStepIcon(step)}</div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-bold text-gray-400">
                        {step.sequence.toString().padStart(2, "0")}
                      </span>
                      <h4 className="text-sm font-semibold text-white truncate">{step.title}</h4>
                    </div>

                    {/* Step Status Badge */}
                    <div className="mt-1">
                      <span
                        className={`inline-block px-2 py-0.5 text-xs font-semibold rounded ${
                          step.status === "completed"
                            ? "bg-green-900/50 text-green-300"
                            : step.status === "in_progress"
                              ? "bg-blue-900/50 text-blue-300"
                              : step.status === "failed"
                                ? "bg-red-900/50 text-red-300"
                                : step.status === "skipped"
                                  ? "bg-gray-700 text-gray-400"
                                  : "bg-gray-700 text-gray-400"
                        }`}
                      >
                        {step.status === "in_progress" ? "↻ En curso" : step.status}
                      </span>
                    </div>

                    {/* Step Description (when active) */}
                    {isActive && step.description && (
                      <p className="text-xs text-gray-400 mt-2 line-clamp-2">{step.description}</p>
                    )}

                    {/* Outcome (when completed) */}
                    {step.outcome && (
                      <p className="text-xs text-gray-500 mt-1 line-clamp-1">
                        <span className="text-gray-600">→ </span>
                        {step.outcome}
                      </p>
                    )}

                    {/* Learnings */}
                    {step.learnings.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {step.learnings.slice(0, 2).map((learning, idx) => (
                          <span
                            key={idx}
                            className="inline-block px-1.5 py-0.5 text-xs bg-amber-900/50 text-amber-300 rounded"
                          >
                            💡 {learning.slice(0, 20)}
                            {learning.length > 20 ? "..." : ""}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {/* Dependencies (if any) */}
                {step.dependencies && step.dependencies.length > 0 && (
                  <div className="mt-2 ml-6 text-xs text-gray-500">
                    <span className="text-gray-600">🔗 Depende de:</span> {step.dependencies.length} paso
                    {step.dependencies.length !== 1 ? "s" : ""}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Plan Reasoning Footer */}
      {currentPlan.reasoning && (
        <div className="border-t border-gray-700 p-4 bg-gray-800/30">
          <p className="text-xs font-semibold text-gray-400 mb-2">🧠 Razonamiento del Plan:</p>
          <p className="text-xs text-gray-500 line-clamp-3">{currentPlan.reasoning}</p>
        </div>
      )}
    </div>
  );
}
