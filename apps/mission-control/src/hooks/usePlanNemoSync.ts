/**
 * usePlanNemoSync Hook
 * Handles bidirectional sync between localStorage and NEMO persistence
 */

import { useEffect, useRef, useCallback } from "react";
import {
  planNemoClient,
  ObjectiveState,
  ExecutionPlan,
  PlanStep,
  PlanSummary,
} from "../services/planNemoClient";

export interface PlanNemoSyncOptions {
  nemoEnabled?: boolean;
  autoSync?: boolean;
  syncInterval?: number; // milliseconds
  onSyncError?: (error: Error) => void;
  onSyncSuccess?: () => void;
}

export function usePlanNemoSync(
  objective: ObjectiveState | null,
  currentPlan: ExecutionPlan | null,
  activeStepId: string | null,
  options: PlanNemoSyncOptions = {}
) {
  const {
    nemoEnabled = true,
    autoSync = true,
    syncInterval = 30000, // 30 seconds
    onSyncError,
    onSyncSuccess,
  } = options;

  const syncTimeoutRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const lastSyncRef = useRef<{
    objectiveId?: string;
    planId?: string;
    stepId?: string;
  }>({});

  // Sync objective to NEMO
  const syncObjective = useCallback(async (): Promise<boolean> => {
    if (!objective || !nemoEnabled) return false;

    try {
      // Skip if unchanged
      if (lastSyncRef.current.objectiveId === objective.objective_id) {
        return true;
      }

      await planNemoClient.syncObjectiveToNemo(objective);
      lastSyncRef.current.objectiveId = objective.objective_id;

      onSyncSuccess?.();
      return true;
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error));
      console.error("[usePlanNemoSync] Failed to sync objective:", err);
      onSyncError?.(err);
      return false;
    }
  }, [objective, nemoEnabled, onSyncError, onSyncSuccess]);

  // Sync current plan to NEMO
  const syncPlan = useCallback(async (): Promise<boolean> => {
    if (!currentPlan || !nemoEnabled) return false;

    try {
      // Skip if unchanged
      if (lastSyncRef.current.planId === currentPlan.plan_id) {
        return true;
      }

      await planNemoClient.syncPlanToNemo(currentPlan);
      lastSyncRef.current.planId = currentPlan.plan_id;

      onSyncSuccess?.();
      return true;
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error));
      console.error("[usePlanNemoSync] Failed to sync plan:", err);
      onSyncError?.(err);
      return false;
    }
  }, [currentPlan, nemoEnabled, onSyncError, onSyncSuccess]);

  // Sync active step outcome to NEMO
  const syncActiveStepOutcome = useCallback(async (): Promise<string | null> => {
    if (!currentPlan || !activeStepId || !nemoEnabled) return null;

    try {
      const step = currentPlan.steps.find((s) => s.step_id === activeStepId);
      if (!step) return null;

      // Only sync completed steps
      if (step.status !== "completed" && step.status !== "failed") {
        return null;
      }

      const handle = await planNemoClient.syncStepOutcomeToNemo(currentPlan, step);
      onSyncSuccess?.();
      return handle;
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error));
      console.error("[usePlanNemoSync] Failed to sync step outcome:", err);
      onSyncError?.(err);
      return null;
    }
  }, [currentPlan, activeStepId, nemoEnabled, onSyncError, onSyncSuccess]);

  // Sync plan completion summary
  const syncPlanSummary = useCallback(async (summary: PlanSummary): Promise<boolean> => {
    if (!nemoEnabled) return false;

    try {
      await planNemoClient.syncPlanSummaryToNemo(summary);
      onSyncSuccess?.();
      return true;
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error));
      console.error("[usePlanNemoSync] Failed to sync plan summary:", err);
      onSyncError?.(err);
      return false;
    }
  }, [nemoEnabled, onSyncError, onSyncSuccess]);

  // Generate context for agent injection
  const generateAgentContext = useCallback(async (): Promise<string> => {
    if (!currentPlan || !nemoEnabled) return "";

    try {
      const context = await planNemoClient.buildPlanContextPortfolio(
        currentPlan.plan_id,
        2000
      );
      return context;
    } catch (error) {
      console.error("[usePlanNemoSync] Failed to generate agent context:", error);
      return "";
    }
  }, [currentPlan, nemoEnabled]);

  // Retrieve previous plans and learnings for continuity
  const getContextContinuity = useCallback(async (): Promise<{
    previousPlans: string[];
    learnings: string[];
  }> => {
    if (!nemoEnabled) return { previousPlans: [], learnings: [] };

    try {
      const [prevPlans, prevLearnings] = await Promise.all([
        planNemoClient.retrievePreviousPlans(3),
        planNemoClient.retrievePreviousLearnings("cli tools"),
      ]);

      const planSummaries = prevPlans.map(
        (p) =>
          `Plan "${p.objective_id}": ${p.steps.length} steps, status: ${p.status}`
      );

      return {
        previousPlans: planSummaries,
        learnings: prevLearnings,
      };
    } catch (error) {
      console.error("[usePlanNemoSync] Failed to get context continuity:", error);
      return { previousPlans: [], learnings: [] };
    }
  }, [nemoEnabled]);

  // Process retry queue periodically
  const processRetries = useCallback(async () => {
    if (!nemoEnabled) return;

    try {
      await planNemoClient.processRetryQueue();
    } catch (error) {
      console.error("[usePlanNemoSync] Failed to process retry queue:", error);
    }
  }, [nemoEnabled]);

  // Auto-sync on objective/plan changes
  useEffect(() => {
    if (!autoSync) return;

    // Sync objective and plan
    (async () => {
      await syncObjective();
      await syncPlan();
    })();
  }, [objective, currentPlan, autoSync, syncObjective, syncPlan]);

  // Periodic auto-sync + retry queue processing
  useEffect(() => {
    if (!autoSync) return;

    syncTimeoutRef.current = setInterval(() => {
      syncObjective();
      syncPlan();
      processRetries();
    }, syncInterval);

    return () => {
      if (syncTimeoutRef.current) {
        clearInterval(syncTimeoutRef.current);
      }
    };
  }, [autoSync, syncInterval, syncObjective, syncPlan, processRetries]);

  // Manual sync all
  const syncAll = useCallback(async (): Promise<boolean> => {
    const results = await Promise.all([
      syncObjective(),
      syncPlan(),
      syncActiveStepOutcome(),
      processRetries(),
    ]);

    return results.every((r) => r !== false);
  }, [syncObjective, syncPlan, syncActiveStepOutcome, processRetries]);

  return {
    syncObjective,
    syncPlan,
    syncActiveStepOutcome,
    syncPlanSummary,
    syncAll,
    generateAgentContext,
    getContextContinuity,
    processRetries,
  };
}
