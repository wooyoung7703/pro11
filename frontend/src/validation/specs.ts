// 주요 엔드포인트별 ValidationSpec 정의
import type { ValidationSpec } from './validate';

export const systemStatusSpec: ValidationSpec = {
  required: ['overall','components'],
  types: {
    overall: 'string',
    components: 'object'
  }
};

export const tradingSummarySpec: ValidationSpec = {
  required: ['decisions_5m','decisions_1m','auto_loop_enabled'],
  types: {
    decisions_5m: 'number',
    decisions_1m: 'number',
    auto_loop_enabled: 'boolean'
  },
  ranges: {
    decisions_5m: { min: 0 },
    decisions_1m: { min: 0 }
  }
};

export const decisionHistorySpec: ValidationSpec = {
  required: ['decisions'],
  types: { decisions: 'array' },
  arrayShape: {
    decisions: {
      required: ['ts','decision'],
      types: { ts: 'number', decision: 'string' }
    }
  }
};

// /api/inference/activity/recent 전용
export const recentInferenceSpec: ValidationSpec = {
  required: ['status','count','items'],
  types: { status: 'string', count: 'number', items: 'array' },
  ranges: { count: { min: 0 } },
  arrayShape: {
    items: {
      required: ['created_at','decision'],
      types: {
        created_at: 'number',
        probability: 'number',
        decision: 'number', // backend supplies numeric (-1,0,1)
        threshold: 'number',
        production: 'boolean',
        model_name: 'string',
        model_version: 'string',
        auto: 'boolean'
      }
    }
  }
};
