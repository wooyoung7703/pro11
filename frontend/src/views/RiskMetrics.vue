<template>
  <div class="space-y-6">
    <section class="card space-y-4">
      <div class="flex items-center justify-between">
        <h1 class="text-xl font-semibold">Risk Metrics</h1>
        <div class="flex items-center gap-3 text-xs">
          <button class="btn" :disabled="loading" @click="fetchState">Reload</button>
          <label class="flex items-center gap-1 cursor-pointer select-none">
            <input type="checkbox" v-model="auto" @change="toggleAuto()" /> Auto
          </label>
          <div class="flex items-center gap-1">
            주기 <input type="range" min="3" max="60" v-model.number="intervalSec" @change="setIntervalSec(intervalSec)" /> <span>{{ intervalSec }}s</span>
          </div>
          <span v-if="error" class="px-2 py-0.5 rounded bg-brand-danger/20 text-brand-danger">{{ error }}</span>
        </div>
      </div>
      <p class="text-xs text-neutral-400">세션 자본 상태, 드로우다운, 포지션 노출을 모니터링합니다.</p>

      <div class="grid md:grid-cols-5 gap-4 text-sm">
        <div class="p-3 rounded bg-neutral-700/40 flex flex-col justify-between">
          <div class="text-neutral-400">Current Equity</div>
          <div class="font-mono font-semibold">{{ fmt(session?.current_equity) }}</div>
        </div>
        <div class="p-3 rounded bg-neutral-700/40 flex flex-col justify-between">
          <div class="text-neutral-400">Peak Equity</div>
          <div class="font-mono font-semibold">{{ fmt(session?.peak_equity) }}</div>
        </div>
        <div class="p-3 rounded bg-neutral-700/40 flex flex-col justify-between">
          <div class="text-neutral-400 flex justify-between items-center">
            <span>Drawdown</span>
            <span v-if="drawdownPct != null" :class="ddClass(drawdownPct)" class="text-xs">
              {{ (drawdownPct*100).toFixed(1) }}%
              <template v-if="limits?.max_drawdown">
                <span class="text-neutral-500">/ {{ (limits.max_drawdown*100).toFixed(0) }}%</span>
              </template>
            </span>
          </div>
          <div class="mt-1 h-2 w-full bg-neutral-800 rounded overflow-hidden">
            <div v-if="drawdownPct != null" class="h-full bg-brand-danger transition-all" :style="{ width: ddBar }"></div>
          </div>
        </div>
        <div class="p-3 rounded bg-neutral-700/40 flex flex-col justify-between">
          <div class="text-neutral-400">PnL</div>
          <div class="font-mono font-semibold" :class="pnlClass(pnl)">{{ pnlDisplay }}</div>
        </div>
        <div class="p-3 rounded bg-neutral-700/40 flex flex-col justify-between">
          <div class="text-neutral-400 flex justify-between items-center">
            <span>Utilization</span>
            <span v-if="utilPct != null" :class="utilClass(utilPct)" class="text-xs">{{ (utilPct*100).toFixed(0) }}%</span>
          </div>
          <div class="mt-1 h-2 w-full bg-neutral-800 rounded overflow-hidden">
            <div v-if="utilPct != null" class="h-full bg-brand-accent transition-all" :style="{ width: utilBar }"></div>
          </div>
        </div>
      </div>

      <div class="grid md:grid-cols-4 gap-3 text-xs text-neutral-300">
        <div class="bg-neutral-800/40 rounded p-2 border border-neutral-700/60">
          <div class="text-neutral-500">Max Notional</div>
          <div class="font-mono">{{ limits?.max_notional ?? '—' }}</div>
        </div>
        <div class="bg-neutral-800/40 rounded p-2 border border-neutral-700/60">
          <div class="text-neutral-500">Max Daily Loss</div>
          <div class="font-mono">{{ limits?.max_daily_loss ?? '—' }}</div>
        </div>
        <div class="bg-neutral-800/40 rounded p-2 border border-neutral-700/60">
          <div class="text-neutral-500">Max Drawdown</div>
          <div class="font-mono">{{ (limits && limits.max_drawdown != null) ? (limits.max_drawdown*100).toFixed(0)+'%' : '—' }}</div>
        </div>
        <div class="bg-neutral-800/40 rounded p-2 border border-neutral-700/60">
          <div class="text-neutral-500">ATR Multiple</div>
          <div class="font-mono">{{ limits?.atr_multiple ?? '—' }}</div>
        </div>
      </div>

      <div class="flex items-end gap-6">
        <div class="flex-1">
          <h2 class="text-xs text-neutral-400 mb-1">Equity Sparkline</h2>
          <div class="bg-neutral-800/50 border border-neutral-700 rounded p-2">
            <svg v-if="sparkPath" :width="sparkW" :height="sparkH">
              <path :d="sparkPath" fill="none" stroke="#10b981" stroke-width="2" />
            </svg>
            <div v-else class="h-[30px] text-[10px] text-neutral-600 flex items-center">No data</div>
          </div>
        </div>
        <div class="text-[10px] text-neutral-500 min-w-[120px]">
          <div>points: {{ equityHistory.length }}</div>
          <div v-if="lastUpdated">updated: {{ new Date(lastUpdated).toLocaleTimeString() }}</div>
        </div>
      </div>
    </section>

    <section class="card space-y-3">
      <h2 class="text-sm font-semibold">Positions ({{ positions.length }})</h2>
      <div v-if="positions.length===0 && (pnl ?? 0) !== 0" class="text-xs text-amber-400">
        열린 포지션이 없습니다. PnL은 최근에 청산된 거래(실현 손익)에서 발생했을 수 있어요.
      </div>
      <div class="overflow-x-auto">
        <table class="min-w-full text-xs">
          <thead class="text-neutral-400 border-b border-neutral-700/60">
            <tr>
              <th class="py-1 pr-3 text-left">Symbol</th>
              <th class="py-1 pr-3 text-right">Size</th>
              <th class="py-1 pr-3 text-right">Entry</th>
              <th class="py-1 pr-3 text-right">Notional</th>
              <th class="py-1 pr-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in positions" :key="p.symbol" class="border-b border-neutral-800/40 hover:bg-neutral-800/30">
              <td class="py-1 pr-3 font-mono">{{ p.symbol }}</td>
              <td class="py-1 pr-3 font-mono" :class="p.size === 0 ? 'text-neutral-500': (p.size>0?'text-brand-accent':'text-brand-danger')">{{ p.size }}</td>
              <td class="py-1 pr-3 font-mono text-right">{{ num(p.entry_price) }}</td>
              <td class="py-1 pr-3 font-mono text-right">{{ num(Math.abs(p.size * p.entry_price)) }}</td>
              <td class="py-1 pr-3 font-mono text-right">
                <button class="btn btn-xs !bg-rose-700 hover:!bg-rose-600" :disabled="deleting===p.symbol" @click="onDelete(p.symbol)">
                  {{ deleting===p.symbol ? '...' : '삭제' }}
                </button>
              </td>
            </tr>
            <tr v-if="positions.length===0"><td colspan="5" class="py-3 text-center text-neutral-500">No positions</td></tr>
          </tbody>
        </table>
      </div>
      <div class="mt-2 flex items-center gap-3 text-xs">
        <button class="btn btn-xs" :disabled="loadingOrders" @click="onClickLoadOrders">
          {{ loadingOrders ? '불러오는 중…' : '주문 내역 보기' }}
        </button>
        <span v-if="ordersError" class="text-brand-danger">{{ ordersError }}</span>
        <span v-if="!ordersError" class="text-neutral-500">
          scope: since last reset · <span class="font-mono">{{ ohlcv.symbol }}</span>
        </span>
      </div>
      <div v-if="recentOrders.length" class="mt-2 rounded border border-neutral-700/60 overflow-x-auto">
        <table class="min-w-full text-[11px]">
          <thead class="text-neutral-400 bg-neutral-800/50">
            <tr>
              <th class="px-2 py-1 text-left">Time</th>
              <th class="px-2 py-1 text-left">Side</th>
              <th class="px-2 py-1 text-left">Symbol</th>
              <th class="px-2 py-1 text-right">Size</th>
              <th class="px-2 py-1 text-right">Price</th>
              <th class="px-2 py-1 text-left">Status</th>
              <th class="px-2 py-1 text-left">Source</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="o in recentOrders" :key="o._key" class="border-t border-neutral-800/40">
              <td class="px-2 py-1">{{ ts(o.filled_ts || o.created_ts) }}</td>
              <td class="px-2 py-1" :class="o.side==='buy' ? 'text-brand-accent' : 'text-brand-danger'">{{ (o.side||'').toUpperCase() }}</td>
              <td class="px-2 py-1">{{ o.symbol }}</td>
              <td class="px-2 py-1 text-right">{{ num(o.size) }}</td>
              <td class="px-2 py-1 text-right">{{ num(o.price) }}</td>
              <td class="px-2 py-1">{{ o.status }}</td>
              <td class="px-2 py-1">{{ ordersSource }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, computed, ref } from 'vue';
import { storeToRefs } from 'pinia';
import { useRiskStore } from '../stores/risk';
import { useOhlcvStore } from '../stores/ohlcv';
import http from '../lib/http';

const store = useRiskStore();
const ohlcv = useOhlcvStore();
const { session, positions, loading, error, lastUpdated, auto, intervalSec, drawdown, pnl, utilization, sparkPath, equityHistory, limits } = storeToRefs(store);
const { fetchState, toggleAuto, setIntervalSec } = store;

onMounted(() => {
  fetchState();
  store.startAuto();
});

const drawdownPct = computed(() => typeof drawdown.value === 'number' ? drawdown.value : null);
const utilPct = computed(() => typeof utilization.value === 'number' ? utilization.value : null);
const ddBar = computed(() => drawdownPct.value == null ? '0%' : Math.min(1, drawdownPct.value)*100 + '%');
const utilBar = computed(() => utilPct.value == null ? '0%' : Math.min(1, utilPct.value)*100 + '%');
const pnlDisplay = computed(() => pnl.value == null ? '—' : pnl.value.toFixed(2));
const sparkW = 100; const sparkH = 30;

function fmt(v: any) { return typeof v === 'number' ? v.toFixed(2) : '—'; }
function num(v: any) { return typeof v === 'number' ? v.toFixed(2) : '—'; }
function ddClass(d: number) { if (d > 0.15) return 'text-brand-danger'; if (d > 0.07) return 'text-amber-400'; return 'text-brand-accent'; }
function pnlClass(p: number | null) { if (p == null) return 'text-neutral-500'; if (p > 0) return 'text-brand-accent'; if (p < 0) return 'text-brand-danger'; return 'text-neutral-500'; }
function utilClass(u: number) { if (u > 0.8) return 'text-brand-danger'; if (u > 0.6) return 'text-amber-400'; return 'text-brand-accent'; }

// Delete position per symbol
const deleting = ref<string | null>(null);
async function onDelete(symbol: string) {
  if (!symbol) return;
  if (!confirm(`${symbol} 포지션을 삭제할까요?`)) return;
  try {
    deleting.value = symbol;
    await store.deletePosition(symbol);
  } catch {
    // error surfaced via store.error; silently ignore here
  } finally {
    deleting.value = null;
  }
}

// Recent orders (diagnostic)
interface OrderRow { id?: number|null; symbol: string; side: string; size: number; price: number; status: string; created_ts?: number; filled_ts?: number; }
const recentOrders = ref<Array<OrderRow & { _key: string }>>([]);
const loadingOrders = ref(false);
const ordersError = ref<string | null>(null);
const ordersSource = ref<'db'|'exchange'|'unknown'>('unknown');
function ts(v?: number) { return v ? new Date(v*1000).toLocaleString() : '-'; }
const onClickLoadOrders = async () => {
  const limit = 20;
  loadingOrders.value = true; ordersError.value = null;
  try {
    const r = await http.get(`/api/trading/orders`, { params: { source: 'db', limit, since_reset: true, symbol: ohlcv.symbol } });
    const data = r.data || {};
    const rows: OrderRow[] = Array.isArray(data.orders) ? data.orders : [];
    ordersSource.value = (data.source || 'db');
    recentOrders.value = rows.map((o, idx) => ({ ...o, _key: `${o.id ?? 'x'}-${idx}` }));
  } catch (e:any) {
    ordersError.value = e?.__friendlyMessage || e?.message || 'load failed';
  } finally {
    loadingOrders.value = false;
  }
}
</script>

<style scoped>
table { border-collapse: collapse; }
</style>