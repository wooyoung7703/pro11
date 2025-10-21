<template>
  <div class="auto-dashboard">
    <header class="top-bar">
      <div class="left">
        <h1>{{ strategy?.name || 'Autopilot' }}</h1>
        <span class="version" v-if="strategy?.version">v{{ strategy.version }}</span>
        <span class="mode" :class="strategy?.mode">{{ strategy?.mode?.toUpperCase() }}</span>
        <span class="status" :class="{ on: strategy?.enabled, off: !strategy?.enabled }">
          {{ strategy?.enabled ? 'ON' : 'OFF' }}
        </span>
      </div>
      <div class="right">
        <span v-if="strategy" class="heartbeat">마지막 하트비트 · {{ formatTs(strategy.last_heartbeat) }}</span>
        <span v-if="streamConnected" class="stream ok">실시간 연결</span>
        <span v-else class="stream warn">재연결 중…</span>
      </div>
    </header>

    <main class="content">
      <section class="chart-panel">
        <AutoOhlcvPanel />
      </section>
      <aside class="side-panel">
        <SignalTimeline :events="events" />
        <PositionSnapshot :position="position" :signal="activeSignal" />
        <RiskLimitsCard :risk="risk" />
      </aside>
    </main>

    <footer class="bottom">
      <AutoPerformanceSummary :performance="performance" />
      <div class="inline-row">
        <!-- Show compact inference snapshot (from Playground data model) -->
        <InferenceSnapshotCard />
        <InferenceActivityBadge />
        <CalibrationStatusBadge />
      </div>
      <div>
        <div class="logs-header">
          <div class="left">최근 이벤트 로그</div>
          <div class="right">
            <button class="btn" @click="loadEvents" :disabled="loadingEvents">{{ loadingEvents ? '불러오는 중…' : '불러오기' }}</button>
          </div>
        </div>
        <ExecutionLogTable :events="eventLogForTable" />
      </div>
      <SystemHealthBar :health="health" />
    </footer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, computed, ref } from 'vue';
import AutoOhlcvPanel from '@/components/autopilot/AutoOhlcvPanel.vue';
import SignalTimeline from '@/components/autopilot/SignalTimeline.vue';
import PositionSnapshot from '@/components/autopilot/PositionSnapshot.vue';
import RiskLimitsCard from '@/components/autopilot/RiskLimitsCard.vue';
import AutoPerformanceSummary from '@/components/autopilot/AutoPerformanceSummary.vue';
import ExecutionLogTable from '@/components/autopilot/ExecutionLogTable.vue';
import InferenceSnapshotCard from '@/components/autopilot/InferenceSnapshotCard.vue';
import InferenceActivityBadge from '@/components/autopilot/InferenceActivityBadge.vue';
import CalibrationStatusBadge from '@/components/autopilot/CalibrationStatusBadge.vue';
import SystemHealthBar from '@/components/autopilot/SystemHealthBar.vue';
import { useAutoTraderStore } from '@/stores/autoTrader';
const store = useAutoTraderStore();

const strategy = computed(() => store.strategy);
const position = computed(() => store.position);
const activeSignal = computed(() => store.activeSignal);
const risk = computed(() => store.risk);
const performance = computed(() => store.performance);
const events = computed(() => store.events);
const health = computed(() => store.health);
const streamConnected = computed(() => store.streamConnected);
const eventLogForTable = computed(() => (store.persistedEvents.length ? store.persistedEvents : store.events));
const loadingEvents = ref(false);

const formatter = new Intl.DateTimeFormat('ko-KR', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

function formatTs(ts?: number | null) {
  if (!ts) return '-';
  return formatter.format(new Date(ts * 1000));
}

async function loadEvents() {
  if (loadingEvents.value) return;
  loadingEvents.value = true;
  try {
    await store.fetchPersistedEvents({ limit: 200 });
  } finally {
    loadingEvents.value = false;
  }
}

onMounted(async () => {
  await store.fetchInitial();
  store.connectStream();
});

onBeforeUnmount(() => {
  store.disconnectStream();
});
</script>

<style scoped>
.auto-dashboard {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
  min-height: 100vh;
  background: #0b1018;
  color: #e5ecf5;
}
.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #111927;
  border-radius: 8px;
  padding: 16px 20px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.35);
}
.top-bar .left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.top-bar h1 {
  font-size: 20px;
  margin: 0;
}
.version {
  font-size: 12px;
  color: #9aa8ba;
}
.mode {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #1d2a3a;
  letter-spacing: 0.8px;
}
.mode.paper { color: #6fa8ff; }
.mode.live { color: #ff8f6f; }
.status {
  font-weight: 600;
  padding: 2px 10px;
  border-radius: 999px;
  border: 1px solid transparent;
}
.status.on {
  color: #3ce69a;
  border-color: rgba(60, 230, 154, 0.4);
}
.status.off {
  color: #f87171;
  border-color: rgba(248, 113, 113, 0.4);
}
.top-bar .right {
  display: flex;
  gap: 12px;
  align-items: center;
  font-size: 12px;
  color: #8fa3c0;
}
.stream.ok {
  color: #3ce69a;
}
.stream.warn { color: #f0c05a; }
.content {
  display: grid;
  grid-template-columns: 1.6fr 1fr;
  gap: 12px;
}
.chart-panel {
  background: transparent;
  border-radius: 12px;
  padding: 0;
  min-height: auto;
  box-shadow: none;
}
.side-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.bottom {
  display: grid;
  grid-template-columns: 1fr 1.4fr 1fr;
  gap: 12px;
}
.logs-header { display:flex; align-items:center; justify-content: space-between; margin-bottom: 6px; font-size: 13px; color:#8fa3c0; }
.btn { background:#1b2a40; color:#e5ecf5; border:1px solid rgba(70, 96, 140, 0.35); padding:6px 10px; border-radius:6px; cursor:pointer; }
.btn[disabled] { opacity: 0.6; cursor: default; }
/* Inline row for snapshot + activity */
.inline-row { display:flex; align-items:flex-start; gap:12px; flex-wrap: wrap; }
.inline-row > *:first-child { flex: 1 1 420px; }
.inline-row > *:last-child { flex: 0 0 auto; }
@media (max-width: 1280px) {
  .content {
    grid-template-columns: 1fr;
  }
  .bottom {
    grid-template-columns: 1fr;
  }
}
</style>
