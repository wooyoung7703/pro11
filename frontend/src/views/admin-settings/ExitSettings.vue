<template>
  <div class="space-y-4">
    <div class="text-sm text-neutral-400">익절/청산(Exit) 정책 설정</div>

    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">새 Exit 정책 사용 (feature flag)</div>
        <select v-model="enableNewPolicyStr" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm">
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      </label>

      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">트레일링 모드</div>
        <select v-model="trailMode" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm">
          <option value="percent">percent</option>
          <option value="atr">atr</option>
        </select>
      </label>

      <label class="block" v-if="trailMode==='percent'">
        <div class="text-xs text-neutral-400 mb-1">트레일링 폭(%)</div>
        <input type="number" step="0.01" min="0" v-model.number="trailPercentPct" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
        <div class="text-[10px] text-neutral-500 mt-1">UI는 % 단위입니다. 서버에는 소수(fraction)로 저장됩니다.</div>
      </label>

      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">타임 스톱 (bars)</div>
        <input type="number" step="1" min="0" v-model.number="timeStopBars" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>

      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">부분 청산 사용</div>
        <select v-model="partialEnabledStr" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm">
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      </label>

      <label class="block md:col-span-2">
        <div class="text-xs text-neutral-400 mb-1">부분 청산 레벨 (CSV, 각 분배 비율: 0~1, 합 <= 1)</div>
        <input type="text" placeholder="예: 0.25,0.25" v-model="partialLevelsCsv" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>

      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">Exit 쿨다운 (bars)</div>
        <input type="number" step="1" min="0" v-model.number="cooldownBars" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>

      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">일일 손실 제한 (R)</div>
        <input type="number" step="0.1" min="0" v-model.number="dailyLossCapR" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>

      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">Exit 발생 시 거래 일시중지</div>
        <select v-model="freezeOnExitStr" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm">
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      </label>
    </div>

    <div class="flex items-center gap-2">
      <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-sky-500/20 text-sky-300 border border-sky-500/40 hover:bg-sky-500/30 transition" @click="save">저장</button>
      <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-neutral-800 text-neutral-300 border border-neutral-700 hover:bg-neutral-700/80 transition" @click="load">초기화</button>
      <span v-if="status" class="text-xs" :class="statusClass">{{ status }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, computed } from 'vue';
import http from '../../lib/http';

const props = defineProps<{ refreshKey?: number }>();
const emit = defineEmits<{ (e: 'changed'): void }>();

const enableNewPolicyStr = ref<'true'|'false'>('false');
const trailMode = ref<'percent'|'atr'>('percent');
const trailPercentPct = ref<number>(0.5); // UI in %
const timeStopBars = ref<number>(0);
const partialEnabledStr = ref<'true'|'false'>('false');
const partialLevelsCsv = ref<string>('');
const cooldownBars = ref<number>(0);
const dailyLossCapR = ref<number>(0);
const freezeOnExitStr = ref<'true'|'false'>('false');

const status = ref('');
const statusClass = computed(() => status.value.includes('실패') || status.value.includes('오류') ? 'text-red-400' : 'text-neutral-400');

async function getSetting(key: string): Promise<any|undefined> {
  try { const { data } = await http.get(`/admin/settings/${encodeURIComponent(key)}`); return data?.item?.value; } catch { return undefined; }
}
async function putSetting(key: string, value: any, apply = true): Promise<boolean> {
  try { const { data } = await http.put(`/admin/settings/${encodeURIComponent(key)}`, { value, apply }); return !!data?.status; } catch { return false; }
}

function parseCsvNumbers(csv: string): number[] | null {
  if (!csv.trim()) return [];
  const parts = csv.split(',').map(s => s.trim()).filter(Boolean);
  const nums = parts.map(p => Number(p));
  if (nums.some(n => !isFinite(n))) return null;
  return nums;
}

async function load() {
  status.value = '';
  const en = await getSetting('exit.enable_new_policy');
  if (typeof en === 'boolean') enableNewPolicyStr.value = en ? 'true' : 'false';

  const mode = await getSetting('exit.trail.mode');
  if (mode === 'percent' || mode === 'atr') trailMode.value = mode;

  const pct = await getSetting('exit.trail.percent');
  if (typeof pct === 'number') trailPercentPct.value = pct * 100.0; // store as % in UI

  const tsb = await getSetting('exit.time_stop.bars');
  if (typeof tsb === 'number' && tsb >= 0) timeStopBars.value = tsb;

  const pe = await getSetting('exit.partial.enabled');
  if (typeof pe === 'boolean') partialEnabledStr.value = pe ? 'true' : 'false';

  const pl = await getSetting('exit.partial.levels');
  if (Array.isArray(pl)) {
    const arr = (pl as any[]).map((x) => Number(x)).filter((n) => isFinite(n));
    partialLevelsCsv.value = arr.join(',');
  } else if (typeof pl === 'string') {
    partialLevelsCsv.value = pl;
  }

  const cdb = await getSetting('exit.cooldown.bars');
  if (typeof cdb === 'number' && cdb >= 0) cooldownBars.value = cdb;

  const dlr = await getSetting('exit.daily_loss_cap_r');
  if (typeof dlr === 'number' && dlr >= 0) dailyLossCapR.value = dlr;

  const fz = await getSetting('exit.freeze_on_exit');
  if (typeof fz === 'boolean') freezeOnExitStr.value = fz ? 'true' : 'false';
}

async function save() {
  // Basic validation
  if (trailMode.value === 'percent') {
    if (!(trailPercentPct.value >= 0 && trailPercentPct.value <= 50)) {
      status.value = '오류: 트레일링 폭(%)는 0~50 사이여야 합니다.';
      return;
    }
  }
  if (!(timeStopBars.value >= 0)) { status.value = '오류: 타임 스톱(bars)은 0 이상이어야 합니다.'; return; }
  if (!(cooldownBars.value >= 0)) { status.value = '오류: Exit 쿨다운(bars)은 0 이상이어야 합니다.'; return; }
  if (!(dailyLossCapR.value >= 0)) { status.value = '오류: 일일 손실 제한(R)은 0 이상이어야 합니다.'; return; }

  const levels = parseCsvNumbers(partialLevelsCsv.value);
  if (levels === null) { status.value = '오류: 부분 청산 레벨은 숫자 CSV여야 합니다.'; return; }
  if (levels.some(n => n < 0 || n > 1)) { status.value = '오류: 각 부분 청산 비율은 0~1 사이여야 합니다.'; return; }
  const sum = levels.reduce((a, b) => a + b, 0);
  if (sum > 1 + 1e-9) { status.value = '오류: 부분 청산 비율 합은 1을 초과할 수 없습니다.'; return; }

  status.value = '저장 중…';

  const results: boolean[] = [];
  results.push(await putSetting('exit.enable_new_policy', enableNewPolicyStr.value === 'true'));
  results.push(await putSetting('exit.trail.mode', trailMode.value));
  if (trailMode.value === 'percent') {
    results.push(await putSetting('exit.trail.percent', Number(trailPercentPct.value) / 100.0));
  }
  results.push(await putSetting('exit.time_stop.bars', Number(timeStopBars.value)));
  results.push(await putSetting('exit.partial.enabled', partialEnabledStr.value === 'true'));
  results.push(await putSetting('exit.partial.levels', levels));
  results.push(await putSetting('exit.cooldown.bars', Number(cooldownBars.value)));
  results.push(await putSetting('exit.daily_loss_cap_r', Number(dailyLossCapR.value)));
  results.push(await putSetting('exit.freeze_on_exit', freezeOnExitStr.value === 'true'));

  const ok = results.every(Boolean);
  status.value = ok ? '저장 완료' : '일부 실패';
  if (ok) emit('changed');
}

onMounted(load);
watch(() => props.refreshKey, () => load());
</script>
