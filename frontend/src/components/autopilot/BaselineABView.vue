<template>
  <div class="ab-root">
    <div class="row">
      <label class="fld">
        <span>Baseline 사용</span>
        <select v-model="enabledStr">
          <option value="true">켜기</option>
          <option value="false">끄기</option>
        </select>
      </label>
      <label class="fld">
        <span>모델</span>
        <select v-model="model">
          <option value="xgb">XGBoost</option>
          <option value="lgbm">LightGBM</option>
        </select>
      </label>
      <label class="fld">
        <span>Threshold</span>
        <input type="number" step="0.01" min="0" max="1" v-model.number="threshold" />
      </label>
      <div class="actions">
        <button @click="saveSettings" :disabled="busy">설정 저장</button>
        <button @click="runPreview" :disabled="busy">프리뷰</button>
        <button @click="runTrain" :disabled="busy">학습</button>
      </div>
    </div>

    <div class="note" v-if="note">{{ note }}</div>

    <div class="metrics">
      <div class="metric" v-for="m in metricsList" :key="m.k">
        <div class="k">{{ m.k }}</div>
        <div class="v">{{ m.v }}</div>
      </div>
    </div>

    <div class="placeholder" v-if="!metricsList.length">
      ROC/PR 차트와 메트릭은 백엔드 준비 후 표시됩니다.
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import http from '@/lib/http';

const enabledStr = ref<'true'|'false'>('false');
const model = ref<'xgb'|'lgbm'>('xgb');
const threshold = ref<number>(0.5);
const busy = ref(false);
const note = ref('');

const metricsList = ref<{k: string; v: string}[]>([]);

async function getSetting(key: string): Promise<any> {
  try { const { data } = await http.get(`/admin/settings/${encodeURIComponent(key)}`); return data?.item?.value; } catch { return undefined; }
}
async function putSetting(key: string, value: any): Promise<boolean> {
  try { const { data } = await http.put(`/admin/settings/${encodeURIComponent(key)}`, { value, apply: false }); return !!data?.status; } catch { return false; }
}

async function loadSettings() {
  const en = await getSetting('ml.baseline.enabled');
  const mdl = await getSetting('ml.baseline.model');
  const th = await getSetting('ml.baseline.threshold');
  if (typeof en === 'boolean') enabledStr.value = en ? 'true' : 'false';
  if (mdl === 'xgb' || mdl === 'lgbm') model.value = mdl;
  if (typeof th === 'number' && isFinite(th)) threshold.value = th;
}

async function saveSettings() {
  busy.value = true; note.value = '';
  try {
    await Promise.all([
      putSetting('ml.baseline.enabled', enabledStr.value === 'true'),
      putSetting('ml.baseline.model', model.value),
      putSetting('ml.baseline.threshold', threshold.value),
    ]);
    note.value = '저장되었습니다';
  } finally { busy.value = false; }
}

async function runPreview() {
  busy.value = true; note.value = '';
  metricsList.value = [];
  try {
    const { data } = await http.post('/api/training/baseline/preview', { model: model.value, threshold: threshold.value });
    const m = data?.metrics || {};
    metricsList.value = Object.entries(m).map(([k, v]) => ({ k, v: String(v) }));
  } catch (e: any) {
    note.value = '프리뷰 엔드포인트가 아직 준비되지 않았습니다 (백엔드 필요).';
  } finally { busy.value = false; }
}

async function runTrain() {
  busy.value = true; note.value = '';
  try {
    const { data } = await http.post('/api/training/baseline/train', { model: model.value, threshold: threshold.value });
    note.value = (data?.status || '요청됨');
  } catch (e: any) {
    note.value = '학습 엔드포인트가 아직 준비되지 않았습니다 (백엔드 필요).';
  } finally { busy.value = false; }
}

onMounted(() => { loadSettings(); });
</script>

<style scoped>
.ab-root { display: flex; flex-direction: column; gap: 10px; }
.row { display: flex; flex-wrap: wrap; gap: 10px; align-items: end; }
.fld { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #cbd7eb; }
.fld select, .fld input { background:#0b1322; color:#e6edf3; border:1px solid #1e2a44; border-radius:8px; padding:6px 8px; min-width: 120px; }
.actions { display: flex; gap: 8px; }
.actions button { background:#0b1322; color:#cbd7eb; border:1px solid #1e2a44; border-radius:8px; padding:6px 10px; cursor:pointer; }
.note { color:#9cb2d6; font-size: 12px; }
.metrics { display: grid; grid-template-columns: repeat(auto-fit,minmax(160px,1fr)); gap: 8px; }
.metric { background:#0b1322; border:1px solid #1e2a44; border-radius:8px; padding:8px; }
.metric .k { color:#8fa3c0; font-size:12px; }
.metric .v { color:#e5ecf5; font-weight:600; font-size:13px; }
.placeholder { color:#8fa3c0; font-size:12px; padding:8px; }
</style>
