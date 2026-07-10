import { useEffect, useMemo, useState } from 'react';
import type { ReplayToolMock, Trace } from './types';
import { buildReplayCliCommand, buildReplayPlan, buildReplayPlanDownload, fmtMs, listReplayCheckpoints, stepIcon } from './trace';
import { EmptyState } from './components/EmptyState';
import { MetricCard } from './components/MetricCard';
import { StatusBadge } from './components/StatusBadge';

interface Props {
  trace: Trace;
  tracePath: string;
}

export function ReplayView({ trace, tracePath }: Props) {
  const checkpoints = useMemo(() => listReplayCheckpoints(trace), [trace]);
  const [selectedStepId, setSelectedStepId] = useState(checkpoints[0]?.id ?? '');
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'downloaded' | 'failed' | 'invalid'>('idle');
  const [mockEdits, setMockEdits] = useState<Record<string, string>>({});

  useEffect(() => {
    setSelectedStepId(checkpoints[0]?.id ?? '');
    setCopyState('idle');
  }, [trace.run.id, checkpoints]);

  const plan = useMemo(
    () => buildReplayPlan(trace, selectedStepId),
    [trace, selectedStepId],
  );
  useEffect(() => {
    const next: Record<string, string> = {};
    for (const mock of plan.mockedTools) {
      next[mock.stepId] = JSON.stringify(mock.result, null, 2);
    }
    setMockEdits(next);
  }, [trace.run.id, selectedStepId, plan.mockedTools]);

  const mockState = useMemo(
    () => buildEditedMocks(plan.mockedTools, mockEdits),
    [plan.mockedTools, mockEdits],
  );
  const replayPlanPath = mockState.mockedTools.length > 0 ? 'replay-plan.json' : undefined;
  const planDownload = useMemo(
    () => buildReplayPlanDownload(plan, mockState.mockedTools),
    [plan, mockState.mockedTools],
  );
  const planJson = planDownload.content;
  const replayCommand = useMemo(
    () => buildReplayCliCommand(tracePath, selectedStepId, trace.run.id, replayPlanPath),
    [tracePath, selectedStepId, trace.run.id, replayPlanPath],
  );

  const copyPlan = async () => {
    if (mockState.hasInvalidMocks) {
      setCopyState('invalid');
      return;
    }
    try {
      await navigator.clipboard.writeText(planJson);
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
  };

  const downloadPlan = () => {
    if (mockState.hasInvalidMocks) {
      setCopyState('invalid');
      return;
    }
    try {
      const blob = new Blob([planDownload.content], { type: planDownload.mimeType });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = planDownload.fileName;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setCopyState('downloaded');
    } catch {
      setCopyState('failed');
    }
  };

  const copyCommand = async () => {
    if (mockState.hasInvalidMocks) {
      setCopyState('invalid');
      return;
    }
    try {
      await navigator.clipboard.writeText(replayCommand);
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
  };

  if (checkpoints.length === 0) {
    return (
      <div className="panel-full">
        <EmptyState
          title="回放 Replay"
          message="这个 Trace 没有标记 replayable 的步骤，暂时不能生成回放计划。"
        />
      </div>
    );
  }

  return (
    <div className="panel-full replay-view">
      <div className="replay-header">
        <div>
          <h2 className="panel-heading">回放工作台 Replay</h2>
          <p className="muted replay-subtitle">选择一个可回放步骤，生成从该步骤开始的回放计划。当前版本只生成计划，不重新执行 Agent。</p>
        </div>
        <div className="replay-actions">
          <button type="button" className="btn btn-secondary" onClick={copyCommand} disabled={mockState.hasInvalidMocks}>复制命令</button>
          <button type="button" className="btn btn-secondary" onClick={downloadPlan} disabled={mockState.hasInvalidMocks}>下载计划</button>
          <button type="button" className="btn" onClick={copyPlan} disabled={mockState.hasInvalidMocks}>复制计划</button>
        </div>
      </div>

      {copyState === 'copied' && <p className="replay-copy-state">已复制到剪贴板。</p>}
      {copyState === 'downloaded' && <p className="replay-copy-state">已下载 replay-plan.json。</p>}
      {copyState === 'failed' && <p className="replay-copy-state replay-copy-error">浏览器阻止复制，请手动复制下方内容。</p>}
      {copyState === 'invalid' && <p className="replay-copy-state replay-copy-error">Mock result JSON 无效，请先修正。</p>}

      <div className="metrics-row">
        <MetricCard label="可回放点" value={checkpoints.length} kind="accent" />
        <MetricCard label="回放步骤" value={plan.stepsToReplay.length} />
        <MetricCard label="工具 Mock" value={plan.mockedTools.length} />
        <MetricCard label="起点状态" value={plan.startStep ? <StatusBadge status={plan.startStep.status} /> : '-'} />
      </div>

      <div className="replay-grid">
        <section className="replay-section">
          <h3>回放起点</h3>
          <div className="replay-checkpoint-list">
            {checkpoints.map((step, index) => (
              <button
                key={step.id}
                type="button"
                className={`replay-checkpoint ${step.id === selectedStepId ? 'selected' : ''}`}
                onClick={() => setSelectedStepId(step.id)}
              >
                <span className="step-pos">{index + 1}</span>
                <span className={`step-kind kind-${step.type}`}>{stepIcon(step.type)}</span>
                <span className="replay-checkpoint-main">
                  <span className="step-name">{step.name}</span>
                  <span className="step-meta">{step.type} / {fmtMs(step.duration_ms)}</span>
                </span>
                <StatusBadge status={step.status} />
              </button>
            ))}
          </div>
        </section>

        <section className="replay-section">
          <h3>将重放的步骤</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>步骤 Step</th>
                <th>类型</th>
                <th>状态</th>
                <th className="num">耗时</th>
              </tr>
            </thead>
            <tbody>
              {plan.stepsToReplay.map((step, index) => (
                <tr key={step.id}>
                  <td>{index + 1}</td>
                  <td>{step.name}</td>
                  <td className="mono">{step.type}</td>
                  <td><StatusBadge status={step.status} /></td>
                  <td className="num">{fmtMs(step.duration_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      <div className="replay-grid replay-grid-bottom">
        <section className="replay-section">
          <h3>工具 Mock</h3>
          {plan.mockedTools.length === 0 ? (
            <p className="muted replay-note">从当前起点开始没有可复用的工具结果。</p>
          ) : (
            <div className="replay-mock-list">
              {plan.mockedTools.map((mock) => (
                <div key={mock.stepId} className="replay-mock">
                  <div className="replay-mock-title">
                    <span className="mono">{mock.name}</span>
                    <span className="muted">{mock.stepId}</span>
                  </div>
                  <pre>{JSON.stringify({ args: mock.args }, null, 2)}</pre>
                  <textarea
                    className={`replay-mock-editor ${mockState.errors[mock.stepId] ? 'invalid' : ''}`}
                    value={mockEdits[mock.stepId] ?? JSON.stringify(mock.result, null, 2)}
                    onChange={(event) => setMockEdits((current) => ({ ...current, [mock.stepId]: event.target.value }))}
                    spellCheck={false}
                    aria-label={`${mock.name} result JSON`}
                  />
                  {mockState.errors[mock.stepId] && (
                    <p className="replay-mock-error">Result JSON 无效</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="replay-section">
          <h3>Replay CLI 命令</h3>
          <pre className="replay-command">{replayCommand}</pre>
          {tracePath.startsWith('import:') && (
            <p className="muted replay-note">浏览器不能读取本地绝对路径，请把占位路径替换成真实 trace 文件路径。</p>
          )}
        </section>
      </div>

      <div className="replay-grid replay-grid-bottom">
        <section className="replay-section replay-section-wide">
          <h3>Replay Plan JSON</h3>
          <pre className="replay-plan-json">{planJson}</pre>
        </section>
      </div>
    </div>
  );
}

function buildEditedMocks(mockedTools: ReplayToolMock[], mockEdits: Record<string, string>) {
  const errors: Record<string, string> = {};
  const edited = mockedTools.map((mock) => {
    const text = mockEdits[mock.stepId] ?? JSON.stringify(mock.result, null, 2);
    try {
      return { ...mock, result: JSON.parse(text) } satisfies ReplayToolMock;
    } catch {
      errors[mock.stepId] = 'invalid';
      return mock;
    }
  });

  return {
    mockedTools: edited,
    errors,
    hasInvalidMocks: Object.keys(errors).length > 0,
  };
}
