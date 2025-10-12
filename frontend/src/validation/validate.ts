// 공통 응답 검증 유틸
// validateResponse(data, spec) -> { warnings: string[], errors: string[] }
// spec 구조:
// {
//   required?: string[];
//   types?: Record<string, 'number'|'string'|'boolean'|'object'|'array'>;
//   ranges?: Record<string,{min?:number,max?:number}>;
//   arrayShape?: Record<string,{ required?:string[]; types?:Record<string,string> }>;
// }

export interface ValidationSpec {
  required?: string[];
  types?: Record<string, 'number'|'string'|'boolean'|'object'|'array'>;
  ranges?: Record<string,{ min?: number; max?: number }>;
  arrayShape?: Record<string,{ required?: string[]; types?: Record<string,string> }>;
}

export interface ValidationResult {
  warnings: string[];
  errors: string[];
}

export function validateResponse(data: any, spec: ValidationSpec): ValidationResult {
  const warnings: string[] = [];
  const errors: string[] = [];
  if (!data || typeof data !== 'object') {
    return { warnings: [], errors: ['응답 형식 오류: 객체가 아닙니다'] };
  }
  if (spec.required) {
    for (const k of spec.required) {
      if (!Object.prototype.hasOwnProperty.call(data, k)) warnings.push(`필수 필드 없음: ${k}`);
    }
  }
  if (spec.types) {
    for (const [k, t] of Object.entries(spec.types)) {
      if (!(k in data)) continue;
      const v = data[k];
      const actual = Array.isArray(v) ? 'array' : typeof v;
      if (actual !== t) warnings.push(`필드 타입 불일치: ${k} (기대: ${t}, 실제: ${actual})`);
    }
  }
  if (spec.ranges) {
    for (const [k, r] of Object.entries(spec.ranges)) {
      const v = data[k];
      if (typeof v === 'number') {
        if (r.min != null && v < r.min) warnings.push(`값 최소 범위 위반: ${k} (${v} < ${r.min})`);
        if (r.max != null && v > r.max) warnings.push(`값 최대 범위 위반: ${k} (${v} > ${r.max})`);
        if (!Number.isFinite(v)) errors.push(`숫자 값 비정상: ${k}=${v}`);
      }
    }
  }
  if (spec.arrayShape) {
    for (const [arrKey, shape] of Object.entries(spec.arrayShape)) {
      const arr = data[arrKey];
      if (!Array.isArray(arr)) continue;
      for (let i=0; i<Math.min(arr.length,3); i++) {
        const item = arr[i];
        if (!item || typeof item !== 'object') { warnings.push(`${arrKey}[${i}] 객체 아님`); continue; }
        if (shape.required) {
          for (const rk of shape.required) if (!Object.prototype.hasOwnProperty.call(item, rk)) warnings.push(`${arrKey}[${i}] 필수 필드 없음: ${rk}`);
        }
        if (shape.types) {
          for (const [fk, ft] of Object.entries(shape.types)) {
            if (!(fk in item)) continue;
            const vv = (item as any)[fk];
            const at = Array.isArray(vv) ? 'array' : typeof vv;
            if (at !== ft) warnings.push(`${arrKey}[${i}].${fk} 타입 불일치 (기대:${ft}, 실제:${at})`);
          }
        }
      }
    }
  }
  return { warnings, errors };
}
