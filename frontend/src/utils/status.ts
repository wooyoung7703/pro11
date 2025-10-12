// 상태 및 색상 매핑 유틸
// 한 곳에서 상태 표현 규칙을 관리하여 Dashboard 등 여러 화면에서 재사용

export type CoreStatus = 'ok' | 'warning' | 'danger' | 'idle' | 'neutral' | 'unknown';

export interface StatusStyle {
  text: string; // 텍스트 색상 클래스
  bg?: string;  // 배경색 클래스 (배지 등)
  label?: string; // 한글 라벨
}

const MAP: Record<CoreStatus, StatusStyle> = {
  ok: { text: 'text-green-400', bg: 'bg-green-600/20', label: '정상' },
  warning: { text: 'text-amber-300', bg: 'bg-amber-500/20', label: '주의' },
  danger: { text: 'text-brand-danger', bg: 'bg-brand-danger/20', label: '위험' },
  idle: { text: 'text-neutral-500', bg: 'bg-neutral-600/40', label: '대기' },
  neutral: { text: 'text-neutral-300', bg: 'bg-neutral-600/40', label: '중립' },
  unknown: { text: 'text-neutral-400', bg: 'bg-neutral-700/40', label: '미확인' },
};

export function statusStyle(status: CoreStatus | string | null | undefined): StatusStyle {
  if (!status) return MAP.unknown;
  const key = status as CoreStatus;
  return MAP[key] || MAP.unknown;
}

// 최근 의사결정 시간(초) -> 상태 분류
export function classifyDecisionAge(ageSec: number | null | undefined): CoreStatus {
  if (ageSec == null) return 'unknown';
  if (ageSec < 120) return 'ok';
  if (ageSec < 600) return 'warning';
  return 'danger';
}

// 의사결정 수 (5분) 분류
export function classifyDecisionThroughput(count5m: number | null | undefined): CoreStatus {
  if (count5m == null) return 'unknown';
  if (count5m === 0) return 'idle';
  if (count5m < 5) return 'warning';
  return 'ok';
}

// disable_reason -> 한글 메시지 매핑
const DISABLE_REASON_MAP: Record<string,string> = {
  config_disabled: '환경설정 비활성화',
  runtime_disabled: '런타임 수동 비활성화',
  artifact_missing: '모델 아티팩트 누락',
};

export function humanDisableReason(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return DISABLE_REASON_MAP[reason] || reason;
}
