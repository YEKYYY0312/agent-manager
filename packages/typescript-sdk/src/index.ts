import { mkdirSync, writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { join, resolve } from 'node:path';

export type TraceStatus = 'success' | 'error' | 'timeout' | 'cancelled';
export type StepType = 'model_call' | 'tool_call' | 'retrieval' | 'memory' | 'planner' | 'control' | 'custom';

export interface Cost {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  amount_usd: number;
}

export interface TraceStep {
  id: string;
  parent_id: string | null;
  type: StepType;
  name: string;
  status: TraceStatus;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  model: string;
  input: unknown;
  output: unknown;
  tool: { name: string; args: unknown; result: unknown } | null;
  cost: Cost | null;
  error: { type: string; message: string; stack: string } | null;
  events: unknown[];
  replayable: boolean;
  metadata: Record<string, unknown>;
}

export interface TraceDocument {
  schema_version: '1.0';
  run: {
    id: string;
    task: string;
    status: TraceStatus;
    started_at: string;
    ended_at: string | null;
    duration_ms: number | null;
    labels: Record<string, string>;
    final_output: unknown;
    cost: Cost | null;
  };
  steps: TraceStep[];
}

export interface TraceAgentOptions {
  outputDir?: string;
  labels?: Record<string, string>;
}

export interface StepOptions {
  model?: string;
  replayable?: boolean;
  cost?: Partial<Cost>;
}

export class TraceSession {
  readonly trace: TraceDocument;
  private readonly outputDir: string;

  constructor(task: string, options: TraceAgentOptions = {}) {
    this.outputDir = resolve(options.outputDir ?? 'traces');
    this.trace = {
      schema_version: '1.0',
      run: {
        id: randomUUID().replaceAll('-', ''),
        task,
        status: 'success',
        started_at: now(),
        ended_at: null,
        duration_ms: null,
        labels: { ...options.labels },
        final_output: null,
        cost: null,
      },
      steps: [],
    };
  }

  tool<T>(name: string, args: unknown, fn: () => T | Promise<T>, options: StepOptions = {}): Promise<T> {
    return this.record('tool_call', name, args, fn, options, true);
  }

  model<T>(name: string, input: unknown, fn: () => T | Promise<T>, options: StepOptions = {}): Promise<T> {
    return this.record('model_call', name, input, fn, options, false);
  }

  step<T>(type: StepType, name: string, input: unknown, fn: () => T | Promise<T>, options: StepOptions = {}): Promise<T> {
    return this.record(type, name, input, fn, options, false);
  }

  complete(status: TraceStatus, finalOutput: unknown): void {
    this.trace.run.status = status;
    this.trace.run.final_output = finalOutput;
    this.trace.run.ended_at = now();
    this.trace.run.duration_ms = elapsed(this.trace.run.started_at);
  }

  write(): string {
    mkdirSync(this.outputDir, { recursive: true });
    const path = join(this.outputDir, `${this.trace.run.id}.trace.json`);
    writeFileSync(path, JSON.stringify(this.trace, null, 2), 'utf8');
    return path;
  }

  private async record<T>(
    type: StepType,
    name: string,
    input: unknown,
    fn: () => T | Promise<T>,
    options: StepOptions,
    isTool: boolean,
  ): Promise<T> {
    const startedAt = now();
    const step: TraceStep = {
      id: randomUUID().replaceAll('-', ''),
      parent_id: null,
      type,
      name,
      status: 'success',
      started_at: startedAt,
      ended_at: null,
      duration_ms: null,
      model: options.model ?? '',
      input,
      output: null,
      tool: isTool ? { name, args: input, result: null } : null,
      cost: options.cost ? normalizeCost(options.cost) : null,
      error: null,
      events: [],
      replayable: options.replayable ?? false,
      metadata: {},
    };
    this.trace.steps.push(step);
    try {
      const result = await fn();
      step.output = result;
      if (step.tool) step.tool.result = result;
      completeStep(step, 'success', startedAt);
      return result;
    } catch (error) {
      const caught = error instanceof Error ? error : new Error(String(error));
      step.error = { type: caught.name, message: caught.message, stack: caught.stack ?? '' };
      completeStep(step, 'error', startedAt);
      throw error;
    }
  }
}

export async function traceAgent<T>(
  task: string,
  fn: (trace: TraceSession) => T | Promise<T>,
  options: TraceAgentOptions = {},
): Promise<T> {
  const trace = new TraceSession(task, options);
  try {
    const result = await fn(trace);
    trace.complete('success', result);
    return result;
  } catch (error) {
    const caught = error instanceof Error ? error : new Error(String(error));
    trace.complete('error', { error: caught.message });
    throw error;
  } finally {
    trace.write();
  }
}

function now(): string {
  return new Date().toISOString();
}

function elapsed(startedAt: string): number {
  return Math.max(0, Date.now() - new Date(startedAt).getTime());
}

function completeStep(step: TraceStep, status: TraceStatus, startedAt: string): void {
  step.status = status;
  step.ended_at = now();
  step.duration_ms = elapsed(startedAt);
}

function normalizeCost(cost: Partial<Cost>): Cost {
  const inputTokens = cost.input_tokens ?? 0;
  const outputTokens = cost.output_tokens ?? 0;
  return {
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    total_tokens: cost.total_tokens ?? inputTokens + outputTokens,
    amount_usd: cost.amount_usd ?? 0,
  };
}
