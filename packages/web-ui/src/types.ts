export type RunStatus = 'success' | 'error' | 'cancelled' | 'timeout' | string;

export type StepType =
  | 'model_call'
  | 'tool_call'
  | 'retrieval'
  | 'memory'
  | 'planner'
  | 'control'
  | 'custom'
  | string;

export interface Cost {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  amount_usd: number;
}

export interface Error {
  type: string;
  message: string;
  stack: string;
}

export interface ToolCall {
  name: string;
  args: unknown;
  result: unknown;
}

export interface Step {
  id: string;
  parent_id: string | null;
  type: StepType;
  name: string;
  status: RunStatus;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  model: string;
  input: unknown;
  output: unknown;
  tool: ToolCall | null;
  cost: Cost | null;
  error: Error | null;
  events: unknown[];
  replayable: boolean;
  metadata: Record<string, unknown>;
}

export interface Run {
  id: string;
  task: string;
  status: RunStatus;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  labels: Record<string, string>;
  final_output: unknown;
  cost: Cost | null;
}

export interface Trace {
  schema_version: string;
  run: Run;
  steps: Step[];
}

export interface ModelCostSummary {
  model: string;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  amountUsd: number;
}

export interface CostSummary {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  amountUsd: number;
  source: 'steps' | 'run' | 'none';
  byModel: ModelCostSummary[];
  expensiveSteps: Step[];
}

export interface TimelineItem {
  id: string;
  index: number;
  kind: 'model' | 'tool' | 'planner' | 'retrieval' | 'memory' | 'control' | 'custom';
  label: string;
  status: RunStatus;
  durationMs: number | null;
  tokenCount: number;
  costUsd: number;
  isFailure: boolean;
  parentId: string | null;
  step: Step;
}

export interface DiffRow {
  key: string;
  change: 'unchanged' | 'changed' | 'added' | 'removed';
  left: Step | null;
  right: Step | null;
  statusChanged: boolean;
  durationDeltaMs: number;
  tokenDelta: number;
  costDeltaUsd: number;
}

export interface RunDiff {
  leftRunId: string;
  rightRunId: string;
  statusChanged: boolean;
  stepCountDelta: number;
  durationDeltaMs: number;
  tokenDelta: number;
  costDeltaUsd: number;
  rows: DiffRow[];
}

export type ExperimentWinner = 'A' | 'B' | 'tie';

export interface ExperimentArm {
  label: 'A' | 'B';
  traceId: string;
  status: RunStatus;
  stepCount: number;
  failedSteps: number;
  durationMs: number;
  totalTokens: number;
  costUsd: number;
  finalOutput: unknown;
}

export interface ExperimentDelta {
  tokenDelta: number;
  costDeltaUsd: number;
  latencyDeltaMs: number;
  stepCountDelta: number;
  outputChanged: boolean;
}

export interface ExperimentReport {
  left: ExperimentArm;
  right: ExperimentArm;
  delta: ExperimentDelta;
  winnerBySuccess: ExperimentWinner;
  winnerByCost: ExperimentWinner;
  winnerByLatency: ExperimentWinner;
  recommendation: ExperimentWinner;
  recommendationReason: string;
}

export interface ReplayToolMock {
  stepId: string;
  name: string;
  args: unknown;
  result: unknown;
}

export interface ReplayPlan {
  runId: string;
  task: string;
  startStep: Step | null;
  stepsToReplay: Step[];
  mockedTools: ReplayToolMock[];
}

export interface ReplayComparisonDelta {
  tokenDelta: number;
  costDeltaUsd: number;
  latencyDeltaMs: number;
  stepCountDelta: number;
}

export interface ReplayStepChange {
  index: number;
  kind: 'shape_changed' | 'status_changed' | 'output_changed' | 'cost_changed' | 'missing_replay_step' | 'extra_replay_step';
  sourceStepId: string | null;
  replayStepId: string | null;
  detail: string;
}

export interface ReplayComparisonReport {
  sourceRunId: string;
  replayRunId: string;
  replaySourceRunId: string;
  sourceRunMatch: boolean;
  sourceStartStepId: string;
  replayMode: string;
  sourceStatus: string;
  replayStatus: string;
  sourceStepCount: number;
  replayStepCount: number;
  sourceDurationMs: number;
  replayDurationMs: number;
  sourceTokens: number;
  replayTokens: number;
  sourceCostUsd: number;
  replayCostUsd: number;
  statusChanged: boolean;
  outputChanged: boolean;
  delta: ReplayComparisonDelta;
  stepChanges: ReplayStepChange[];
}
