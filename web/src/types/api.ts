/* Generated from api/app by 'make types'. Do not edit. */

export type ArtifactKind =
  "requirements" | "design" | "dev_result" | "integration" | "review" | "test_report" | "release_plan";
export type RunEvent =
  | RunCreatedEvent
  | RunStatusChangedEvent
  | RunCompletedEvent
  | RunFailedEvent
  | RunAbortedEvent
  | RunInterruptedEvent
  | PhaseStartedEvent
  | PhaseCompletedEvent
  | AgentStartedEvent
  | AgentFinishedEvent
  | AgentTextEvent
  | AgentThinkingEvent
  | AgentToolCallEvent
  | AgentToolResultEvent
  | AgentUsageEvent
  | HandoffEvent
  | ArtifactCreatedEvent
  | ArtifactRevisedEvent
  | GateOpenedEvent
  | GateResolvedEvent
  | LoopIterationEvent
  | GuardrailBlockedEvent
  | BudgetUpdatedEvent
  | MergeStartedEvent
  | MergeConflictEvent
  | MergeCompletedEvent
  | GitPushedEvent
  | PrOpenedEvent
  | PrReadyEvent
  | CiStatusEvent;
export type Phase = "init" | "ba" | "architect" | "dev" | "integrate" | "review" | "qa" | "devops" | "done";
export type RunStatus = "pending" | "running" | "waiting_gate" | "completed" | "failed" | "aborted" | "interrupted";
/**
 * Durable points a run resumes from, in run order (stored on the run, not derived).
 */
export type Checkpoint =
  | "cloned"
  | "requirements_drafted"
  | "requirements_approved"
  | "design_drafted"
  | "design_approved"
  | "pushed"
  | "pr_opened"
  | "done";
export type Role = "ba" | "architect" | "dev" | "integrator" | "reviewer" | "qa" | "devops" | "guardrails";
export type GateKind = "requirements" | "design" | "release" | "escalation";
export type RunStatus1 = "pending" | "running" | "waiting_gate" | "completed" | "failed" | "aborted" | "interrupted";

export interface Api {
  artifact: ArtifactRecord;
  event: RunEvent;
  run: RunRecord;
}
export interface ArtifactRecord {
  created_at: string;
  data: {
    [k: string]: unknown;
  };
  edited_by_user: boolean;
  id: string;
  kind: ArtifactKind;
  markdown: string;
  run_id: string;
  title: string;
  version: number;
  [k: string]: unknown;
}
export interface RunCreatedEvent {
  agent_id: string | null;
  payload: RunCreatedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "run.created";
}
export interface RunCreatedPayload {
  base_branch: string;
  budget_usd: number;
  max_parallel_devs: number;
  repo: string;
  requirement: string;
}
export interface RunStatusChangedEvent {
  agent_id: string | null;
  payload: RunStatusChangedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "run.status_changed";
}
export interface RunStatusChangedPayload {
  status: RunStatus;
}
export interface RunCompletedEvent {
  agent_id: string | null;
  payload: RunCompletedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "run.completed";
}
export interface RunCompletedPayload {
  pr_url: string | null;
}
export interface RunFailedEvent {
  agent_id: string | null;
  payload: RunFailedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "run.failed";
}
export interface RunFailedPayload {
  detail: string | null;
  reason: string;
}
export interface RunAbortedEvent {
  agent_id: string | null;
  payload: RunAbortedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "run.aborted";
}
export interface RunAbortedPayload {
  by: "user" | "system";
  reason: string | null;
}
export interface RunInterruptedEvent {
  agent_id: string | null;
  payload: RunInterruptedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "run.interrupted";
}
export interface RunInterruptedPayload {
  last_checkpoint: Checkpoint | null;
}
export interface PhaseStartedEvent {
  agent_id: string | null;
  payload: PhaseStartedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "phase.started";
}
export interface PhaseStartedPayload {
  attempt: number;
}
export interface PhaseCompletedEvent {
  agent_id: string | null;
  payload: PhaseCompletedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "phase.completed";
}
export interface PhaseCompletedPayload {
  attempt: number;
  outcome: "ok" | "escalated" | "failed";
}
export interface AgentStartedEvent {
  agent_id: string | null;
  payload: AgentStartedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "agent.started";
}
export interface AgentStartedPayload {
  model: string;
  role: Role;
  task_id: string | null;
}
export interface AgentFinishedEvent {
  agent_id: string | null;
  payload: AgentFinishedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "agent.finished";
}
export interface AgentFinishedPayload {
  duration_ms: number;
  num_turns: number;
  ok: boolean;
}
export interface AgentTextEvent {
  agent_id: string | null;
  payload: AgentTextPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "agent.text";
}
export interface AgentTextPayload {
  text: string;
}
export interface AgentThinkingEvent {
  agent_id: string | null;
  payload: AgentThinkingPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "agent.thinking";
}
export interface AgentThinkingPayload {
  text: string;
}
export interface AgentToolCallEvent {
  agent_id: string | null;
  payload: AgentToolCallPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "agent.tool_call";
}
export interface AgentToolCallPayload {
  input: {
    [k: string]: unknown;
  };
  input_truncated: boolean;
  name: string;
  tool_use_id: string;
}
export interface AgentToolResultEvent {
  agent_id: string | null;
  payload: AgentToolResultPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "agent.tool_result";
}
export interface AgentToolResultPayload {
  content: string;
  is_error: boolean;
  original_size: number | null;
  tool_use_id: string;
  truncated: boolean;
}
export interface AgentUsageEvent {
  agent_id: string | null;
  payload: AgentUsagePayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "agent.usage";
}
export interface AgentUsagePayload {
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  context_max: number | null;
  context_tokens: number | null;
  cost_usd: number;
  input_tokens: number;
  model: string;
  output_tokens: number;
}
export interface HandoffEvent {
  agent_id: string | null;
  payload: HandoffPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "handoff";
}
export interface HandoffPayload {
  artifact_id: string | null;
  from_agent: string;
  summary: string;
  to_agent: string;
}
export interface ArtifactCreatedEvent {
  agent_id: string | null;
  payload: ArtifactPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "artifact.created";
}
export interface ArtifactPayload {
  artifact_id: string;
  kind: ArtifactKind;
  title: string;
  version: number;
}
export interface ArtifactRevisedEvent {
  agent_id: string | null;
  payload: ArtifactPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "artifact.revised";
}
export interface GateOpenedEvent {
  agent_id: string | null;
  payload: GateOpenedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "gate.opened";
}
export interface GateOpenedPayload {
  /**
   * @minItems 1
   */
  actions: ["approve" | "revise" | "edit" | "abort", ...("approve" | "revise" | "edit" | "abort")[]];
  artifact_id: string | null;
  gate: GateKind;
  gate_id: string;
  reason: string | null;
}
export interface GateResolvedEvent {
  agent_id: string | null;
  payload: GateResolvedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "gate.resolved";
}
export interface GateResolvedPayload {
  action: "approve" | "revise" | "edit" | "abort";
  feedback: string | null;
  gate_id: string;
}
export interface LoopIterationEvent {
  agent_id: string | null;
  payload: LoopIterationPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "loop.iteration";
}
export interface LoopIterationPayload {
  iteration: number;
  limit: number | null;
  loop: "review" | "qa" | "revise";
}
export interface GuardrailBlockedEvent {
  agent_id: string | null;
  payload: GuardrailBlockedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "guardrail.blocked";
}
export interface GuardrailBlockedPayload {
  detail: string;
  rule: string;
  tool: string;
}
export interface BudgetUpdatedEvent {
  agent_id: string | null;
  payload: BudgetUpdatedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "budget.updated";
}
export interface BudgetUpdatedPayload {
  budget_usd: number;
  spent_usd: number;
}
export interface MergeStartedEvent {
  agent_id: string | null;
  payload: MergeStartedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "merge.started";
}
export interface MergeStartedPayload {
  branch: string;
  into: string;
}
export interface MergeConflictEvent {
  agent_id: string | null;
  payload: MergeConflictPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "merge.conflict";
}
export interface MergeConflictPayload {
  branch: string;
  files: string[];
}
export interface MergeCompletedEvent {
  agent_id: string | null;
  payload: MergeCompletedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "merge.completed";
}
export interface MergeCompletedPayload {
  branch: string;
  commit: string;
  into: string;
}
export interface GitPushedEvent {
  agent_id: string | null;
  payload: GitPushedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "git.pushed";
}
export interface GitPushedPayload {
  branch: string;
  commit: string;
}
export interface PrOpenedEvent {
  agent_id: string | null;
  payload: PrOpenedPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "pr.opened";
}
export interface PrOpenedPayload {
  draft: boolean;
  number: number;
  url: string;
}
export interface PrReadyEvent {
  agent_id: string | null;
  payload: PrReadyPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "pr.ready";
}
export interface PrReadyPayload {
  number: number;
  url: string;
}
export interface CiStatusEvent {
  agent_id: string | null;
  payload: CiStatusPayload;
  phase: Phase | null;
  run_id: string;
  seq: number;
  ts: string;
  type: "ci.status";
}
export interface CiStatusPayload {
  state: "pending" | "success" | "failure" | "neutral";
  summary: string;
}
export interface RunRecord {
  base_branch: string;
  budget_usd: number;
  checkpoint: Checkpoint | null;
  checkpoint_data: {
    [k: string]: unknown;
  };
  created_at: string;
  id: string;
  last_seq: number;
  max_parallel_devs: number;
  mode: "live" | "scripted" | "demo";
  phase: Phase | null;
  pr_url: string | null;
  repo: string;
  requirement: string;
  spent_usd: number;
  status: RunStatus1;
  tokens: TokenTotals;
  updated_at: string;
  [k: string]: unknown;
}
export interface TokenTotals {
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  input_tokens: number;
  output_tokens: number;
  [k: string]: unknown;
}
