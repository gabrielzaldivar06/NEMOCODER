/**
 * usePlanState Hook
 * Manages objective, current plan, and active step state
 */

import { useState, useCallback, useRef } from "react";
import {
  ObjectiveState,
  ExecutionPlan,
  PlanStep,
  PlanExecutionLog,
} from "../services/planNemoClient";

export function usePlanState() {
  const [objective, setObjective] = useState<ObjectiveState | null>(null);
  const [currentPlan, setCurrentPlan] = useState<ExecutionPlan | null>(null);
  const [activeStepId, setActiveStepId] = useState<string | null>(null);
  const [previousPlans, setPreviousPlans] = useState<ExecutionPlan[]>([]);
  const [executionLogs, setExecutionLogs] = useState<PlanExecutionLog[]>([]);
  const [planHistoryVisible, setPlanHistoryVisible] = useState(false);

  const executionLogsRef = useRef<PlanExecutionLog[]>([]);

  // Create new objective
  const createObjective = useCallback(
    (title: string, description: string, acceptance_criteria: string[]) => {
      const objective_id = `obj_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      const now = new Date().toISOString();

      const newObjective: ObjectiveState = {
        objective_id,
        title,
        description,
        acceptance_criteria,
        status: "planning",
        created_at: now,
        updated_at: now,
      };

      setObjective(newObjective);
      return newObjective;
    },
    []
  );

  // Create new execution plan
  const createPlan = useCallback(
    (
      objective_id: string,
      steps: PlanStep[],
      reasoning: string
    ): ExecutionPlan => {
      const plan_id = `plan_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      const now = new Date().toISOString();

      const newPlan: ExecutionPlan = {
        plan_id,
        objective_id,
        version: 1,
        steps: steps.map((s, idx) => ({
          ...s,
          step_id: `step_${plan_id}_${idx}`,
          sequence: idx + 1,
        })),
        status: "draft",
        reasoning,
        created_at: now,
        updated_at: now,
      };

      setCurrentPlan(newPlan);
      if (objective) {
        setObjective({ ...objective, plan_id, status: "executing" });
      }
      setActiveStepId(newPlan.steps[0]?.step_id || null);

      return newPlan;
    },
    [objective]
  );

  // Start plan execution
  const startPlanExecution = useCallback(() => {
    if (!currentPlan) return;

    const updatedPlan = {
      ...currentPlan,
      status: "active" as const,
      updated_at: new Date().toISOString(),
    };

    setCurrentPlan(updatedPlan);

    // Mark first step as in_progress
    if (updatedPlan.steps.length > 0) {
      const firstStep = updatedPlan.steps[0];
      firstStep.status = "in_progress";
      firstStep.started_at = new Date().toISOString();

      const updatedPlanWithStep = {
        ...updatedPlan,
        steps: updatedPlan.steps,
      };
      setCurrentPlan(updatedPlanWithStep);
    }
  }, [currentPlan]);

  // Complete current step
  const completeStep = useCallback(
    (stepId: string, outcome: string, learnings: string[] = []) => {
      if (!currentPlan) return;

      const stepIndex = currentPlan.steps.findIndex((s) => s.step_id === stepId);
      if (stepIndex === -1) return;

      const updatedSteps = [...currentPlan.steps];
      const step = updatedSteps[stepIndex];

      step.status = "completed";
      step.outcome = outcome;
      step.learnings = learnings;
      step.success_criteria_met = learnings.length === 0 || outcome.includes("success");
      step.completed_at = new Date().toISOString();

      // Log the event
      const logEntry: PlanExecutionLog = {
        log_id: `log_${Date.now()}`,
        plan_id: currentPlan.plan_id,
        step_id: stepId,
        message_id: "", // Will be filled by caller
        action: "completed",
        timestamp: new Date().toISOString(),
        details: { outcome, learnings },
      };

      executionLogsRef.current.push(logEntry);
      setExecutionLogs([...executionLogsRef.current]);

      // Advance to next step if available
      if (stepIndex < updatedSteps.length - 1) {
        const nextStep = updatedSteps[stepIndex + 1];
        nextStep.status = "in_progress";
        nextStep.started_at = new Date().toISOString();
        setActiveStepId(nextStep.step_id);
      } else {
        // All steps completed
        setActiveStepId(null);
      }

      const updatedPlan = {
        ...currentPlan,
        steps: updatedSteps,
        updated_at: new Date().toISOString(),
      };

      setCurrentPlan(updatedPlan);
    },
    [currentPlan]
  );

  // Fail current step
  const failStep = useCallback(
    (stepId: string, reason: string) => {
      if (!currentPlan) return;

      const stepIndex = currentPlan.steps.findIndex((s) => s.step_id === stepId);
      if (stepIndex === -1) return;

      const updatedSteps = [...currentPlan.steps];
      const step = updatedSteps[stepIndex];

      step.status = "failed";
      step.outcome = `Failed: ${reason}`;
      step.success_criteria_met = false;
      step.completed_at = new Date().toISOString();

      const updatedPlan = {
        ...currentPlan,
        steps: updatedSteps,
        updated_at: new Date().toISOString(),
      };

      setCurrentPlan(updatedPlan);

      // Log the failure
      const logEntry: PlanExecutionLog = {
        log_id: `log_${Date.now()}`,
        plan_id: currentPlan.plan_id,
        step_id: stepId,
        message_id: "",
        action: "failed",
        timestamp: new Date().toISOString(),
        details: { reason },
      };

      executionLogsRef.current.push(logEntry);
      setExecutionLogs([...executionLogsRef.current]);
    },
    [currentPlan]
  );

  // Skip step
  const skipStep = useCallback(
    (stepId: string, reason: string = "") => {
      if (!currentPlan) return;

      const stepIndex = currentPlan.steps.findIndex((s) => s.step_id === stepId);
      if (stepIndex === -1) return;

      const updatedSteps = [...currentPlan.steps];
      const step = updatedSteps[stepIndex];

      step.status = "skipped";
      step.outcome = `Skipped: ${reason}`;
      step.completed_at = new Date().toISOString();

      // Move to next step
      if (stepIndex < updatedSteps.length - 1) {
        const nextStep = updatedSteps[stepIndex + 1];
        nextStep.status = "in_progress";
        nextStep.started_at = new Date().toISOString();
        setActiveStepId(nextStep.step_id);
      } else {
        setActiveStepId(null);
      }

      const updatedPlan = {
        ...currentPlan,
        steps: updatedSteps,
        updated_at: new Date().toISOString(),
      };

      setCurrentPlan(updatedPlan);
    },
    [currentPlan]
  );

  // Complete entire plan
  const completePlan = useCallback(() => {
    if (!currentPlan || !objective) return;

    const updatedPlan = {
      ...currentPlan,
      status: "completed" as const,
      updated_at: new Date().toISOString(),
    };

    setCurrentPlan(updatedPlan);
    setObjective({ ...objective, status: "completed" });

    return updatedPlan;
  }, [currentPlan, objective]);

  // Archive plan
  const archivePlan = useCallback(() => {
    if (!currentPlan) return;

    const updatedPlan = {
      ...currentPlan,
      status: "archived" as const,
      updated_at: new Date().toISOString(),
    };

    setPreviousPlans([...previousPlans, updatedPlan]);
    setCurrentPlan(null);
    setActiveStepId(null);
  }, [currentPlan, previousPlans]);

  // Get current active step
  const getActiveStep = useCallback((): PlanStep | null => {
    if (!currentPlan || !activeStepId) return null;
    return currentPlan.steps.find((s) => s.step_id === activeStepId) || null;
  }, [currentPlan, activeStepId]);

  // Get plan progress percentage
  const getPlanProgress = useCallback((): number => {
    if (!currentPlan || currentPlan.steps.length === 0) return 0;

    const completedSteps = currentPlan.steps.filter(
      (s) => s.status === "completed" || s.status === "skipped"
    ).length;

    return Math.round((completedSteps / currentPlan.steps.length) * 100);
  }, [currentPlan]);

  // Load plan from localStorage
  const loadFromLocalStorage = useCallback(() => {
    try {
      const objStr = localStorage.getItem("mission_control_objective_v1");
      const planStr = localStorage.getItem("mission_control_plan_v1");

      if (objStr) {
        setObjective(JSON.parse(objStr));
      }

      if (planStr) {
        const plan = JSON.parse(planStr);
        setCurrentPlan(plan);

        // Set active step to first pending or in_progress
        const activeStep =
          plan.steps.find((s: PlanStep) => s.status === "in_progress") ||
          plan.steps.find((s: PlanStep) => s.status === "pending");

        if (activeStep) {
          setActiveStepId(activeStep.step_id);
        }
      }
    } catch (error) {
      console.error("[usePlanState] Failed to load from localStorage:", error);
    }
  }, []);

  // Save plan to localStorage
  const saveToLocalStorage = useCallback(() => {
    try {
      if (objective) {
        localStorage.setItem(
          "mission_control_objective_v1",
          JSON.stringify(objective)
        );
      }

      if (currentPlan) {
        localStorage.setItem(
          "mission_control_plan_v1",
          JSON.stringify(currentPlan)
        );
      }
    } catch (error) {
      console.error("[usePlanState] Failed to save to localStorage:", error);
    }
  }, [objective, currentPlan]);

  // Clear plan state
  const clearPlanState = useCallback(() => {
    setObjective(null);
    setCurrentPlan(null);
    setActiveStepId(null);
    setExecutionLogs([]);
    executionLogsRef.current = [];
    localStorage.removeItem("mission_control_objective_v1");
    localStorage.removeItem("mission_control_plan_v1");
  }, []);

  return {
    // State
    objective,
    currentPlan,
    activeStepId,
    activeStep: getActiveStep(),
    previousPlans,
    executionLogs,
    planHistoryVisible,
    planProgress: getPlanProgress(),

    // Setters
    setObjective,
    setActiveStepId,
    setPlanHistoryVisible,

    // Actions
    createObjective,
    createPlan,
    startPlanExecution,
    completeStep,
    failStep,
    skipStep,
    completePlan,
    archivePlan,

    // Persistence
    loadFromLocalStorage,
    saveToLocalStorage,
    clearPlanState,
  };
}
