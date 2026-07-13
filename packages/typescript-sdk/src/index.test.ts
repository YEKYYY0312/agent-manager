import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { traceAgent } from './index.ts';

const outputDir = mkdtempSync(join(tmpdir(), 'agent-devtools-ts-'));

try {
  const result = await traceAgent(
    'TypeScript agent run',
    async (trace) => {
      const weather = await trace.tool('weather.lookup', { city: 'Shanghai' }, async () => ({ summary: 'Warm' }));
      return trace.model('answer', { weather }, async () => ({ content: 'Warm in Shanghai' }), {
        model: 'gpt-4.1-mini',
        cost: { input_tokens: 10, output_tokens: 5, amount_usd: 0.001 },
      });
    },
    { outputDir },
  );

  if (result.content !== 'Warm in Shanghai') throw new Error('agent result was not returned');
  const files = readdirSync(outputDir).filter((name) => name.endsWith('.trace.json'));
  if (files.length !== 1) throw new Error(`expected one trace file, got ${files.length}`);
  const trace = JSON.parse(readFileSync(join(outputDir, files[0]!), 'utf8'));
  if (trace.schema_version !== '1.0') throw new Error('trace schema version is missing');
  if (trace.run.task !== 'TypeScript agent run' || trace.run.status !== 'success') throw new Error('run was not completed');
  if (trace.steps.length !== 2 || trace.steps[0].tool.name !== 'weather.lookup') throw new Error('tool step was not recorded');
  if (trace.steps[1].model !== 'gpt-4.1-mini') throw new Error('model metadata was not recorded');
  if (trace.steps[1].cost.total_tokens !== 15 || trace.steps[1].cost.amount_usd !== 0.001) throw new Error('model cost was not recorded');
} finally {
  rmSync(outputDir, { recursive: true, force: true });
}

const errorOutputDir = mkdtempSync(join(tmpdir(), 'agent-devtools-ts-error-'));
try {
  await traceAgent('TypeScript failing run', async (trace) => {
    await trace.tool('failing.tool', {}, async () => {
      throw new Error('tool unavailable');
    });
    return 'unreachable';
  }, { outputDir: errorOutputDir }).then(
    () => { throw new Error('failing agent unexpectedly resolved'); },
    () => undefined,
  );

  const files = readdirSync(errorOutputDir).filter((name) => name.endsWith('.trace.json'));
  const trace = JSON.parse(readFileSync(join(errorOutputDir, files[0]!), 'utf8'));
  if (trace.run.status !== 'error' || trace.steps[0].error.message !== 'tool unavailable') throw new Error('failing run was not preserved');
} finally {
  rmSync(errorOutputDir, { recursive: true, force: true });
}
