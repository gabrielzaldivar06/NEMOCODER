/**
 * NEMO MCP Client for Plan Persistence
 * Handles all communication with NEMO server for objectives, plans, steps, and learnings
 */

export interface ObjectiveState {
  objective_id: string;
  title: string;
  description: string;
  acceptance_criteria: string[];
  status: "planning" | "executing" | "completed" | "archived";
  created_at: string;
  updated_at: string;
  plan_id?: string;
}

export interface PlanStep {
  step_id: string;
  sequence: number;
  title: string;
  description: string;
  expected_outcome: string;
  status: "pending" | "in_progress" | "completed" | "failed" | "skipped";
  dependencies: string[];
  assigned_to: "user" | "agent";
  started_at?: string;
  completed_at?: string;
  chat_message_ids: string[];
  artifacts: string[];
  outcome: string;
  success_criteria_met: boolean;
  learnings: string[];
}

export interface ExecutionPlan {
  plan_id: string;
  objective_id: string;
  version: number;
  steps: PlanStep[];
  status: "draft" | "active" | "paused" | "completed" | "archived";
  reasoning: string;
  created_at: string;
  updated_at: string;
}

export interface PlanExecutionLog {
  log_id: string;
  plan_id: string;
  step_id: string;
  message_id: string;
  action: "started" | "completed" | "failed" | "revised" | "skipped";
  timestamp: string;
  details: Record<string, unknown>;
}

export interface PlanSummary {
  plan_id: string;
  objective_id: string;
  title: string;
  summary: string;
  success_rate: number;
  key_learnings: string[];
  failed_steps: string[];
  revisions: number;
  total_artifacts: number;
  template_reusable: boolean;
  created_at: string;
}

class PlanNemoClient {
  private nemoEndpoint: string;
  private retryQueue: Array<{ fn: () => Promise<any>; retries: number }> = [];

  constructor(nemoEndpoint: string = "http://localhost:8765") {
    this.nemoEndpoint = nemoEndpoint;
  }

  /**
   * Sync objective to NEMO as a memory atom
   */
  async syncObjectiveToNemo(objective: ObjectiveState): Promise<void> {
    try {
      const content = JSON.stringify({
        objective_id: objective.objective_id,
        title: objective.title,
        description: objective.description,
        acceptance_criteria: objective.acceptance_criteria,
        status: objective.status,
        plan_id: objective.plan_id,
        created_at: objective.created_at,
        updated_at: objective.updated_at,
      });

      const response = await fetch(`${this.nemoEndpoint}/api/memory`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          memory_type: "objective",
          importance_level: 8,
          tags: ["planning", "long-term-goal", `objective:${objective.objective_id}`],
          source_scope: "mission_control",
        }),
      });

      if (!response.ok) {
        throw new Error(`NEMO sync failed: ${response.statusText}`);
      }
    } catch (error) {
      console.error("[PlanNemoClient] syncObjectiveToNemo failed:", error);
      // Queue for retry
      this.retryQueue.push({
        fn: () => this.syncObjectiveToNemo(objective),
        retries: 0,
      });
    }
  }

  /**
   * Sync execution plan to NEMO
   */
  async syncPlanToNemo(plan: ExecutionPlan): Promise<void> {
    try {
      const stepsSummary = plan.steps
        .map((s) => `${s.sequence}. ${s.title}`)
        .join(" → ");

      const content = JSON.stringify({
        plan_id: plan.plan_id,
        objective_id: plan.objective_id,
        version: plan.version,
        steps_summary: stepsSummary,
        total_steps: plan.steps.length,
        completed_steps: plan.steps.filter((s) => s.status === "completed").length,
        status: plan.status,
        reasoning: plan.reasoning,
        created_at: plan.created_at,
        updated_at: plan.updated_at,
      });

      const importance = plan.status === "completed" ? 9 : 7;

      const response = await fetch(`${this.nemoEndpoint}/api/memory`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          memory_type: "plan",
          importance_level: importance,
          tags: [
            "planning",
            "executable",
            `plan:${plan.plan_id}`,
            `objective:${plan.objective_id}`,
          ],
          source_scope: "mission_control",
        }),
      });

      if (!response.ok) {
        throw new Error(`NEMO plan sync failed: ${response.statusText}`);
      }
    } catch (error) {
      console.error("[PlanNemoClient] syncPlanToNemo failed:", error);
      this.retryQueue.push({
        fn: () => this.syncPlanToNemo(plan),
        retries: 0,
      });
    }
  }

  /**
   * Sync step outcome to NEMO as decision/learning
   */
  async syncStepOutcomeToNemo(
    plan: ExecutionPlan,
    step: PlanStep
  ): Promise<string | null> {
    try {
      const content = JSON.stringify({
        step_id: step.step_id,
        plan_id: plan.plan_id,
        objective_id: plan.objective_id,
        title: step.title,
        expected: step.expected_outcome,
        actual: step.outcome,
        success: step.success_criteria_met,
        learnings: step.learnings,
        artifacts: step.artifacts,
        sequence: step.sequence,
        created_at: new Date().toISOString(),
      });

      // Create evidence handle for detailed step info
      const evidenceHandle = `step_outcome_${plan.plan_id}_${step.step_id}`;

      const response = await fetch(`${this.nemoEndpoint}/api/memory`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          memory_type: step.learnings.length > 0 ? "learning" : "decision",
          importance_level: step.learnings.length > 0 ? 8 : 6,
          tags: [
            "planning",
            "step-outcome",
            `step:${step.step_id}`,
            `plan:${plan.plan_id}`,
            `objective:${plan.objective_id}`,
          ],
          evidence_handle: evidenceHandle,
          source_scope: "mission_control",
        }),
      });

      if (!response.ok) {
        throw new Error(`NEMO step sync failed: ${response.statusText}`);
      }

      return evidenceHandle;
    } catch (error) {
      console.error("[PlanNemoClient] syncStepOutcomeToNemo failed:", error);
      this.retryQueue.push({
        fn: () => this.syncStepOutcomeToNemo(plan, step),
        retries: 0,
      });
      return null;
    }
  }

  /**
   * Sync plan completion summary to NEMO as learning
   */
  async syncPlanSummaryToNemo(summary: PlanSummary): Promise<void> {
    try {
      const content = JSON.stringify(summary);

      const response = await fetch(`${this.nemoEndpoint}/api/memory`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          memory_type: "learning",
          importance_level: 9,
          tags: [
            "planning",
            "completion",
            "summary",
            `objective:${summary.objective_id}`,
            `plan:${summary.plan_id}`,
          ],
          source_scope: "mission_control",
        }),
      });

      if (!response.ok) {
        throw new Error(`NEMO summary sync failed: ${response.statusText}`);
      }
    } catch (error) {
      console.error("[PlanNemoClient] syncPlanSummaryToNemo failed:", error);
      this.retryQueue.push({
        fn: () => this.syncPlanSummaryToNemo(summary),
        retries: 0,
      });
    }
  }

  /**
   * Retrieve previous plans from NEMO for context
   */
  async retrievePreviousPlans(limit: number = 5): Promise<ExecutionPlan[]> {
    try {
      const response = await fetch(
        `${this.nemoEndpoint}/api/search?query=completed plans&tags=planning&limit=${limit}`,
        {
          method: "GET",
          headers: { "Content-Type": "application/json" },
        }
      );

      if (!response.ok) {
        throw new Error("Failed to retrieve previous plans");
      }

      const data = await response.json();
      // Parse returned memories into ExecutionPlan structure
      // This is a simplified version - actual implementation depends on NEMO API
      return [];
    } catch (error) {
      console.error("[PlanNemoClient] retrievePreviousPlans failed:", error);
      return [];
    }
  }

  /**
   * Retrieve learnings from previous plans
   */
  async retrievePreviousLearnings(query: string = ""): Promise<string[]> {
    try {
      const response = await fetch(
        `${this.nemoEndpoint}/api/search?query=learnings ${query}&tags=planning,learning&limit=10`,
        {
          method: "GET",
          headers: { "Content-Type": "application/json" },
        }
      );

      if (!response.ok) {
        throw new Error("Failed to retrieve learnings");
      }

      const data = await response.json();
      // Extract learnings from memories
      return [];
    } catch (error) {
      console.error("[PlanNemoClient] retrievePreviousLearnings failed:", error);
      return [];
    }
  }

  /**
   * Get context portfolio for agent injection
   */
  async buildPlanContextPortfolio(
    planId: string,
    tokenBudget: number = 2000
  ): Promise<string> {
    try {
      const response = await fetch(`${this.nemoEndpoint}/api/context-portfolio`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task: "execute plan step",
          topic: "mission_control_planning",
          tags_include: ["planning"],
          token_budget: tokenBudget,
        }),
      });

      if (!response.ok) {
        throw new Error("Failed to build context portfolio");
      }

      const data = await response.json();
      return data.context || "";
    } catch (error) {
      console.error("[PlanNemoClient] buildPlanContextPortfolio failed:", error);
      return "";
    }
  }

  /**
   * Process retry queue for failed syncs
   */
  async processRetryQueue(): Promise<void> {
    const toRetry = [...this.retryQueue];
    this.retryQueue = [];

    for (const item of toRetry) {
      if (item.retries < 3) {
        try {
          await item.fn();
        } catch (error) {
          item.retries++;
          this.retryQueue.push(item);
        }
      }
    }
  }

  /**
   * Check if NEMO MCP is available
   */
  async checkHealth(): Promise<boolean> {
    try {
      const response = await fetch(`${this.nemoEndpoint}/api/health`, {
        method: "GET",
      });
      return response.ok;
    } catch {
      return false;
    }
  }
}

export const planNemoClient = new PlanNemoClient();
