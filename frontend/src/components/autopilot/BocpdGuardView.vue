<template>
  <div class="guard-root">
    <div class="row">
      <label class="fld">
        <span>BOCPD 가드</span>
        <select v-model="enabledStr">
          <option value="true">켜기</option>
          <option value="false">끄기</option>
        </select>
      </label>
      <label class="fld">
        <span>Hazard λ</span>
        <input type="number" step="0.001" min="0.000" max="1" v-model.number="hazard" />
      </label>
      <label class="fld">
        <span>최소 하락폭 (%)</span>
        <input type="number" step="0.05" min="0" v-model.number="minDownPct" />
      </label>
      <label class="fld">
        <span>쿨다운 (초)</span>
        <input type="number" step="1" min="0" v-model.number="cooldownSec" />
      </label>
      <label class="fld">
        <span>스케일인 시 BOCPD 필수</span>
        <select v-model="scaleInRequireStr">
          <option value="true">예</option>
          <option value="false">아니오</option>
        </select>
      </label>
      <div class="actions">
        <button @click="save" :disabled="busy">설정 저장</button>
        <button @click="reload" :disabled="busy">상태 새로고침</button>
        <button v-if="isDev" @click="testFeed" :disabled="busy" title="개발환경 전용: synthetic 가격 시퀀스를 주입하여 이벤트를 강제로 생성합니다">Dev: 테스트 이벤트 생성</button>
      </div>
    </div>

    <div class="note" v-if="note">{{ note }}</div>

    <div class="status" v-if="lastStatus">
      <span>상태: {{ lastStatus.enabled ? '켜짐' : '꺼짐' }}</span>
      <span>하락감지: {{ lastStatus.down_detected ? '예' : '아니오' }}</span>
      <span>최소하락: {{ (Number(lastStatus.min_down||0)*100).toFixed(2) }}%</span>
      <span>쿨다운: {{ Number(lastStatus.cooldown_sec||0).toFixed(0) }}s</span>
      <span>Hazard: {{ Number(lastStatus.hazard||0).toFixed(2) }}</span>
    </div>

    <div class="timeline">
      <div class="ev" v-for="(ev, i) in events" :key="i">
        <div class="ts">{{ fmtTs(ev.ts) }}</div>
        <div class="desc">{{ ev.desc }}</div>
      </div>
    </div>

    <div class="placeholder" v-if="!events.length">
      상태는 백엔드 준비 후 표시됩니다. (가드가 통합되면 최근 체인지포인트/결정 로그가 여기에 노출됩니다)
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import http from '@/lib/http';

const enabledStr = ref<'true'|'false'>('false');
const hazard = ref<number>(0.01);
const minDownPct = ref<number>(0.5);
const cooldownSec = ref<number>(300);
const scaleInRequireStr = ref<'true'|'false'>('true');
const busy = ref(false);
const note = ref('');
const isDev = !!import.meta.env.DEV;

const events = ref<{ ts: number; desc: string }[]>([]);
const lastStatus = ref<any | null>(null);

async function getSetting(key: string): Promise<any> {
  try { const { data } = await http.get(`/admin/settings/${encodeURIComponent(key)}`); return data?.item?.value; } catch { return undefined; }
}
async function putSetting(key: string, value: any): Promise<boolean> {
  try { const { data } = await http.put(`/admin/settings/${encodeURIComponent(key)}`, { value, apply: true }); return !!data?.status; } catch { return false; }
}

async function load() {
  const en = await getSetting('guard.bocpd.enabled');
  const hz = await getSetting('guard.bocpd.hazard');
  const md = await getSetting('guard.bocpd.min_down');
  const cd = await getSetting('guard.bocpd.cooldown_sec');
  const si = await getSetting('live_scale_in_require_bocpd');
  if (typeof en === 'boolean') enabledStr.value = en ? 'true' : 'false';
  if (typeof hz === 'number' && isFinite(hz)) hazard.value = hz;
  if (typeof md === 'number' && isFinite(md)) minDownPct.value = md * 100;
  if (typeof cd === 'number' && isFinite(cd)) cooldownSec.value = cd;
  if (typeof si === 'boolean') scaleInRequireStr.value = si ? 'true' : 'false';
}

async function save() {
  busy.value = true; note.value = '';
  try {
    // Use bulk config endpoint to persist and apply in one call
    await http.post('/admin/guard/bocpd/config', {
      enabled: enabledStr.value === 'true',
      hazard: hazard.value,
      min_down: minDownPct.value / 100,
      cooldown_sec: cooldownSec.value,
    });
    // Persist scale-in requirement toggle via settings API
    await putSetting('live_scale_in_require_bocpd', scaleInRequireStr.value === 'true');
    note.value = '저장되었습니다 (일괄 적용)';
    // Refresh status after save
    await reload();
  } finally { busy.value = false; }
}

async function reload() {
  busy.value = true; note.value = '';
  try {
    const { data } = await http.get('/api/guard/bocpd/status');
    const rows = Array.isArray(data?.events) ? data.events : [];
    events.value = rows.map((r: any) => ({ ts: Number(r.ts) || 0, desc: String(r.desc || '') })).slice(-100).reverse();
    lastStatus.value = data?.status || null;
  } catch (e: any) {
    note.value = '상태 엔드포인트가 아직 준비되지 않았습니다 (백엔드 필요).';
    events.value = [];
    lastStatus.value = null;
  } finally { busy.value = false; }
}

function fmtTs(ts: number): string {
  if (!ts) return '-';
  const d = new Date(Math.floor(ts * 1000));
  return d.toLocaleString();
}

async function testFeed() {
  busy.value = true; note.value = '';
  try {
    // Simple synthetic down sequence to trigger down_detected
    const base = 100;
    const prices = [0, -0.2, -0.5, -0.9, -1.0, -1.1, -1.2, -1.3, -1.35, -1.4].map((d) => base + d);
    const { data } = await http.post('/dev/guard/bocpd/feed', { prices, interval_sec: 1.0 });
    if (data && data.status === 'ok') {
      note.value = `테스트 피드 완료 (fed=${data.fed}, events=${data.events_appended})`;
    } else {
      note.value = '테스트 피드 응답이 비정상입니다';
    }
    await reload();
  } catch (e: any) {
    const msg = String(e?.response?.data?.detail || e?.message || e);
    if (msg.includes('dev_only')) {
      note.value = '개발/로컬 환경에서만 동작합니다 (APP_ENV=local|dev|test 필요)';
    } else if (msg.includes('missing_prices')) {
      note.value = '백엔드가 요청을 거부했습니다: prices 누락';
    } else {
      note.value = '테스트 피드 실패: ' + msg;
    }
  } finally { busy.value = false; }
}

onMounted(() => { load(); reload(); });
</script>

<style scoped>
.guard-root { display:flex; flex-direction:column; gap:10px; }
.row { display:flex; flex-wrap:wrap; gap:10px; align-items:end; }
.fld { display:flex; flex-direction:column; gap:4px; font-size:12px; color:#cbd7eb; }
.fld select, .fld input { background:#0b1322; color:#e6edf3; border:1px solid #1e2a44; border-radius:8px; padding:6px 8px; min-width:120px; }
.actions { display:flex; gap:8px; }
.actions button { background:#0b1322; color:#cbd7eb; border:1px solid #1e2a44; border-radius:8px; padding:6px 10px; cursor:pointer; }
.note { color:#9cb2d6; font-size:12px; }
.status { display:flex; gap:12px; align-items:center; color:#cfe1ff; font-size:12px; padding:6px 8px; background:#0b1322; border:1px solid #1e2a44; border-radius:8px; }
.timeline { display:flex; flex-direction:column; gap:6px; }
.ev { background:#0b1322; border:1px solid #1e2a44; border-radius:8px; padding:8px; display:flex; gap:8px; }
.ev .ts { color:#8fa3c0; font-size:12px; min-width: 180px; }
.ev .desc { color:#e5ecf5; font-size:13px; }
.placeholder { color:#8fa3c0; font-size:12px; padding:8px; }
</style>
