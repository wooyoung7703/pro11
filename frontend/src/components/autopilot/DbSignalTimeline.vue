<template>
  <div class="timeline">
    <header>
      <h3>DB 시그널 타임라인</h3>
      <div class="controls">
        <button class="btn" @click="refresh" :disabled="loading">{{ loading ? '새로고침…' : '새로고침' }}</button>
      </div>
    </header>
    <ul>
      <li v-for="row in items" :key="row.key">
        <div class="meta">
          <span class="type">{{ row.event }}</span>
          <span class="src">{{ row.source }}</span>
          <span class="ts">{{ format(row.ts) }}</span>
        </div>
        <div class="sub">
          <span v-if="row.signal_type" class="tag">{{ row.signal_type }}</span>
          <span v-if="row.symbol" class="tag">{{ row.symbol }}</span>
        </div>
        <pre class="payload">{{ pretty(row.details) }}</pre>
      </li>
    </ul>
    <div v-if="!items.length && !loading" class="empty">최근 저장된 시그널 타임라인이 없습니다.</div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, computed } from 'vue';
import http from '@/lib/http';

interface TimelineRow {
  ts: number;
  source: string;
  event: string;
  signal_type?: string | null;
  symbol?: string | null;
  details?: Record<string, unknown> | null;
}

const limit = 100;
const loading = ref(false);
const rows = ref<TimelineRow[]>([]);

async function refresh() {
  if (loading.value) return;
  loading.value = true;
  try {
    const resp = await http.get('/api/trading/signals/timeline', { params: { limit } });
    const data = (resp.data?.timeline ?? []) as any[];
    const mapped: TimelineRow[] = Array.isArray(data) ? data.map((r) => ({
      ts: Number(r.ts ?? 0),
      source: String(r.source ?? 'unknown'),
      event: String(r.event ?? 'unknown'),
      signal_type: r.signal_type ?? null,
      symbol: r.symbol ?? null,
      details: (typeof r.details === 'object' && r.details) ? r.details : null,
    })) : [];
    rows.value = mapped.filter((r) => Number.isFinite(r.ts) && r.ts > 0);
  } catch {
    // swallow for now; UI stays quiet
  } finally {
    loading.value = false;
  }
}

const items = computed(() => rows.value.slice(0, limit));

const formatter = new Intl.DateTimeFormat('ko-KR', {
  hour: '2-digit', minute: '2-digit', second: '2-digit',
});
function format(ts: number) { return formatter.format(new Date(ts * 1000)); }
function pretty(obj?: Record<string, unknown> | null) { try { return JSON.stringify(obj ?? {}, null, 2); } catch { return String(obj ?? ''); } }

onMounted(refresh);
</script>

<style scoped>
.timeline {
  background: #0f1a2a;
  border-radius: 10px;
  padding: 12px;
  border: 1px solid rgba(60, 86, 125, 0.4);
  color: #d2ddf0;
  display: flex;
  flex-direction: column;
  min-height: 260px;
  max-height: 260px;
}
header { display:flex; justify-content: space-between; align-items:center; margin-bottom:8px; }
h3 { margin:0; font-size:15px; }
.controls .btn { background:#1b2a40; color:#e5ecf5; border:1px solid rgba(70, 96, 140, 0.35); padding:6px 10px; border-radius:6px; cursor:pointer; }
ul { list-style:none; padding:0; margin:0; flex:1; overflow-y:auto; display:flex; flex-direction:column; gap:6px; }
li { background: rgba(19, 32, 51, 0.85); border-radius: 6px; padding: 8px; border: 1px solid rgba(70, 96, 140, 0.35); }
.meta { display:flex; gap:8px; justify-content:space-between; font-size:12px; color:#9cb2d6; margin-bottom:4px; }
.sub { display:flex; gap:6px; margin-bottom: 4px; }
.tag { font-size: 11px; background:#1d2a3a; color:#9fb6d8; padding:2px 6px; border-radius: 999px; }
.payload { margin:0; white-space: pre-wrap; font-size: 11px; color: #aebed9; font-family: 'JetBrains Mono','Fira Code', monospace; }
.empty { text-align:center; color:#6f7f98; padding:16px 0; margin-top:auto; }
</style>
