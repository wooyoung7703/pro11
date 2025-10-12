export type ConfirmPreset = {
  title: string;
  message: string;
  requireText?: string;
  delayMs?: number;
};

export const confirmPresets = {
  labelerRun(minAgeSec: number, limit: number): ConfirmPreset {
    return {
      title: '라벨러 실행',
      message: `라벨러를 1회 실행할까요?\n(min_age=${Math.max(0, minAgeSec)}s, limit=${Math.max(1, limit)})`,
      requireText: 'RUN',
      delayMs: 800,
    };
  },
  yearBackfillCancel(): ConfirmPreset {
    return {
      title: 'Year Backfill 취소',
      message: '진행 중인 Year Backfill을 취소할까요?\n이 작업은 중단 지점에서 partial 상태로 표시될 수 있습니다.',
      requireText: 'CANCEL',
      delayMs: 1200,
    };
  },
  ensureTables(): ConfirmPreset {
    return {
      title: '전체 테이블 생성',
      message: '핵심 테이블을 생성/보장합니다 (idempotent). 운영 DB에서 주의하세요.',
      requireText: 'YES',
      delayMs: 800,
    };
  },
  bootstrapRun(dry: boolean): ConfirmPreset {
    return dry ? {
      title: 'Bootstrap (드라이런)',
      message: '드라이런: 학습/프로모션은 수행하지 않고 결과를 시뮬레이션합니다. OHLCV/갭/피처 백필은 실제 실행됩니다.',
      delayMs: 600,
    } : {
      title: 'Bootstrap 실행',
      message: '초기 모델 생성 파이프라인을 실행합니다. 리소스 사용과 임계치 검증에 유의하세요.',
      requireText: 'RUN',
      delayMs: 1200,
    };
  },
  featBackfill(target: number, window?: number | null): ConfirmPreset {
    return {
      title: 'Feature Backfill',
      message: `피처 백필을 실행합니다. target=${target}, window=${window ?? 'auto'}`,
      delayMs: 600,
    };
  },
  trainingTrigger(): ConfirmPreset {
    return {
      title: '학습 트리거',
      message: '학습 잡을 트리거합니다. 리소스 사용과 큐 상태를 확인하세요.',
      delayMs: 600,
    };
  },
  fastUpgrade(): ConfirmPreset {
    return {
      title: 'fast_startup upgrade',
      message: '초기 지연 루프 업그레이드/기동을 호출합니다.',
      delayMs: 600,
    };
  },
  modelPromote(version: string): ConfirmPreset {
    return {
      title: '모델 승격',
      message: `버전 ${version} 을(를) 프로덕션으로 승격할까요?`,
      requireText: 'PROMOTE',
      delayMs: 800,
    };
  },
  modelRollback(version: string): ConfirmPreset {
    return {
      title: '프로덕션 롤백',
      message: `프로덕션을 버전 ${version} 으로 롤백할까요?`,
      requireText: 'ROLLBACK',
      delayMs: 800,
    };
  },
  modelDelete(version: string): ConfirmPreset {
    return {
      title: '모델 레코드 삭제',
      message: `버전 ${version} 레지스트리 레코드를 삭제(soft-delete)할까요?\n아티팩트 파일은 삭제하지 않습니다.`,
      requireText: 'DELETE',
      delayMs: 1000,
    };
  },
};
