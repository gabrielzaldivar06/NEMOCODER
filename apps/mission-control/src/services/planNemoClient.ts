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
  private retryQueue: Array<{ fn: () => Promise<any>; retries: number }> = [];

  constructor() {}

  private async callNemoTool<T = Record<string, unknown>>(
    toolName: string,
    argumentsPayload: Record<string, unknown>,
    lifecyclePhase: "start" | "plan" | "build" | "review" | "close" = "plan"
  ): Promise<T> {
    const response = await fetch("/api/nemo/tool", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tool_name: toolName,
        lifecycle_phase: lifecyclePhase,
        arguments: argumentsPayload,
      }),
    });

    if (!response.ok) {
      throw new Error(`NEMO tool ${toolName} failed: ${response.statusText}`);
    }

    const data = await response.json();
    return (data.result || {}) as T;
  }

  private queueRetry(fn: () => Promise<any>): void {
    this.retryQueue.push({ fn, retries: 0 });
  }

  /**
   * Sync objective to NEMO as a memory atom
   */
  async syncObjectiveToNemo(objective: ObjectiveState): Promise<void> {
    try {
      await this.callNemoTool(
        "cognitive_ingest",
        {
          content: JSON.stringify({
            objective_id: objective.objective_id,
            title: objective.title,
            description: objective.description,
            acceptance_criteria: objective.acceptance_criteria,
            status: objective.status,
            plan_id: objective.plan_id,
            created_at: objective.created_at,
            updated_at: objective.updated_at,
          }),
          memory_type: "objective",
          tags: ["spacecode", "planning", "long-term-goal", `objective:${objective.objective_id}`],
          context: "Spacecode Mission Control objective sync",
        },
        "review"
      );
    } catch (error) {
      console.error("[PlanNemoClient] syncObjectiveToNemo failed:", error);
      this.queueRetry(() => this.syncObjectiveToNemo(objective));
    }
  }

  /**
   * Sync execution plan to NEMO
   */
  async syncPlanToNemo(plan: ExecutionPlan): Promise<void> {
    try {
      const stepsSummary = plan.steps.map((step) => `${step.sequence}. ${step.title}`).join(" -> ");
      await this.callNemoTool(
        "cognitive_ingest",
        {
          content: JSON.stringify({
            plan_id: plan.plan_id,
            objective_id: plan.objective_id,
            version: plan.version,
            steps_summary: stepsSummary,
            total_steps: plan.steps.length,
            completed_steps: plan.steps.filter((step) => step.status === "completed").length,
            status: plan.status,
            reasoning: plan.reasoning,
            created_at: plan.created_at,
            updated_at: plan.updated_at,
          }),
          memory_type: "plan",
          tags: ["spacecode", "planning", "executable", `plan:${plan.plan_id}`, `objective:${plan.objective_id}`],
          context: "Spacecode Mission Control execution plan sync",
        },
        "review"
      );
    } catch (error) {
      console.error("[PlanNemoClient] syncPlanToNemo failed:", error);
      this.queueRetry(() => this.syncPlanToNemo(plan));
    }
  }

  /**
   * Sync step outcome to NEMO as decision/learning
   */
  async syncStepOutcomeToNemo(
    plan: ExecutionPlan,
    step: PlanStep
  ): Promise<string | null> {
    const evidenceHandle = `spacecode_step_outcome_${plan.plan_id}_${step.step_id}`;
    try {
      await this.callNemoTool(
        "cognitive_ingest",
        {
          content: JSON.stringify({
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
            evidence_handle: evidenceHandle,
            created_at: new Date().toISOString(),
          }),
          memory_type: step.learnings.length > 0 ? "learning" : "decision",
          tags: ["spacecode", "planning", "step-outcome", `step:${step.step_id}`, `plan:${plan.plan_id}`, `objective:${plan.objective_id}`],
          context: "Spacecode Mission Control step outcome sync",
        },
        "review"
      );
      return evidenceHandle;
    } catch (error) {
      console.error("[PlanNemoClient] syncStepOutcomeToNemo failed:", error);
      this.queueRetry(() => this.syncStepOutcomeToNemo(plan, step));
      return null;
    }
  }

  /**
   * Sync plan completion summary to NEMO as learning
   */
  async syncPlanSummaryToNemo(summary: PlanSummary): Promise<void> {
    try {
      await this.callNemoTool(
        "cognitive_ingest",
        {
          content: JSON.stringify(summary),
          memory_type: "learning",
          tags: ["spacecode", "planning", "completion", "summary", `objective:${summary.objective_id}`, `plan:${summary.plan_id}`],
          context: "Spacecode Mission Control plan completion summary",
        },
        "review"
      );
    } catch (error) {
      console.error("[PlanNemoClient] syncPlanSummaryToNemo failed:", error);
      this.queueRetry(() => this.syncPlanSummaryToNemo(summary));
    }
  }

  /**
   * Retrieve previous plans from NEMO for context
   */
  async retrievePreviousPlans(limit: number = 5): Promise<ExecutionPlan[]> {
    try {
      await this.callNemoTool(
        "search_memories",
        { query: "Spacecode completed execution plans", tags_include: ["spacecode", "planning"], limit, compact: true },
        "review"
      );
    } catch (error) {
      console.error("[PlanNemoClient] retrievePreviousPlans failed:", error);
    }
    return [];
  }

  /**
   * Retrieve learnings from previous plans
   */
  async retrievePreviousLearnings(query: string = ""): Promise<string[]> {
    try {
      const result = await this.callNemoTool<{ memories?: Array<{ content?: string; text?: string }> }>(
        "search_memories",
        { query: `Spacecode plan learnings ${query}`.trim(), tags_include: ["spacecode", "planning", "learning"], limit: 10, compact: true },
        "review"
      );
      return (result.memories || []).map((memory) => String(memory.content || memory.text || "")).filter(Boolean);
    } catch (error) {
      console.error("[PlanNemoClient] retrievePreviousLearnings failed:", error);
    }
    return [];
  }

  /**
   * Get context portfolio for agent injection
   */
  async buildPlanContextPortfolio(
    planId: string,
    tokenBudget: number = 2000
  ): Promise<string> {
    try {
      const result = await this.callNemoTool<{ context?: string; portfolio?: { context?: string }; context_portfolio?: { context?: string } }>(
        "build_context_portfolio",
        {
          task: "execute Spacecode plan step",
          topic: "spacecode_mission_control_planning",
          tags_include: ["spacecode", "planning", `plan:${planId}`],
          token_budget: tokenBudget,
        },
        "plan"
      );
      return result.context || result.portfolio?.context || result.context_portfolio?.context || "";
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
      const response = await fetch("/api/nemo/mcp-status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ include_capability_probe: false, include_roundtrip_probe: false }),
      });
      return response.ok;
    } catch {
      return false;
    }
  }
}

export const planNemoClient = new PlanNemoClient();
