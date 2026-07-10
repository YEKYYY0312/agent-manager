import type { CostSummary, Step } from './types';
import { CostPanel } from './CostPanel';
import { LatencyPanel } from './LatencyPanel';

interface Props {
  cost: CostSummary;
  steps: Step[];
}

export function AnalysisView({ cost, steps }: Props) {
  return (
    <div className="analysis-view">
      <CostPanel cost={cost} />
      <LatencyPanel steps={steps} />
    </div>
  );
}
