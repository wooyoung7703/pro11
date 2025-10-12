<template>
  <div class="space-y-6">
    <!-- Help banner -->
    <div class="px-3 py-2 text-[11px] rounded border border-neutral-700 bg-neutral-800/40 text-neutral-300">
      <details>
        <summary class="cursor-pointer">도움말</summary>
        <div class="mt-1 space-y-1">
          <div>• 요약: 자동 새로고침은 백엔드 간격을 따릅니다. 수동 갱신 시 활동 요약과 라이브/백테스트 블록이 함께 갱신됩니다.</div>
          <div>• 신뢰 구간 운영: 기준(thr) 대비 prob 차이(margin)와 히스테리시스 Δ(pp)로 경계 구간 on/off 흔들림을 완화합니다.</div>
          <div>• 성과: Live(실거래) 또는 Backtest(리플레이)를 선택할 수 있습니다. 백엔드 트레이드가 없으면 이벤트 기반 합성 거래로 대체합니다.</div>
          <div>• 캘리브레이션: ECE/Brier/ΔECE, Wilson 95% CI, ECE 기여도%/누적%로 우선순위를 확인하세요.</div>
          <a class="underline text-[11px] text-brand-accent" href="/docs/ko_ops_guide.md" target="_blank" rel="noopener">자세한 가이드 보기</a>
        </div>
      </details>
    </div>
    <!-- Summary -->
    <section class="card">
      <div class="flex items-center justify-between mb-2">
        <h1 class="text-xl font-semibold flex items-center gap-2">
          트레이딩
        </h1>
        <div class="flex items-center gap-2 text-[10px]">
          <button class="btn btn-xs" @click="onRefresh" :disabled="ta.refreshing">갱신</button>
          <span v-if="ta.refreshing" class="inline-block w-3 h-3 rounded-full border-2 border-t-transparent border-brand-accent animate-spin" />
          <label class="flex items-center gap-1 cursor-pointer select-none">
            <input type="checkbox" v-model="auto" class="accent-brand-primary" /> 자동 새로고침
          </label>
          <span v-if="displaySummary?.interval" class="text-neutral-500">loop {{ displaySummary.interval }}s</span>
        </div>
      </div>

      <!-- Interval mismatch hint -->
      <div
        v-if="intervalHint"
        class="mb-3 text-[11px] px-2 py-1 rounded border"
        :class="intervalHintMatch ? 'border-neutral-700 bg-neutral-800/40 text-neutral-300' : 'border-amber-500/30 bg-amber-500/10 text-amber-200'"
      >
        {{ intervalHint }}
      </div>

      <!-- Summary metrics -->
      <div v-if="ta.initialLoading && !displaySummary" class="text-xs text-neutral-400 animate-pulse">불러오는 중...</div>
      <div v-else-if="!displaySummary && !ta.refreshing" class="text-xs text-neutral-400">요약 데이터 없음</div>
      <div v-else class="space-y-3">
        <div class="flex items-center gap-2">
          <span class="text-sm font-semibold">Trading Activity</span>
          <StatusBadge :status="displaySummary?.auto_loop_enabled ? 'ok':'idle'">
            {{ displaySummary?.auto_loop_enabled ? '자동' : '수동' }}
          </StatusBadge>
          <StatusBadge v-show="disableReasonHuman" status="warning">비활성: {{ disableReasonHuman }}</StatusBadge>
          <span v-if="lastUpdated" class="ml-auto text-[10px] text-neutral-500">갱신 {{ new Date(lastUpdated).toLocaleTimeString() }}</span>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-6 gap-3 text-[11px]">
          <MetricCard label="최근 결정 시각" :value="lastDecisionLabel" :status="lastDecisionStatus" />
          <MetricCard label="1분 결정 수" :value="displaySummary?.decisions_1m ?? '-'" :status="decisions1mStatus" />
          <MetricCard label="5분 결정 수" :value="displaySummary?.decisions_5m ?? '-'" :status="decisions5mStatus" />
          <MetricCard label="연속 유휴" :value="idleStreakLabel" :status="idleStatus" />
          <MetricCard label="자동 루프" :value="displaySummary?.auto_loop_enabled ? 'on':'off'" :status="displaySummary?.auto_loop_enabled ? 'ok':'idle'" />
          <MetricCard label="활동 상태" :value="activityOverall" :status="activityStatus" />
        </div>
      </div>

      <details class="mt-3 text-[10px] opacity-70 max-h-48 overflow-auto" v-show="displaySummary">
        <summary class="cursor-pointer">raw summary JSON</summary>
        <pre>{{ displaySummary }}</pre>
      </details>
    </section>

    <!-- Live Trading (Backtester removed) -->
    <section class="card space-y-3">
      <!-- Header -->
      <div class="flex items-start justify-between">
        <div class="space-y-1">
          <div class="flex items-center gap-2">
            <div class="text-sm font-semibold">Live Trading</div>
            <StatusBadge :status="live.enabled ? 'ok' : 'idle'">{{ live.enabled ? '자동 주문 실행' : '비활성' }}</StatusBadge>
            <StatusBadge v-if="live.params.allow_scale_in" status="ok">추가 매수 ON</StatusBadge>
          </div>
          <div class="text-[10px] text-neutral-500" v-if="feeLabel">수수료 {{ feeLabel }}</div>
        </div>
        <div class="flex items-center gap-3 text-[11px]">
          <label class="flex items-center gap-1 select-none"><input type="checkbox" class="accent-brand-primary" :checked="live.enabled" @change="onToggleEnabled($event)" /> 자동 주문 실행</label>
          <button :disabled="live.loading" @click="onRefresh" class="px-3 py-1.5 rounded bg-brand-accent text-neutral-900 dark:text-white font-medium hover:opacity-90 disabled:opacity-50">Refresh</button>
        </div>
      </div>
      <div class="mt-1 flex items-center gap-3 text-[10px] text-neutral-500">
        <span v-if="live.params.last_trade_ts">last trade {{ new Date(live.params.last_trade_ts*1000).toLocaleTimeString() }}</span>
        <span v-if="cooldownRemainingSec > 0">쿨다운 {{ cooldownRemainingSec }}s 남음</span>
      </div>

      <!-- Scale-in Diagnostics: prominent panel -->
      <div v-if="nsb.data?.scale_in" class="p-2 rounded border border-neutral-700/60 bg-neutral-800/30 mt-2 text-[11px]">
        <div class="flex items-center justify-between mb-2">
          <div class="flex items-center gap-2">
            <div class="font-medium">추가매수 진단</div>
            <span v-if="nsb.data?.scale_in?.allow" class="px-1 py-0.5 rounded border border-emerald-700/60 bg-emerald-900/30 text-emerald-200">ON</span>
            <span v-else class="px-1 py-0.5 rounded border border-neutral-700/60 bg-neutral-900/40 text-neutral-300">OFF</span>
            <span class="px-1 py-0.5 rounded border border-neutral-600/60 text-neutral-400">gate: {{ (nsb.data?.scale_in?.gates?.gate_mode || 'or').toUpperCase() }}</span>
          </div>
          <StatusBadge :status="nsb.data?.scale_in?.ready ? 'ok' : 'idle'">{{ nsb.data?.scale_in?.ready ? 'Ready' : 'Blocked' }}</StatusBadge>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-2">
          <!-- Gates -->
          <div class="p-2 rounded bg-neutral-800/40">
            <div class="text-neutral-400 mb-1">게이트</div>
            <div class="grid grid-cols-2 gap-1">
              <div class="text-neutral-400">가격 게이트</div>
              <div :class="nsb.data?.scale_in?.gates?.gate_price_ok ? 'text-emerald-300 text-right' : 'text-rose-300 text-right'">
                {{ nsb.data?.scale_in?.gates?.gate_price_ok ? 'OK' : 'BLOCK' }}
              </div>
              <div class="text-neutral-400">Δ(권고) 게이트</div>
              <div :class="nsb.data?.scale_in?.gates?.gate_prob_ok ? 'text-emerald-300 text-right' : 'text-rose-300 text-right'">
                {{ nsb.data?.scale_in?.gates?.gate_prob_ok ? 'OK' : 'BLOCK' }}
              </div>
            </div>
            <div class="mt-1 grid grid-cols-2 gap-1 text-[10px] text-neutral-500">
              <div>min_drop</div>
              <div class="text-right text-neutral-300">{{ (nsb.data?.scale_in?.min_price_move ?? 0).toFixed ? nsb.data.scale_in.min_price_move.toFixed(3) : nsb.data?.scale_in?.min_price_move ?? '-' }}</div>
              <div>price_drop</div>
              <div class="text-right text-neutral-300">
                {{ (nsb.data?.scale_in?.gates?.price_drop ?? null) == null ? '-' : ((nsb.data.scale_in.gates.price_drop * 100).toFixed(2) + '%') }}
              </div>
              <div>Δ 요구</div>
              <div class="text-right text-neutral-300">{{ (nsb.data?.scale_in?.prob_delta_gate ?? 0).toFixed ? nsb.data.scale_in.prob_delta_gate.toFixed(3) : nsb.data?.scale_in?.prob_delta_gate ?? '-' }}</div>
              <div>Δ 현재</div>
              <div class="text-right text-neutral-300">{{ (nsb.data?.scale_in?.delta ?? null) == null ? '-' : (nsb.data.scale_in.delta).toFixed?.(3) ?? nsb.data.scale_in.delta }}</div>
            </div>
          </div>
          <!-- Prices -->
          <div class="p-2 rounded bg-neutral-800/40">
            <div class="text-neutral-400 mb-1">가격</div>
            <div class="grid grid-cols-2 gap-1">
              <div class="text-neutral-400">현재</div>
              <div class="text-right text-neutral-200">{{ Number(nsb.data?.scale_in?.gates?.current_price ?? NaN).toFixed?.(4) ?? '-' }}</div>
              <div class="text-neutral-400">엔트리</div>
              <div class="text-right text-neutral-200">{{ Number(nsb.data?.scale_in?.gates?.entry_price ?? NaN).toFixed?.(4) ?? '-' }}</div>
              <div class="text-neutral-400">앵커</div>
              <div class="text-right text-neutral-200">{{ Number(nsb.data?.scale_in?.gates?.anchor_price ?? NaN).toFixed?.(4) ?? '-' }}</div>
            </div>
          </div>
          <!-- State -->
          <div class="p-2 rounded bg-neutral-800/40">
            <div class="text-neutral-400 mb-1">상태</div>
            <div class="grid grid-cols-2 gap-1">
              <div class="text-neutral-400">레그</div>
              <div class="text-right text-neutral-200">{{ nsb.data?.scale_in?.state?.legs_used ?? 0 }}/{{ nsb.data?.scale_in?.max_legs ?? 0 }}</div>
              <div class="text-neutral-400">쿨다운</div>
              <div class="text-right text-neutral-200">{{ nsb.data?.scale_in?.state?.cooldown_remaining ?? 0 }}s</div>
              <div class="text-neutral-400">포지션</div>
              <div class="text-right text-neutral-200">{{ Number(nsb.data?.scale_in?.position_size ?? 0).toFixed?.(4) ?? '-' }}</div>
            </div>
            <div class="mt-1 text-[10px] text-neutral-500">활성 게이트: {{ (nsb.data?.scale_in?.gates?.active_gates || []).join(', ') || '-' }}</div>
          </div>
        </div>
        <div v-if="Array.isArray(nsb.data?.scale_in?.blocked_reasons) && nsb.data.scale_in.blocked_reasons.length" class="mt-2 flex flex-wrap gap-1">
          <span v-for="(r, i) in nsb.data.scale_in.blocked_reasons" :key="i" class="px-1 py-0.5 rounded border border-amber-700/50 bg-amber-900/20 text-amber-200 text-[10px]">{{ r }}</span>
        </div>
      </div>

      <!-- Exit Diagnostics: prominent panel -->
      <div v-if="nsb.data?.exit" class="p-2 rounded border border-neutral-700/60 bg-neutral-800/30 mt-2 text-[11px]">
        <div class="flex items-center justify-between mb-2">
          <div class="flex items-center gap-2">
            <div class="font-medium">청산 진단</div>
          </div>
          <StatusBadge :status="nsb.data?.exit?.ready ? 'ok' : 'idle'">{{ nsb.data?.exit?.ready ? 'Ready' : 'Blocked' }}</StatusBadge>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-2">
          <!-- Decision-based exit -->
          <div class="p-2 rounded bg-neutral-800/40">
            <div class="text-neutral-400 mb-1">결정 기반</div>
            <div class="grid grid-cols-2 gap-1">
              <div class="text-neutral-400">의사결정</div>
              <div :class="nsb.data?.exit?.triggers?.decision?.decision_ok ? 'text-emerald-300 text-right' : 'text-rose-300 text-right'">{{ nsb.data?.exit?.triggers?.decision?.decision_ok ? 'EXIT/FLAT' : 'HOLD' }}</div>
              <div class="text-neutral-400">순익 요건</div>
              <div :class="nsb.data?.exit?.triggers?.decision?.net_profit_ok ? 'text-emerald-300 text-right' : 'text-rose-300 text-right'">{{ nsb.data?.exit?.triggers?.decision?.net_profit_ok ? 'OK' : 'NOT YET' }}</div>
              <div class="text-neutral-400">쿨다운</div>
              <div :class="nsb.data?.exit?.triggers?.decision?.cooldown_ok ? 'text-emerald-300 text-right' : 'text-rose-300 text-right'">{{ nsb.data?.exit?.triggers?.decision?.cooldown_ok ? 'OK' : (nsb.data?.exit?.triggers?.decision?.cooldown_remaining ?? 0) + 's' }}</div>
              <div class="text-neutral-400">손익분기까지</div>
              <div class="text-right text-neutral-200">{{ nsb.data?.exit?.triggers?.decision?.price_to_breakeven == null ? '-' : (nsb.data.exit.triggers.decision.price_to_breakeven).toFixed?.(4) ?? nsb.data.exit.triggers.decision.price_to_breakeven }}</div>
            </div>
            <div class="mt-1 text-[10px] text-neutral-500">ready: {{ nsb.data?.exit?.triggers?.decision?.ready ? 'yes' : 'no' }}</div>
          </div>
          <!-- Trailing TP -->
          <div class="p-2 rounded bg-neutral-800/40">
            <div class="text-neutral-400 mb-1">트레일링</div>
            <div class="grid grid-cols-2 gap-1">
              <div class="text-neutral-400">설정</div>
              <div class="text-right text-neutral-200">{{ Math.floor(((nsb.data?.exit?.triggers?.trailing?.trailing_pct ?? 0) * 100)) }}%</div>
              <div class="text-neutral-400">풀백</div>
              <div class="text-right text-neutral-200">{{ (nsb.data?.exit?.triggers?.trailing?.pullback ?? null) == null ? '-' : ((nsb.data.exit.triggers.trailing.pullback * 100).toFixed?.(2) ?? '-') + '%' }}</div>
              <div class="text-neutral-400">남은 풀백</div>
              <div class="text-right text-neutral-200">{{ (nsb.data?.exit?.triggers?.trailing?.remaining ?? null) == null ? '-' : ((nsb.data.exit.triggers.trailing.remaining * 100).toFixed?.(2) ?? '-') + '%' }}</div>
            </div>
            <div class="mt-1 text-[10px] text-neutral-500">ready: {{ nsb.data?.exit?.triggers?.trailing?.ready ? 'yes' : 'no' }}</div>
          </div>
          <!-- Max holding -->
          <div class="p-2 rounded bg-neutral-800/40">
            <div class="text-neutral-400 mb-1">최대 보유</div>
            <div class="grid grid-cols-2 gap-1">
              <div class="text-neutral-400">허용</div>
              <div class="text-right text-neutral-200">{{ nsb.data?.exit?.triggers?.max_hold?.max_hold_sec ?? 0 }}s</div>
              <div class="text-neutral-400">경과</div>
              <div class="text-right text-neutral-200">{{ nsb.data?.exit?.triggers?.max_hold?.age_sec ?? '-' }}s</div>
              <div class="text-neutral-400">남은 시간</div>
              <div class="text-right text-neutral-200">{{ nsb.data?.exit?.triggers?.max_hold?.remaining_sec ?? '-' }}s</div>
            </div>
            <div class="mt-1 text-[10px] text-neutral-500">ready: {{ nsb.data?.exit?.triggers?.max_hold?.ready ? 'yes' : 'no' }}</div>
          </div>
        </div>
        <div v-if="Array.isArray(nsb.data?.exit?.blocked_reasons) && nsb.data.exit.blocked_reasons.length" class="mt-2 flex flex-wrap gap-1">
          <span v-for="(r, i) in nsb.data.exit.blocked_reasons" :key="i" class="px-1 py-0.5 rounded border border-amber-700/50 bg-amber-900/20 text-amber-200 text-[10px]">{{ r }}</span>
        </div>
      </div>

      <div class="grid md:grid-cols-3 gap-3 text-[11px]">
        <div class="space-y-2">
          <div class="text-neutral-400">Parameters</div>
          <div class="grid grid-cols-2 gap-2">
            <label class="flex items-center gap-2"><span class="w-20 text-neutral-500">base size</span>
              <input class="input w-full text-neutral-100 placeholder-neutral-500" type="number" step="0.0001" min="0" v-model.number="live.params.base_size" />
            </label>
            <label class="flex items-center gap-2"><span class="w-20 text-neutral-500">cooldown</span>
              <input class="input w-full text-neutral-100 placeholder-neutral-500" type="number" min="0" v-model.number="live.params.cooldown_sec" />
            </label>
          </div>
        </div>
        <!-- Confidence Baseline (Recommended Ops) -->
        <div class="space-y-2">
          <div class="text-neutral-400">신뢰 구간 운영</div>
          <div class="flex items-center justify-between">
            <div class="text-[10px] text-neutral-500">실행은 라이브, 기준은 프로덕션 · 라이브는 미리보기/감시</div>
            <label class="text-[10px] text-neutral-500 flex items-center gap-1">
              hysteresis Δ(pp):
              <input class="input input-xxs w-20" type="number" step="0.005" min="0" max="0.2" v-model.number="hystDelta" />
            </label>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
            <div class="p-2 rounded border border-neutral-700/60 bg-neutral-800/30">
              <div class="flex items-center gap-2 mb-1">
                <div class="font-medium">운영 기준</div>
                <StatusBadge status="ok">프로덕션 기준(실거래)</StatusBadge>
              </div>
              <div class="text-[10px] text-neutral-400">
                <div>thr <span class="text-neutral-200">{{ ciEffThreshold?.toFixed(3) ?? '-' }}</span></div>
                <div v-if="nsb.data?.threshold_override" class="text-amber-300">override 적용됨</div>
              </div>
            </div>
            <div class="p-2 rounded border border-neutral-700/60 bg-neutral-800/30">
              <div class="flex items-center gap-2 mb-1">
                <div class="font-medium">라이브 미리보기</div>
                <StatusBadge :status="ciStatus">{{ ciDecisionText }}</StatusBadge>
              </div>
              <div class="grid grid-cols-3 gap-2 text-[10px] text-neutral-400">
                <div>prob <span class="text-neutral-200">{{ ciProb?.toFixed(3) ?? '-' }}</span></div>
                <div>thr <span class="text-neutral-200">{{ ciEffThreshold?.toFixed(3) ?? '-' }}</span></div>
                <div>margin <span :class="ciMarginClass">{{ ciMarginLabel }}</span></div>
              </div>
              <div v-if="ciBorderline" class="mt-1 text-[10px] text-amber-300">경계 구간: 잦은 on/off 예방에 유의</div>
              <div v-if="nsb.data?.inference_symbol_mismatch" class="mt-1 text-[10px] text-rose-300">심볼/피드 불일치 감지</div>
            </div>
          </div>
          <!-- Ops summary table -->
          <div class="mt-2 overflow-x-auto">
            <div class="text-[10px] text-neutral-500 mb-1" v-if="inferenceSymbol">
              symbol: <span class="text-neutral-300">{{ inferenceSymbol }}</span>
            </div>
            <table class="w-full text-[10px] border-collapse">
              <thead class="text-neutral-400">
                <tr>
                  <th class="text-left font-medium p-1 border-b border-neutral-700/60">기준</th>
                  <th class="text-left font-medium p-1 border-b border-neutral-700/60">thr</th>
                  <th class="text-left font-medium p-1 border-b border-neutral-700/60">override</th>
                  <th class="text-left font-medium p-1 border-b border-neutral-700/60">thr_in</th>
                  <th class="text-left font-medium p-1 border-b border-neutral-700/60">thr_out</th>
                  <th class="text-left font-medium p-1 border-b border-neutral-700/60">추가매수 비율</th>
                  <th class="text-left font-medium p-1 border-b border-neutral-700/60">최대 레그</th>
                  <th class="text-left font-medium p-1 border-b border-neutral-700/60">추가매수 쿨다운(s)</th>
                  <th class="text-left font-medium p-1 border-b border-neutral-700/60">최소 가격 이동</th>
                  <th class="text-left font-medium p-1 border-b border-neutral-700/60">수수료</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td class="p-1 text-neutral-300">프로덕션</td>
                  <td class="p-1">{{ ciEffThreshold?.toFixed(3) ?? '-' }}</td>
                  <td class="p-1">{{ nsb.data?.threshold_override != null ? nsb.data.threshold_override.toFixed?.(3) ?? nsb.data.threshold_override : '-' }}</td>
                  <td class="p-1">{{ thrInLabel }}</td>
                  <td class="p-1">{{ thrOutLabel }}</td>
                  <td class="p-1">{{ scaleInRatioLabel }}</td>
                  <td class="p-1">{{ (live.params?.scale_in_max_legs ?? '-') }}</td>
                  <td class="p-1">{{ (live.params?.scale_in_cooldown_sec ?? '-') }}</td>
                  <td class="p-1">{{ (live.params?.scale_in_min_price_move ?? '-') }}</td>
                  <td class="p-1">{{ feeLabel }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <!-- Performance Metrics -->
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <div class="text-neutral-400">Performance</div>
              <StatusBadge :status="perfSource === 'live' ? 'ok' : 'idle'" title="Live: 실거래 지표 / Backtest: 리플레이 요약">Data Source: {{ perfSource === 'live' ? 'Live' : 'Backtest' }}</StatusBadge>
            </div>
            <label class="text-[10px] text-neutral-500 flex items-center gap-1">
              Source:
              <select v-model="perfSource" class="input input-xs bg-neutral-900 text-neutral-200">
                <option value="live">Live (Exchange)</option>
                <option value="backtest">Backtest (Replay)</option>
              </select>
            </label>
          </div>
          <div v-if="perfSource === 'backtest'" class="grid grid-cols-1 md:grid-cols-6 gap-2 text-[10px] items-center">
            <label class="flex items-center gap-2 md:col-span-2"><span class="w-20 text-neutral-500">From</span>
              <input class="input w-full" type="datetime-local" v-model="btFrom" @change="onBacktestParamsChange" />
            </label>
            <label class="flex items-center gap-2 md:col-span-2"><span class="w-20 text-neutral-500">To</span>
              <input class="input w-full" type="datetime-local" v-model="btTo" @change="onBacktestParamsChange" />
            </label>
            <label class="flex items-center gap-2"><span class="w-24 text-neutral-500">Start Equity</span>
              <input class="input w-full" type="number" min="0" step="1" v-model.number="btStartEquity" />
            </label>
            <label class="flex items-center gap-2"><span class="w-16 text-neutral-500">Symbol</span>
              <input class="input w-full" type="text" placeholder="e.g. BTCUSDT" v-model="btSymbol" @change="onBacktestParamsChange" />
            </label>
            <div class="flex items-center gap-2 text-[10px] text-neutral-500">
              <span>Preset:</span>
              <button class="btn btn-xxs" @click="setBtRangeDays(7)">7D</button>
              <button class="btn btn-xxs" @click="setBtRangeDays(30)">30D</button>
              <button class="btn btn-xxs" @click="setBtRangeDays(90)">90D</button>
            </div>
          </div>
          <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
            <MetricCard label="ROI" :value="roiLabel" :status="roiStatus" />
            <MetricCard label="누적 PnL" :value="cumPnlLabel" :status="cumPnlStatus" />
            <MetricCard label="최대 낙폭" :value="ddLabel" :status="ddStatus" />
            <MetricCard label="미실현 PnL" :value="openPnlLabel" :status="openPnlStatus" />
          </div>
          <div v-if="perfSource === 'backtest'" class="text-[10px] text-neutral-500 flex items-center gap-3">
            <span v-if="btWindowFrom || btWindowTo">window: {{ btWindowFrom || '-' }} → {{ btWindowTo || '-' }}</span>
            <span v-if="btNoData" class="text-amber-300">선택 기간에 체결 주문/트레이드가 없습니다</span>
            <span v-else-if="btUsingSynthetic" class="text-amber-200">백엔드 트레이드 없음 → 이벤트로 재구성한 성과 사용</span>
          </div>
          <details v-if="perfSource === 'backtest' && bt.data" class="text-[10px] opacity-70">
            <summary class="cursor-pointer">backtest raw JSON</summary>
            <pre>{{ bt.data }}</pre>
          </details>
        </div>
      </div>

      <div v-if="noTradeHint" class="text-[11px] text-amber-200 bg-amber-500/10 border border-amber-500/30 px-2 py-1 rounded">{{ noTradeHint }}</div>
      <details class="text-[10px] opacity-70" v-show="nsb.data">
        <summary class="cursor-pointer">no-signal breakdown</summary>
        <pre>{{ nsb.data }}</pre>
      </details>

      <!-- Live Positions & Orders -->
      <div class="grid md:grid-cols-2 gap-3 mt-2">
        <!-- Positions -->
        <div class="p-2 rounded border border-neutral-700/60 bg-neutral-800/30">
          <div class="flex items-center justify-between mb-2">
            <div class="font-medium">Positions</div>
            <div class="text-[10px] text-neutral-500" v-if="live.positions?.length">{{ live.positions.length }}개</div>
          </div>
          <div v-if="!live.positions || live.positions.length === 0" class="text-[10px] text-neutral-500">포지션 없음</div>
          <table v-else class="w-full text-[10px] border-collapse">
            <thead class="text-neutral-400">
              <tr>
                <th class="text-left font-medium p-1 border-b border-neutral-700/60">심볼</th>
                <th class="text-right font-medium p-1 border-b border-neutral-700/60">수량</th>
                <th class="text-right font-medium p-1 border-b border-neutral-700/60">평단</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(p, i) in live.positions" :key="i">
                <td class="p-1 text-neutral-300">{{ p.symbol }}</td>
                <td class="p-1 text-right text-neutral-200">{{ Number(p.size).toFixed(4) }}</td>
                <td class="p-1 text-right text-neutral-200">{{ Number(p.entry_price).toFixed(4) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Orders -->
        <div class="p-2 rounded border border-neutral-700/60 bg-neutral-800/30">
          <div class="flex items-center justify-between mb-2">
            <div class="font-medium">Orders</div>
            <div class="text-[10px] text-neutral-500 flex items-center gap-2">
              <span v-if="live.orders?.length">{{ live.orders.length }}개</span>
              <span v-if="live.ordersSource" class="px-1 py-0.5 rounded border border-neutral-600/60 text-neutral-400">{{ live.ordersSource }}</span>
            </div>
          </div>
          <div v-if="!live.orders || live.orders.length === 0" class="text-[10px] text-neutral-500">주문 없음</div>
          <table v-else class="w-full text-[10px] border-collapse">
            <thead class="text-neutral-400">
              <tr>
                <th class="text-left font-medium p-1 border-b border-neutral-700/60">시간</th>
                <th class="text-left font-medium p-1 border-b border-neutral-700/60">심볼</th>
                <th class="text-left font-medium p-1 border-b border-neutral-700/60">사이드</th>
                <th class="text-right font-medium p-1 border-b border-neutral-700/60">수량</th>
                <th class="text-right font-medium p-1 border-b border-neutral-700/60">가격</th>
                <th class="text-left font-medium p-1 border-b border-neutral-700/60">상태</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(o, i) in ordersLimited" :key="i">
                <td class="p-1 text-neutral-300">{{ fmtOrderTime(o) }}</td>
                <td class="p-1 text-neutral-300">{{ o.symbol }}</td>
                <td class="p-1" :class="o.side === 'buy' ? 'text-emerald-300' : 'text-rose-300'">{{ o.side }}</td>
                <td class="p-1 text-right text-neutral-200">{{ Number(o.size).toFixed(4) }}</td>
                <td class="p-1 text-right text-neutral-200">{{ Number(o.price).toFixed(4) }}</td>
                <td class="p-1 text-neutral-300">{{ o.status }}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="live.orders && live.orders.length > 20" class="mt-1 text-[10px] text-neutral-500">최근 20개만 표시</div>
        </div>
      </div>
    </section>

    <!-- Calibration & Monitor (optional) -->
    <section class="card space-y-3" v-if="calib.show">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2">
          <div class="text-sm font-semibold">라이브 vs 프로덕션</div>
          <span v-if="calib.sampleCount != null" :class="['px-1.5 py-0.5 rounded border text-[10px]', nBadgeClass]">N={{ calib.sampleCount }}</span>
        </div>
        <div class="flex items-center gap-3">
          <div class="text-[10px] text-neutral-500" v-if="calib.winFrom || calib.winTo">
            window(UTC): {{ calib.winFrom || '-' }} → {{ calib.winTo || '-' }}
          </div>
          <button class="btn btn-xxs" @click="copyCalibReport">Copy report</button>
          <button class="btn btn-xxs" @click="calibOpen = !calibOpen">{{ calibOpen ? '접기' : '펼치기' }}</button>
        </div>
      </div>
      <div v-show="calibOpen">
        <div class="grid md:grid-cols-3 gap-3 text-[11px]">
          <!-- Metrics -->
          <div class="p-2 rounded border border-neutral-700/60 bg-neutral-800/30">
            <div class="grid grid-cols-2 gap-1 text-[10px]">
              <div class="text-neutral-400" title="낮을수록 좋음">프로덕션 ECE (↓)</div><div class="text-neutral-200 text-right">{{ calib.prodECE }}</div>
              <div class="text-neutral-400" title="낮을수록 좋음">라이브 ECE (↓)</div><div class="text-neutral-200 text-right" :class="calib.eceWorse ? 'text-rose-300':'text-emerald-300'">{{ calib.liveECE }}</div>
              <div class="text-neutral-400">Δ ECE</div><div class="text-neutral-200 text-right" :class="calib.eceWorse ? 'text-rose-300':'text-emerald-300'">{{ calib.deltaECE }}</div>
              <div class="text-neutral-400" title="낮을수록 좋음">Live Brier (↓)</div><div class="text-neutral-200 text-right">{{ calib.liveBrier }}</div>
              <div class="text-neutral-400" title="낮을수록 좋음">Prod Brier (↓)</div><div class="text-neutral-200 text-right">{{ calib.prodBrier }}</div>
            </div>
            <div class="mt-2 h-2 rounded bg-neutral-700/50 overflow-hidden">
              <div class="h-full bg-emerald-500/70" :style="{ width: calib.liveECEBarWidth }"></div>
            </div>
            <div class="mt-1 text-[10px] text-neutral-500">막대 = 라이브 ECE (0~0.2 스케일 제한)</div>
          </div>

          <!-- Monitor -->
          <div class="p-2 rounded border border-neutral-700/60 bg-neutral-800/30">
            <div class="flex items-center justify-between mb-2">
              <div class="font-medium">모니터</div>
              <StatusBadge v-if="calib.retrainRecommended" status="warning">재학습 추천</StatusBadge>
            </div>
            <div class="grid grid-cols-2 gap-2 text-[10px]">
              <div class="p-2 rounded bg-neutral-800/60">
                <div class="text-neutral-400">절대 편차 연속</div>
                <div class="text-right text-neutral-200">{{ calib.absStreak ?? '-' }}</div>
              </div>
              <div class="p-2 rounded bg-neutral-800/60">
                <div class="text-neutral-400">상대 편차 연속</div>
                <div class="text-right text-neutral-200">{{ calib.relStreak ?? '-' }}</div>
              </div>
            </div>
            <div class="mt-2 h-2 rounded bg-neutral-700/50 overflow-hidden">
              <div class="h-full bg-rose-500/70" :style="{ width: calib.deltaBarWidth }"></div>
            </div>
            <ul class="mt-2 list-disc list-inside text-[10px] text-neutral-300" v-if="calib.reasons && calib.reasons.length">
              <li v-for="(r, i) in calib.reasons" :key="i">{{ r }}</li>
            </ul>
            <div class="mt-1 text-[10px] text-neutral-500">샘플수: {{ calib.sampleCount ?? '-' }}</div>
          </div>

          <!-- Top Drift Z placeholder -->
          <div class="p-2 rounded border border-neutral-700/60 bg-neutral-800/30">
            <div class="flex items-center justify-between mb-2"><div class="font-medium">상위 드리프트 Z</div><div class="text-neutral-500">드리프트</div></div>
            <div class="text-[10px] text-neutral-500">데이터 없음</div>
          </div>
          <!-- Reliability plot (conf vs acc) -->
          <div class="p-2 rounded border border-neutral-700/60 bg-neutral-800/30">
            <div class="flex items-center justify-between mb-2">
              <div class="font-medium">리라이어빌리티 플롯</div>
              <div class="text-[10px] text-neutral-500">점 크기=N, 색상=격차(|conf−acc|)</div>
            </div>
            <div class="overflow-x-auto">
              <svg :width="relPlotW" :height="relPlotH" :viewBox="`0 0 ${relPlotW} ${relPlotH}`" class="block">
                <defs>
                  <clipPath id="relClip">
                    <rect :x="relPad" :y="relPad" :width="relInnerW" :height="relInnerH" rx="2" ry="2" />
                  </clipPath>
                </defs>
                <!-- axes -->
                <line :x1="relPad" :y1="relPad" :x2="relPad" :y2="relPad+relInnerH" stroke="#404040" stroke-width="1" />
                <line :x1="relPad" :y1="relPad+relInnerH" :x2="relPad+relInnerW" :y2="relPad+relInnerH" stroke="#404040" stroke-width="1" />
                <!-- y=x reference -->
                <line :x1="relPad" :y1="relPad+relInnerH" :x2="relPad+relInnerW" :y2="relPad" stroke="#6b7280" stroke-dasharray="3,3" stroke-width="1" />
                <!-- ticks (0, 0.5, 1) minimal) -->
                <g class="text-[8px] fill-neutral-400">
                  <text :x="relPad-6" :y="relPad+relInnerH+4" text-anchor="end">0.0</text>
                  <text :x="relPad-6" :y="relPad+relInnerH/2+3" text-anchor="end">0.5</text>
                  <text :x="relPad-6" :y="relPad+3" text-anchor="end">1.0</text>
                  <text :x="relPad" :y="relPad+relInnerH+12" text-anchor="middle">0.0</text>
                  <text :x="relPad+relInnerW/2" :y="relPad+relInnerH+12" text-anchor="middle">0.5</text>
                  <text :x="relPad+relInnerW" :y="relPad+relInnerH+12" text-anchor="middle">1.0</text>
                </g>
                <!-- points -->
                <g clip-path="url(#relClip)">
                  <circle v-for="(p, i) in relPoints" :key="i" :cx="relPad + p.x * relInnerW" :cy="relPad + (1 - p.y) * relInnerH" :r="p.r" :fill="p.color" fill-opacity="0.9">
                    <title>{{ p.title }}</title>
                  </circle>
                </g>
                <g class="text-[8px] fill-neutral-400">
                  <text :x="relPad+relInnerW/2" :y="relPad+relInnerH+22" text-anchor="middle">conf</text>
                  <text :x="relPad-12" :y="relPad+relInnerH/2" text-anchor="middle" transform="rotate(-90, \n                    ${relPad-12}, ${relPad+relInnerH/2})">acc</text>
                </g>
              </svg>
            </div>
            <div v-if="!relPoints.length" class="mt-1 text-[10px] text-neutral-500">데이터 없음</div>
          </div>
          <!-- Calibration bins breakdown -->
          <div class="md:col-span-3 p-2 rounded border border-neutral-700/60 bg-neutral-800/30">
            <div class="flex items-center justify-between mb-2">
              <div class="font-medium">신뢰 구간(Bin) 브레이크다운</div>
              <div class="text-[10px] text-neutral-500">기여도 = (N/N전체)·|conf−acc| · CI=Wilson 95%</div>
            </div>
            <div class="overflow-x-auto">
              <table class="w-full text-[10px] border-collapse">
                <thead class="text-neutral-400">
                  <tr>
                    <th class="text-left font-medium p-1 border-b border-neutral-700/60 cursor-pointer select-none" title="예측 확률 범위(예: 0.6–0.7)" @click="toggleSort('range')">
                      구간 <span v-if="isSort('range')">{{ sortDir === 'asc' ? '▲' : '▼' }}</span>
                    </th>
                    <th class="text-right font-medium p-1 border-b border-neutral-700/60 cursor-pointer select-none" title="예측 확률의 평균" @click="toggleSort('conf')">
                      평균(conf) <span v-if="isSort('conf')">{{ sortDir === 'asc' ? '▲' : '▼' }}</span>
                    </th>
                    <th class="text-right font-medium p-1 border-b border-neutral-700/60 cursor-pointer select-none" title="실제 정답 비율" @click="toggleSort('acc')">
                      경험(acc) <span v-if="isSort('acc')">{{ sortDir === 'asc' ? '▲' : '▼' }}</span>
                    </th>
                    <th class="text-right font-medium p-1 border-b border-neutral-700/60 cursor-pointer select-none" title="정확(acc)의 95% 윌슨 신뢰구간 폭" @click="toggleSort('ciw')">
                      CI(95%) <span v-if="isSort('ciw')">{{ sortDir === 'asc' ? '▲' : '▼' }}</span>
                    </th>
                    <th class="text-right font-medium p-1 border-b border-neutral-700/60 cursor-pointer select-none" title="|conf−acc|, 보정 필요 크기" @click="toggleSort('gap')">
                      격차 <span v-if="isSort('gap')">{{ sortDir === 'asc' ? '▲' : '▼' }}</span>
                    </th>
                    <th class="text-right font-medium p-1 border-b border-neutral-700/60 cursor-pointer select-none" title="표본 수" @click="toggleSort('n')">
                      N <span v-if="isSort('n')">{{ sortDir === 'asc' ? '▲' : '▼' }}</span>
                    </th>
                    <th class="text-right font-medium p-1 border-b border-neutral-700/60 cursor-pointer select-none" title="(N/N전체)×|conf−acc|" @click="toggleSort('contrib')">
                      기여도(ECE) <span v-if="isSort('contrib')">{{ sortDir === 'asc' ? '▲' : '▼' }}</span>
                    </th>
                    <th class="text-right font-medium p-1 border-b border-neutral-700/60">기여도%</th>
                    <th class="text-right font-medium p-1 border-b border-neutral-700/60">누적%</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(r, i) in sortedCalibBins" :key="i" :class="r.n < 20 ? 'opacity-60' : ''">
                    <td class="p-1 text-neutral-300">{{ r.range }}</td>
                    <td class="p-1 text-right text-neutral-200">{{ r.conf.toFixed(3) }}</td>
                    <td class="p-1 text-right text-neutral-200">{{ r.acc.toFixed(3) }}</td>
                    <td class="p-1 text-right text-neutral-400">{{ r.ciLow == null ? '-' : r.ciLow.toFixed(3) }}–{{ r.ciHigh == null ? '-' : r.ciHigh.toFixed(3) }}</td>
                    <td class="p-1 text-right" :class="r.gap > 0.05 ? 'text-rose-300' : 'text-neutral-200'">{{ r.gap.toFixed(3) }}</td>
                    <td class="p-1 text-right text-neutral-200">{{ r.n }}</td>
                    <td class="p-1 text-right text-neutral-200">{{ r.contrib.toFixed(4) }}</td>
                    <td class="p-1 text-right text-neutral-200">{{ r.contribPct == null ? '-' : (r.contribPct * 100).toFixed(1) }}%</td>
                    <td class="p-1 text-right text-neutral-200">{{ r.cumPct == null ? '-' : (r.cumPct * 100).toFixed(1) }}%</td>
                  </tr>
                  <tr v-if="!sortedCalibBins.length">
                    <td class="p-2 text-neutral-500" colspan="9">데이터 없음</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="mt-1 text-[10px] text-neutral-500">
              안내: 컬럼 헤더를 클릭하면 정렬됩니다. 기여도(ECE)=(N/N전체)×|conf−acc|, CI는 Wilson 95%입니다. 기여도%는 각 bin의 ECE 비율, 누적%는 현재 정렬 기준 상단부터의 누적 비율입니다.
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount, watch } from 'vue';
import StatusBadge from '../components/StatusBadge.vue';
import MetricCard from '../components/MetricCard.vue';
import { useTradingActivityStore } from '../stores/tradingActivity';
// removed unused stores and composables
// removed unused validation imports
import { classifyDecisionAge, classifyDecisionThroughput, humanDisableReason } from '../utils/status';
import http from '../lib/http';

const ta = useTradingActivityStore();
const auto = ref(true);
const lastUpdated = ref<number | null>(null);
// risk store not used in simplified view
// Performance source selector: 'live' | 'backtest'
const perfSource = ref<'live' | 'backtest'>('live');
// Backtest window controls and starting equity for ROI/MaxDD
const btFrom = ref<string>(''); // datetime-local string
const btTo = ref<string>('');   // datetime-local string
const btStartEquity = ref<number>(1000);
const btSymbol = ref<string>('');

// UI state: calibration section collapse
const CALIB_OPEN_KEY = 'calibration_open_state';
const calibOpen = ref<boolean>( (() => { try { const v = localStorage.getItem(CALIB_OPEN_KEY); return v == null ? true : v === '1'; } catch { return true; } })() );
watch(calibOpen, (v) => { try { localStorage.setItem(CALIB_OPEN_KEY, v ? '1' : '0'); } catch {} });

// Connection badges removed (SSE status badges not displayed)

// ---------- Reactive state (restored) ----------
// Live trading reactive model
const live = ref({
  loading: false,
  saving: false,
  enabled: false,
  params: {
    base_size: 1,
    cooldown_sec: 60,
    last_trade_ts: null as number | null,
    // scale-in
  allow_scale_in: true,
    scale_in_size_ratio: 0.5,
    scale_in_max_legs: 3,
    scale_in_min_price_move: 0,
    
    scale_in_cooldown_sec: 0,
    // policy/guards (server-provided)
    scale_in_freeze_on_exit: false as boolean,
    exit_slice_seconds: 0 as number,
  },
  equity: {
    starting: null as number | null,
    current: null as number | null,
    peak: null as number | null,
    cumulative_pnl: null as number | null,
  },
  positions: [] as any[],
  orders: [] as any[],
  ordersSource: 'db' as 'exchange'|'db',
});

// Fee label and helpers
const feeLabel = computed(() => {
  const mode = String((live.value.params as any)?.fee_mode || '').toLowerCase();
  const taker = Number((live.value.params as any)?.fee_taker ?? 0.001);
  const maker = Number((live.value.params as any)?.fee_maker ?? 0.001);
  if (mode === 'maker') return `maker ${(maker*100).toFixed(2)}%`;
  return `taker ${(taker*100).toFixed(2)}%`;
});

// Advanced (Scale-In) tooltip summary removed in simplified header
// Break-even and +1% ROI target price (including fees)
// removed bePrice (live progress removed)
// removed r1Price (live progress removed)
// removed remainTextTo (live progress removed)
// removed live progress BE/R1 remain texts UI
// Orders table helpers removed (orders table UI not shown here)

// removed exitTargetPx (live progress removed)
// removed exitDisabled (live progress removed)
// removed exit progress (live progress removed)
// removed exit donut dash/label (live progress UI removed)

// Orders table helpers used in template
// Removed scale-in helpers tied to the orders table display

// Persist drafts locally so manual refresh keeps edits until Save (disabled for now)

// Backtester removed

// No-signal breakdown + recommendation state
const nsb = ref<{ loading: boolean; data: any | null }>({ loading: false, data: null });

// ---------- Header/summary computed ----------
const displaySummary = computed(() => ta.summary);
const lastDecisionLabel = computed(() => {
  const d = ta.lastDecisionDate;
  return d ? d.toLocaleString() : '-';
});
const lastDecisionStatus = computed(() => classifyDecisionAge(displaySummary.value?.last_decision_ts ? Math.max(0, Math.floor(Date.now() / 1000 - (displaySummary.value!.last_decision_ts || 0))) : null));
const decisions1mStatus = computed(() => classifyDecisionThroughput(displaySummary.value?.decisions_1m ?? null));
const decisions5mStatus = computed(() => classifyDecisionThroughput(displaySummary.value?.decisions_5m ?? null));
const idleStreakLabel = computed(() => `${Math.round(ta.idleStreakMinutes)}m`);
const idleStatus = computed(() => (ta.idleStreakMinutes > 0 ? 'idle' : 'ok'));
const activityOverall = computed(() => (ta.isIdle ? 'idle' : 'active'));
const activityStatus = computed(() => (ta.isIdle ? 'idle' : 'ok'));

// Backend vs env interval hint
const intervalHintInfo = computed(() => ta.compareIntervalWithEnv());
const intervalHintMatch = computed(() => intervalHintInfo.value.match);
const intervalHint = computed(() => {
  const i = intervalHintInfo.value;
  if (i.match == null) return '';
  return i.match ? `backend interval=${i.backend}s (env 일치)` : `backend interval=${i.backend}s, env=${i.env}s (불일치)`;
});

// Disable reason (if backend provides one in summary)
const disableReasonHuman = computed(() => humanDisableReason((displaySummary.value as any)?.disable_reason));

// Inference/mismatch badges removed from the header in simplified view

// Orders source preference and handlers
const ORDERS_SRC_KEY = 'ordersSourcePref';
const ordersSourcePref = ref<'auto'|'exchange'|'db'>( (() => {
  try { const v = localStorage.getItem(ORDERS_SRC_KEY) as any; if (v === 'exchange' || v === 'db') return v; } catch {}
  return 'auto';
})() );
// Orders source selector not shown in simplified UI
function onRefresh() {
  // Fetch activity summary to update header metrics and clear initial loading state
  ta.fetchSummary();
  fetchLiveStatus(ordersSourcePref.value);
  fetchNoSignalBreakdown();
  if (perfSource.value === 'backtest') fetchBacktestReplay();
}

// Auto-refresh loop per recommended ops: use backend interval, fallback 10s
const refreshTimer = ref<ReturnType<typeof setInterval> | null>(null);
function stopAutoLoop() {
  if (refreshTimer.value) {
    clearInterval(refreshTimer.value as any);
    refreshTimer.value = null;
  }
}
function startAutoLoop() {
  stopAutoLoop();
  if (!auto.value) return;
  const sec = Number(displaySummary.value?.interval ?? 10);
  const periodMs = Math.max(2, isFinite(sec) ? sec : 10) * 1000;
  refreshTimer.value = setInterval(() => {
    onRefresh();
  }, periodMs);
}

// React to toggle and interval changes
watch([auto, () => displaySummary.value?.interval], () => {
  startAutoLoop();
});

// Fetch initial data and start loop
onMounted(() => {
  // If backtest window is empty, set default 1-month range before first refresh
  if (!btFrom.value && !btTo.value) {
    const now = new Date();
    const monthAgo = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
    btFrom.value = formatDateLocal(monthAgo);
    btTo.value = formatDateLocal(now);
  }
  onRefresh();
  startAutoLoop();
});

onBeforeUnmount(() => {
  stopAutoLoop();
});

/*
const nsbRiskLabel = computed(() => {
   const r = (nsb.value.data as any)?.risk;
   if (!r) return '';
   if (r.blocked_reason) return String(r.blocked_reason);
   if (r.blocked_reasons && Array.isArray(r.blocked_reasons) && r.blocked_reasons.length)
     return r.blocked_reasons.join(',');
   return '';
});
*/

// Restore missing function declaration
function buildNoTradeHint(): string | null {
  const enabled = live.value.enabled;
  const hasOrders = (live.value.orders?.length || 0) > 0;
  if (hasOrders) return null;
  const parts: string[] = [];
  if (!enabled) parts.push('라이브 트레이딩이 비활성화되어 있습니다. 우측 상단 enable을 켜세요.');
  if (live.value.params?.last_trade_ts) {
    const delta = (Date.now()/1000) - (live.value.params.last_trade_ts || 0);
    if (delta < (live.value.params.cooldown_sec || 0)) parts.push(`쿨다운 적용 중 (${Math.ceil((live.value.params.cooldown_sec - delta))}초 남음).`);
  }
  return parts.length ? parts.join(' ') : null;
}

// Restore helpers removed when refactoring
// statusClass not used in simplified UI
async function setEnabled(enabled: boolean) {
  try {
    await http.post('/api/trading/live/enable', null, { params: { enabled } });
    live.value.enabled = enabled;
  } catch {}
}
function onToggleEnabled(ev: Event) {
  const target = ev.target as HTMLInputElement;
  setEnabled(!!target.checked);
}
// saveParams removed (no save button in simplified UI)
// removed unused fmtOrderTime and fmtTs (Backtester removed)
// Manual order helpers and test actions removed in simplified UI
// Recommendation fetch removed (not used in simplified UI)
// ---------- Scale-in diagnostics removed in simplified view ----------

// Ready/blocked reasons computed from backend (with safe fallback)
// Readiness and blocked reasons badges removed

async function fetchNoSignalBreakdown() {
  try {
    nsb.value.loading = true;
    const r = await http.get('/api/trading/no_signal_breakdown');
    const incoming = r.data || null;
    if (incoming == null) {
      // keep prior data to avoid DOM thrash
      nsb.value.data = nsb.value.data || null;
    } else {
      if (!nsb.value.data) nsb.value.data = {} as any;
      const t: any = nsb.value.data;
      t.live_enabled = incoming.live_enabled;
      t.params = incoming.params;
      t.cooldown_remaining_sec = incoming.cooldown_remaining_sec;
      t.latest_inference = incoming.latest_inference;
      t.threshold_config = incoming.threshold_config;
      t.threshold_override = incoming.threshold_override;
      t.inference_symbol_mismatch = incoming.inference_symbol_mismatch || incoming.mismatch_hint;
      t.risk = incoming.risk;
        t.scale_in = incoming.scale_in;
        t.exit = incoming.exit;
    }
  } catch {
    // ignore
  } finally { nsb.value.loading = false; }
}

// ---------- CI (Confidence) preview & baseline (recommended ops) ----------
const ciEffThreshold = computed<number | null>(() => {
  const o = (nsb.value.data as any)?.threshold_override;
  if (typeof o === 'number' && isFinite(o)) return o;
  const thr = (nsb.value.data as any)?.latest_inference?.threshold;
  return (typeof thr === 'number' && isFinite(thr)) ? thr : null;
});
const ciProb = computed<number | null>(() => {
  const p = (nsb.value.data as any)?.latest_inference?.probability;
  return (typeof p === 'number' && isFinite(p)) ? p : null;
});
const ciMargin = computed<number | null>(() => {
  if (ciProb.value == null || ciEffThreshold.value == null) return null;
  return ciProb.value - ciEffThreshold.value;
});
const ciBorderline = computed<boolean>(() => {
  if (ciMargin.value == null) return false;
  return Math.abs(ciMargin.value) < 0.02; // ±2pp
});
const ciDecisionText = computed<string>(() => {
  if (ciProb.value == null || ciEffThreshold.value == null) return '데이터 없음';
  return ciProb.value >= ciEffThreshold.value ? '통과(권고)' : '보류';
});
const ciStatus = computed<'ok'|'warning'|'idle'|'error'>(() => {
  if (ciProb.value == null || ciEffThreshold.value == null) return 'idle';
  if ((ciMargin.value ?? 0) < 0) return 'error';
  if (ciBorderline.value) return 'warning';
  return 'ok';
});
const ciMarginLabel = computed<string>(() => {
  if (ciMargin.value == null) return '-';
  const v = ciMargin.value;
  return (v >= 0 ? '+' : '') + v.toFixed(3);
});
const ciMarginClass = computed<string>(() => {
  if (ciMargin.value == null) return 'text-neutral-200';
  if (ciMargin.value < 0) return 'text-rose-300';
  if (ciBorderline.value) return 'text-amber-200';
  return 'text-emerald-300';
});

// ---- Ops summary labels (symbol, hysteresis, scale-in formatting) ----
const inferenceSymbol = computed<string | null>(() => {
  return ((nsb.value.data as any)?.latest_inference?.symbol) || null;
});
// Hysteresis delta (pp) with local persistence
const HYST_KEY = 'ops_hysteresis_delta_pp';
const hystDelta = ref<number>( (() => { try { const v = Number(localStorage.getItem(HYST_KEY)); return Number.isFinite(v) && v >= 0 ? v : 0.02; } catch { return 0.02; } })() );
watch(hystDelta, (v) => { try { localStorage.setItem(HYST_KEY, String(v)); } catch {} });
const thrInLabel = computed<string>(() => {
  const v = ciEffThreshold.value;
  return v == null ? '-' : v.toFixed(3);
});
const thrOutLabel = computed<string>(() => {
  const v = ciEffThreshold.value;
  if (v == null) return '-';
  const delta = Number.isFinite(hystDelta.value) ? hystDelta.value : 0.02;
  const out = Math.max(0, v - delta);
  return out.toFixed(3);
});
const scaleInRatioLabel = computed<string>(() => {
  const r = Number(live.value.params?.scale_in_size_ratio);
  return Number.isFinite(r) ? `${r.toFixed(2)}x` : '-';
});

// Sample size severity class
const nBadgeClass = computed<string>(() => {
  const n = Number(calib.value.sampleCount ?? 0);
  if (n >= 1000) return 'bg-emerald-900/40 border-emerald-700/60 text-emerald-200';
  if (n >= 200) return 'bg-amber-900/20 border-amber-700/50 text-amber-200';
  return 'bg-neutral-800/60 border-neutral-700/60 text-neutral-300';
});

// ---- Calibration & Monitor (safe, optional) ----
const calib = computed(() => {
  const data: any = nsb.value.data || {};
  const c: any = data.calibration || {};
  const m: any = data.monitor || {};
  const num = (x: any): number | null => { const v = Number(x); return Number.isFinite(v) ? v : null; };
  const int = (x: any): number | null => { const v = Number(x); return Number.isFinite(v) ? Math.trunc(v) : null; };
  const fmt = (x: number | null, d = 4) => x == null ? '-' : x.toFixed(d);
  const clamp01 = (x: number) => Math.max(0, Math.min(1, x));

  const prodECE_n = num(c.prod_ece ?? c.production_ece);
  const liveECE_n = num(c.live_ece);
  const deltaECE_n = (liveECE_n != null && prodECE_n != null) ? Math.abs(liveECE_n - prodECE_n) : num(c.delta_ece ?? c.delta);
  const liveBrier_n = num(c.live_brier);
  const prodBrier_n = num(c.prod_brier ?? c.production_brier);
  const sampleCount_n = int(c.sample_count ?? c.n ?? data.sample_count ?? data.n);
  const winFrom = (c.window && (c.window.from_iso || c.window.from)) ? (c.window.from_iso || c.window.from) : null;
  const winTo = (c.window && (c.window.to_iso || c.window.to)) ? (c.window.to_iso || c.window.to) : null;

  const absThreshold = num(m.abs_threshold) ?? 0.05;
  const deltaForBar = num(m.delta) ?? deltaECE_n ?? 0;
  const liveECEBarWidth = `${(clamp01((liveECE_n ?? 0) / 0.2) * 100).toFixed(1)}%`;
  const deltaBarWidth = `${(clamp01(Math.abs(deltaForBar) / (absThreshold || 0.05)) * 100).toFixed(1)}%`;

  const reasons: string[] = Array.isArray(m.reasons) ? m.reasons : [];
  const absStreak = int(m.abs_streak ?? m.abs_drift_streak);
  const relStreak = int(m.rel_streak ?? m.rel_drift_streak);
  const retrainRecommended = !!(m.retrain_recommended) || ((liveECE_n ?? 0) > 0.2) || ((deltaECE_n ?? 0) > 0.2);

  const show = [prodECE_n, liveECE_n, liveBrier_n, prodBrier_n, absStreak, relStreak].some(v => v != null) || reasons.length > 0 || retrainRecommended;
  return {
    show,
    prodECE: fmt(prodECE_n),
    liveECE: fmt(liveECE_n),
    deltaECE: fmt(deltaECE_n),
    eceWorse: (liveECE_n != null && prodECE_n != null) ? (liveECE_n > prodECE_n) : false,
    liveBrier: fmt(liveBrier_n),
    prodBrier: fmt(prodBrier_n),
    liveECEBarWidth,
    retrainRecommended,
    absStreak,
    relStreak,
    reasons,
    deltaBarWidth,
    sampleCount: sampleCount_n,
    winFrom,
    winTo
  };
});

// Auto-expand when retrain is recommended (placed after calib is defined)
watch(() => calib.value.retrainRecommended, (v) => { if (v) calibOpen.value = true; }, { immediate: true });

// Calibration bins breakdown with Wilson CI and ECE contribution
type CalibBin = { range: string; conf: number; acc: number; n: number; gap: number; contrib: number; ciLow: number | null; ciHigh: number | null; contribPct?: number | null; cumPct?: number | null };
const calibBins = computed<CalibBin[]>(() => {
  const c: any = (nsb.value.data as any)?.calibration;
  const bins: any[] = Array.isArray(c?.bins) ? c.bins : [];
  const Ntot = Number(c?.sample_count ?? 0);
  const safeNum = (x: any) => { const v = Number(x); return Number.isFinite(v) ? v : 0; };
  const wilson = (k: number, n: number, z = 1.96): [number, number] | null => {
    if (!Number.isFinite(n) || n <= 0) return null;
    const phat = k / n;
    const denom = 1 + (z * z) / n;
    const center = phat + (z * z) / (2 * n);
    const margin = z * Math.sqrt((phat * (1 - phat) + (z * z) / (4 * n)) / n);
    const low = Math.max(0, (center - margin) / denom);
    const high = Math.min(1, (center + margin) / denom);
    return [low, high];
  };
  const rows: CalibBin[] = [];
  for (const b of bins) {
    const range = String(b.range ?? b.bin ?? '-')
    const conf = safeNum(b.avg ?? b.confidence ?? b.conf);
    const acc = safeNum(b.acc ?? b.accuracy ?? b.rate);
    const n = Math.max(0, Math.trunc(safeNum(b.n ?? b.count)));
    const gap = Math.abs(conf - acc);
    const contrib = (Ntot > 0 ? (n / Ntot) : 0) * gap;
    const ci = wilson(Math.round(acc * n), n);
    rows.push({ range, conf, acc, n, gap, contrib, ciLow: ci ? ci[0] : null, ciHigh: ci ? ci[1] : null });
  }
  // Do not sort here; keep raw rows. Sorting and percent annotation will be handled downstream.
  return rows;
});

// Sorting for calibration bins table
type SortKey = 'range' | 'conf' | 'acc' | 'ciw' | 'gap' | 'n' | 'contrib';
const sortKey = ref<SortKey>('contrib');
const sortDir = ref<'asc' | 'desc'>('desc');
function isSort(k: SortKey) { return sortKey.value === k; }
function toggleSort(k: SortKey) {
  if (sortKey.value === k) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc';
  } else {
    sortKey.value = k;
    // Default direction: strings asc, numbers desc
    sortDir.value = k === 'range' ? 'asc' : 'desc';
  }
}
const sortedCalibBins = computed(() => {
  const arr = (calibBins.value || []).slice();
  // Calculate total ECE (sum of contributions) for percentage columns
  const totalECE = arr.reduce((s, r) => s + (Number.isFinite(r.contrib) ? r.contrib : 0), 0);
  const dir = sortDir.value === 'asc' ? 1 : -1;
  const key = sortKey.value;
  arr.sort((a, b) => {
    switch (key) {
      case 'range':
        return String(a.range).localeCompare(String(b.range)) * dir;
      case 'conf':
        return (a.conf - b.conf) * dir;
      case 'acc':
        return (a.acc - b.acc) * dir;
      case 'gap':
        return (a.gap - b.gap) * dir;
      case 'n':
        return (a.n - b.n) * dir;
      case 'contrib':
        return (a.contrib - b.contrib) * dir;
      case 'ciw': {
        const wa = (a.ciLow == null || a.ciHigh == null)
          ? (sortDir.value === 'asc' ? Number.POSITIVE_INFINITY : Number.NEGATIVE_INFINITY)
          : (a.ciHigh - a.ciLow);
        const wb = (b.ciLow == null || b.ciHigh == null)
          ? (sortDir.value === 'asc' ? Number.POSITIVE_INFINITY : Number.NEGATIVE_INFINITY)
          : (b.ciHigh - b.ciLow);
        return (wa - wb) * dir;
      }
      default:
        return (a.contrib - b.contrib) * dir;
    }
  });
  // Annotate per-bin percent and cumulative percent based on current sort
  let accPct = 0;
  for (const r of arr) {
    const pct = totalECE > 0 ? (r.contrib / totalECE) : 0;
    accPct += pct;
    r.contribPct = Number.isFinite(pct) ? pct : 0;
    r.cumPct = Number.isFinite(accPct) ? accPct : 0;
  }
  return arr;
});

// Reliability plot data (conf vs acc)
const relPlotW = 320;
const relPlotH = 200;
const relPad = 28;
const relInnerW = relPlotW - relPad * 2;
const relInnerH = relPlotH - relPad * 2;
type RelPoint = { x: number; y: number; r: number; color: string; title: string };
const relPoints = computed<RelPoint[]>(() => {
  const bins = calibBins.value;
  if (!bins.length) return [];
  const maxN = Math.max(...bins.map(b => b.n || 0), 1);
  const clamp01 = (v: number) => Math.max(0, Math.min(1, v));
  const toColor = (gap: number) => {
    // map gap 0..0.2 to green->orange->red
    const g = Math.max(0, Math.min(0.2, gap)) / 0.2; // 0..1
    const r = Math.round(255 * g);
    const gr = Math.round(180 * (1 - g) + 80 * g);
    return `rgb(${r}, ${gr}, 100)`;
  };
  const points: RelPoint[] = [];
  for (const b of bins) {
    const x = clamp01(b.conf);
    const y = clamp01(b.acc);
    const r = 2 + 6 * Math.sqrt((b.n || 0) / maxN); // area ~ N
    const color = toColor(Math.abs(b.conf - b.acc));
    const title = `range: ${b.range}\nconf: ${b.conf.toFixed(3)}\nacc: ${b.acc.toFixed(3)}\nN: ${b.n}\ngap: ${Math.abs(b.conf - b.acc).toFixed(3)}`;
    points.push({ x, y, r, color, title });
  }
  return points;
});

// Copy calibration report to clipboard
async function copyCalibReport() {
  const lines = [
    'Calibration Report',
    `Window(UTC): ${calib.value.winFrom || '-'} → ${calib.value.winTo || '-'}`,
    `N: ${calib.value.sampleCount ?? '-'}`,
    `Prod ECE: ${calib.value.prodECE}`,
    `Live ECE: ${calib.value.liveECE}`,
    `Δ ECE: ${calib.value.deltaECE}`,
    `Prod Brier: ${calib.value.prodBrier}`,
    `Live Brier: ${calib.value.liveBrier}`,
    `Abs streak: ${calib.value.absStreak ?? '-'}`,
    `Rel streak: ${calib.value.relStreak ?? '-'}`,
    `Reasons: ${(calib.value.reasons || []).join(', ') || '-'}`,
  ];
  const txt = lines.join('\n');
  try {
    await navigator.clipboard.writeText(txt);
  } catch {
    // Fallback prompt if clipboard API blocked
    window.prompt('Copy calibration report:', txt);
  }
}

// Helper: format Date to 'YYYY-MM-DDTHH:mm' for datetime-local inputs
function formatDateLocal(d: Date): string {
  const pad = (n: number) => (n < 10 ? `0${n}` : `${n}`);
  const Y = d.getFullYear();
  const M = pad(d.getMonth() + 1);
  const D = pad(d.getDate());
  const h = pad(d.getHours());
  const m = pad(d.getMinutes());
  return `${Y}-${M}-${D}T${h}:${m}`;
}

// ---------- Backtest (Replay) support for Performance ----------
const bt = ref<{ loading: boolean; data: any | null }>({ loading: false, data: null });
async function fetchBacktestReplay() {
  if (perfSource.value !== 'backtest') return;
  try {
    bt.value.loading = true;
    const params: any = {};
    // Convert datetime-local to seconds epoch if provided
    const parseDt = (s: string) => {
      try {
        if (!s) return null;
        // Expect 'YYYY-MM-DDTHH:mm' or 'YYYY-MM-DDTHH:mm:ss'
        const m = s.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/);
        if (m) {
          const [_, Y, M, D, h, mnt, sec] = m;
          const dt = new Date(
            Number(Y),
            Number(M) - 1,
            Number(D),
            Number(h),
            Number(mnt),
            sec ? Number(sec) : 0,
            0
          );
          const t = Math.floor(dt.getTime() / 1000);
          return isFinite(t) ? t : null;
        }
        // Fallback to native parsing
        const d = new Date(s);
        const t = Math.floor(d.getTime() / 1000);
        return isFinite(t) ? t : null;
      } catch { return null; }
    };
    const f = parseDt(btFrom.value); const t = parseDt(btTo.value);
  if (f != null) params.from_ts = f; if (t != null) params.to_ts = t;
  if (btSymbol.value) params.symbol = btSymbol.value.toUpperCase();
    const r = await http.get('/api/backtest/replay', { params });
    bt.value.data = r.data || null;
  } catch {
    // ignore errors in optional view
  } finally {
    bt.value.loading = false;
  }
}
function onBacktestParamsChange() { if (perfSource.value === 'backtest') fetchBacktestReplay(); }

// Debounced auto-fetch when date inputs change
const btDebounce = ref<ReturnType<typeof setTimeout> | null>(null);
watch([btFrom, btTo], () => {
  if (perfSource.value !== 'backtest') return;
  if (btDebounce.value) clearTimeout(btDebounce.value as any);
  btDebounce.value = setTimeout(() => fetchBacktestReplay(), 300);
});
watch(perfSource, (v) => {
  if (v === 'backtest') fetchBacktestReplay();
});

const btSummary = computed(() => (bt.value.data as any)?.summary || null);
const btWindowFrom = computed(() => btSummary.value?.window?.from_iso || null);
const btWindowTo = computed(() => btSummary.value?.window?.to_iso || null);

// --- Synthetic trade reconstruction from events (FIFO matching) ---
type BtEvent = { side: 'buy'|'sell'; size: number; price: number; filled_ts_ms?: number; created_ts_ms?: number };
type BtTrade = { ts_ms: number; side: 'long'|'short'; size: number; entry_price: number; exit_price: number; gross_pnl: number; fees: number; net_pnl: number };
const btSynthetic = computed(() => {
  const data: any = bt.value.data;
  const out = { trades: [] as BtTrade[], totalNet: 0 };
  if (!data) return out;
  const events: BtEvent[] = Array.isArray(data.events) ? data.events
    .filter((e: any) => e && e.status === 'filled' && typeof e.size === 'number' && typeof e.price === 'number')
    .map((e: any) => ({ side: (String(e.side).toLowerCase() === 'sell' ? 'sell' : 'buy') as 'buy'|'sell', size: Number(e.size), price: Number(e.price), filled_ts_ms: Number(e.filled_ts_ms)||undefined, created_ts_ms: Number(e.created_ts_ms)||undefined }))
    : [];
  if (events.length === 0) return out;
  events.sort((a, b) => (a.filled_ts_ms ?? a.created_ts_ms ?? 0) - (b.filled_ts_ms ?? b.created_ts_ms ?? 0));
  const feeRate: number = (() => {
    const s: any = data.summary || {};
    const mode = String(s.fee_mode || 'taker').toLowerCase();
    const r = Number(mode === 'maker' ? s.fee_maker : s.fee_taker);
    return Number.isFinite(r) && r >= 0 ? r : 0.001; // default 0.1%
  })();
  type Leg = { side: 'buy'|'sell'; size: number; price: number; ts_ms: number; fee: number };
  const queue: Leg[] = [];
  let pos = 0; // signed position size: >0 long, <0 short
  let accForTrade = 0; // aggregate net pnl until position reaches 0

  const pushLeg = (side: 'buy'|'sell', size: number, price: number, ts_ms: number) => {
    const fee = price * size * feeRate;
    queue.push({ side, size, price, ts_ms, fee });
  };
  const takeFromQueue = (wantSide: 'buy'|'sell', qty: number, price: number, _ts_ms: number) => {
    // wantSide is current event side, so we are matching against opposite side legs in queue
    let remain = qty;
    while (remain > 0 && queue.length > 0) {
      const head = queue[0];
      if (head.side === wantSide) break; // no opposite legs to match
      const q = Math.min(remain, head.size);
      // Compute realized pnl for the matched quantity
      let gross = 0;
      if (head.side === 'buy' && wantSide === 'sell') {
        // closing long: sell - buy
        gross = (price - head.price) * q;
      } else if (head.side === 'sell' && wantSide === 'buy') {
        // closing short: sell - buy (entry sell, exit buy)
        gross = (head.price - price) * q;
      }
      // allocate proportional fees: entry leg prorated, exit fee for q
      const entryFeePro = head.fee * (q / head.size);
      const exitFee = price * q * feeRate;
      const net = gross - entryFeePro - exitFee;
      accForTrade += net;
      out.totalNet += net;
      // reduce head
      head.size -= q;
      head.fee -= entryFeePro;
      if (head.size <= 1e-12) queue.shift();
      remain -= q;
      // Update position
      pos += (wantSide === 'buy' ? q : -q) * (pos < 0 ? 1 : pos > 0 ? -1 : 0); // this expression is messy; we'll recompute pos separately below
    }
    return qty - remain; // matched amount
  };
  const recomputePos = () => {
    // sum queue by side
    let longSz = 0; let shortSz = 0;
    for (const l of queue) {
      if (l.side === 'buy') longSz += l.size; else shortSz += l.size;
    }
    pos = longSz - shortSz;
  };

  for (const e of events) {
    const ts = e.filled_ts_ms ?? e.created_ts_ms ?? 0;
    const beforeSign = Math.sign(pos);
    if (e.side === 'buy') {
      // first close shorts if any
      const matched = takeFromQueue('buy', e.size, e.price, ts);
      const leftover = e.size - matched;
      if (leftover > 1e-12) pushLeg('buy', leftover, e.price, ts);
    } else { // sell
      const matched = takeFromQueue('sell', e.size, e.price, ts);
      const leftover = e.size - matched;
      if (leftover > 1e-12) pushLeg('sell', leftover, e.price, ts);
    }
    recomputePos();
    const afterSign = Math.sign(pos);
    // If we crossed or touched zero (closed previous direction), finalize a trade
    if (beforeSign !== 0 && afterSign === 0) {
      out.trades.push({ ts_ms: ts, side: beforeSign > 0 ? 'long' : 'short', size: 0, entry_price: 0, exit_price: 0, gross_pnl: 0, fees: 0, net_pnl: accForTrade });
      accForTrade = 0;
    } else if (beforeSign !== 0 && afterSign !== 0 && beforeSign !== afterSign) {
      // Over-flip: we closed previous and opened new in one event
      out.trades.push({ ts_ms: ts, side: beforeSign > 0 ? 'long' : 'short', size: 0, entry_price: 0, exit_price: 0, gross_pnl: 0, fees: 0, net_pnl: accForTrade });
      accForTrade = 0;
    }
  }
  return out;
});

const btBackendTrades = computed<any[]>(() => {
  const t = (bt.value.data as any)?.trades;
  return Array.isArray(t) ? t : [];
});
const btTradesUsed = computed<any[]>(() => btBackendTrades.value.length > 0 ? btBackendTrades.value : btSynthetic.value.trades);
const btUsingSynthetic = computed<boolean>(() => btBackendTrades.value.length === 0 && btTradesUsed.value.length > 0);

const btNet = computed<number | null>(() => {
  if (btBackendTrades.value.length > 0) {
    // Prefer backend summary if finite, else sum backend trades
    const v = Number(btSummary.value?.total_realized_net);
    if (Number.isFinite(v)) return v;
    return btBackendTrades.value.reduce((s, t) => s + (Number(t?.net_pnl) || 0), 0);
  }
  // Synthetic total
  return btSynthetic.value.trades.length ? btSynthetic.value.totalNet : null;
});
// removed btWinRate (win rate card omitted)

// Live trading helpers
async function fetchLiveStatus(source?: 'auto'|'exchange'|'db') {
  try {
    live.value.loading = true;
    const params: any = {};
    if (source && source !== 'auto') params.source = source;
    const r = await http.get('/api/trading/live/status', { params });
    const d = r.data || {};
  live.value.enabled = !!d.enabled;
  // mutate params/equity to keep refs stable
  const p = d.params || {};
  if (!live.value.saving) {
    live.value.params.base_size = p.base_size ?? live.value.params.base_size ?? 1;
    live.value.params.cooldown_sec = p.cooldown_sec ?? live.value.params.cooldown_sec ?? 60;
    live.value.params.last_trade_ts = (p.last_trade_ts ?? live.value.params.last_trade_ts ?? null);
    // scale-in params (set directly since advanced UI is hidden)
    live.value.params.allow_scale_in = p.allow_scale_in ?? live.value.params.allow_scale_in ?? false;
    live.value.params.scale_in_size_ratio = p.scale_in_size_ratio ?? live.value.params.scale_in_size_ratio ?? 0.5;
    live.value.params.scale_in_max_legs = p.scale_in_max_legs ?? live.value.params.scale_in_max_legs ?? 3;
    live.value.params.scale_in_min_price_move = p.scale_in_min_price_move ?? live.value.params.scale_in_min_price_move ?? 0.0;
    live.value.params.scale_in_cooldown_sec = p.scale_in_cooldown_sec ?? live.value.params.scale_in_cooldown_sec ?? 0;
    // policy/guards
    if (Object.prototype.hasOwnProperty.call(p, 'scale_in_freeze_on_exit')) {
      (live.value.params as any).scale_in_freeze_on_exit = !!p.scale_in_freeze_on_exit;
    }
    if (Object.prototype.hasOwnProperty.call(p, 'exit_slice_seconds')) {
      (live.value.params as any).exit_slice_seconds = Number(p.exit_slice_seconds || 0);
    }
  }
  const eq = d.equity || {};
  live.value.equity.starting = eq.starting ?? live.value.equity.starting ?? null;
  live.value.equity.current = eq.current ?? live.value.equity.current ?? null;
  live.value.equity.peak = eq.peak ?? live.value.equity.peak ?? null;
  live.value.equity.cumulative_pnl = eq.cumulative_pnl ?? live.value.equity.cumulative_pnl ?? null;
  // arrays: splice to update in place
  const newPositions = Array.isArray(d.positions) ? d.positions : [];
  live.value.positions.splice(0, live.value.positions.length, ...newPositions);
  const newOrders = Array.isArray(d.orders) ? d.orders : [];
  live.value.orders.splice(0, live.value.orders.length, ...newOrders);
  // capture orders source if provided
  if (d && typeof d.orders_source === 'string') {
    live.value.ordersSource = (d.orders_source === 'exchange') ? 'exchange' : 'db';
  }
  lastUpdated.value = Date.now();
  } catch {
    // soft-fail on UI
  } finally {
    live.value.loading = false;
  }
}

// Cooldown display for header
const cooldownRemainingSec = computed(() => {
  const last = live.value.params?.last_trade_ts || 0;
  const cd = live.value.params?.cooldown_sec || 0;
  if (!last || !cd) return 0;
  const remaining = Math.max(0, Math.ceil((last + cd) - (Date.now()/1000)));
  return remaining;
});

// Styling helpers for order chips
// sideClass not used in simplified UI
// Remaining labels computed directly from snapshot
// removed price gate remain label (live progress UI removed)
// Δ gate removed: no remaining label needed

// --- Donut chart computed helpers ---
// removed donut geometry helpers (live progress removed)

// Current values and targets
// removed siPriceTarget (live progress removed)
// removed siPriceDisabled (live progress removed)

// Price gate progress and diagnostics removed

// If price gate is disabled, reflect 0% to avoid confusion (will also show OFF label)
// removed price progress (live progress removed)

// removed price donut dash array (live progress UI removed)
// Δ gate removed

// --- Buy/Exit donuts ---
// removed buyTarget (live progress removed)
// Latest inference helpers and probability with post-trade reset
// removed toSeconds (live progress removed)
// removed inferLatestTsSec (live progress removed)
// removed buyCurrent (live progress removed)
// removed buyDisabled (live progress removed)
// removed buy progress (live progress removed)
// removed buy remain label and donut dash (live progress UI removed)

// In-position inference removed
// UI-only fallback: infer in-position from recent orders when positions/gates are missing
// removed hasPositionByOrders (live progress removed)
// Signals freshness and suggested UI mode mapping
// removed SSE UI mode helpers (live progress removed)
// Live donut switching helpers
// Average entry price helper removed
// Replace this block to not require backend gates and not require the gate to be enabled
// removed showPriceGate (live progress removed)

// UI state: which donuts to show based on mode and conditions
// removed live progress UI flags

// Define missing inPositionUi (positions or orders-derived)
// removed inPositionUi (live progress removed)

// removed live progress UI exit flag

// Header hint: no-trade message
const noTradeHint = computed<string | null>(() => buildNoTradeHint());

// onMounted removed (Backtester defaults no longer needed)

// ---------- Performance metrics (live baseline) ----------
const perfCurrentPrice = computed<number | null>(() => {
  const v = (nsb.value.data as any)?.scale_in?.gates?.current_price;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
});
// removed posCount (card omitted)
const openPnl = computed<number | null>(() => {
  const cp = perfCurrentPrice.value;
  if (cp == null) return null;
  try {
    const arr = Array.isArray(live.value.positions) ? live.value.positions : [];
    let s = 0;
    for (const p of arr) {
      const ep = Number(p.entry_price);
      const sz = Number(p.size);
      if (Number.isFinite(ep) && Number.isFinite(sz)) {
        s += (cp - ep) * sz;
      }
    }
    return s;
  } catch { return null; }
});
const roiPct = computed<number | null>(() => {
  const start = Number(live.value.equity.starting);
  const curr = Number(live.value.equity.current);
  if (!Number.isFinite(start) || !Number.isFinite(curr) || start === 0) return null;
  return (curr / start - 1) * 100;
});
const drawdownPct = computed<number | null>(() => {
  const peak = Number(live.value.equity.peak);
  const curr = Number(live.value.equity.current);
  if (!Number.isFinite(peak) || !Number.isFinite(curr) || peak === 0) return null;
  return (curr / peak - 1) * 100; // negative when below peak
});
const cumPnl = computed<number | null>(() => {
  const v = Number(live.value.equity.cumulative_pnl);
  return Number.isFinite(v) ? v : null;
});
// removed winRate (card omitted)

// labels & statuses
const fmtPct = (v: number | null, digits = 2) => (v == null ? '-' : `${v.toFixed(digits)}%`);
const fmtNum = (v: number | null, digits = 2) => (v == null ? '-' : v.toFixed(digits));

const roiLabel = computed(() => perfSource.value === 'backtest' ? fmtPct(btRoiPct.value) : fmtPct(roiPct.value));
const roiStatus = computed(() => {
  const v = perfSource.value === 'backtest' ? btRoiPct.value : roiPct.value;
  return v == null ? 'idle' : v >= 0 ? 'ok' : 'error';
});
const ddLabel = computed(() => perfSource.value === 'backtest' ? fmtPct(btMaxDdPct.value) : fmtPct(drawdownPct.value));
const ddStatus = computed(() => {
  const v = perfSource.value === 'backtest' ? btMaxDdPct.value : drawdownPct.value;
  if (v == null) return 'idle';
  if (v <= -10) return 'warning';
  return 'ok';
});
const cumPnlLabel = computed(() => perfSource.value === 'backtest' ? (btNet.value == null ? '-' : btNet.value.toFixed(2)) : fmtNum(cumPnl.value));
const cumPnlStatus = computed(() => {
  if (perfSource.value === 'backtest') return (btNet.value == null ? 'idle' : btNet.value >= 0 ? 'ok' : 'error');
  return (cumPnl.value == null ? 'idle' : cumPnl.value >= 0 ? 'ok' : 'error');
});
const openPnlLabel = computed(() => perfSource.value === 'backtest' ? '-' : fmtNum(openPnl.value));
const openPnlStatus = computed(() => perfSource.value === 'backtest' ? 'idle' : (openPnl.value == null ? 'idle' : openPnl.value >= 0 ? 'ok' : 'error'));
// removed posCount/winRate cards: drop related computed to avoid linter warnings

// Backtest ROI & MaxDD from equity curve reconstructed from cumulative net pnl
const btRoiPct = computed<number | null>(() => {
  try {
    const trades = btTradesUsed.value;
    if (!Array.isArray(trades)) return null;
    const start = Number(btStartEquity.value || 0);
    if (!isFinite(start) || start <= 0) return null;
    const totalNet = trades.reduce((s: number, t: any) => s + (Number(t?.net_pnl) || 0), 0);
    return (start + totalNet) / start * 100 - 100; // %
  } catch { return null; }
});
const btMaxDdPct = computed<number | null>(() => {
  try {
    const trades = btTradesUsed.value;
    if (!Array.isArray(trades)) return null;
    const start = Number(btStartEquity.value || 0);
    if (!isFinite(start) || start <= 0) return null;
    let eq = start; let peak = start; let maxDd = 0;
    for (const t of trades) {
      eq += Number(t?.net_pnl) || 0;
      if (eq > peak) peak = eq;
      const dd = (eq / peak - 1) * 100; // <= 0
      if (dd < maxDd) maxDd = dd;
    }
    return maxDd; // negative %
  } catch { return null; }
});

// Quick range presets
function setBtRangeDays(days: number) {
  const now = new Date();
  const from = new Date(now.getTime() - days * 24 * 60 * 60 * 1000);
  btFrom.value = formatDateLocal(from);
  btTo.value = formatDateLocal(now);
  onBacktestParamsChange();
}

// No data hint
const btNoData = computed<boolean>(() => {
  const d: any = bt.value.data;
  if (!d) return false;
  const orders = Array.isArray(d.orders) ? d.orders.length : 0;
  const events = Array.isArray(d.events) ? d.events.length : 0;
  const trades = Array.isArray(d.trades) ? d.trades.length : 0;
  return orders === 0 && events === 0 && trades === 0;
});

// --- Compact orders helpers for list rendering ---
const ordersLimited = computed<any[]>(() => {
  const arr = Array.isArray(live.value.orders) ? live.value.orders.slice() : [];
  // sort by filled_ts or created_ts descending
  arr.sort((a: any, b: any) => {
    const ta = Number(a.filled_ts || a.filled_ts_ms || a.created_ts || a.created_ts_ms || 0);
    const tb = Number(b.filled_ts || b.filled_ts_ms || b.created_ts || b.created_ts_ms || 0);
    return tb - ta;
  });
  return arr.slice(0, 20);
});
function fmtOrderTime(o: any): string {
  const t = Number(o.filled_ts || o.filled_ts_ms || o.created_ts || o.created_ts_ms || 0);
  if (!Number.isFinite(t) || t <= 0) return '-';
  const ms = t > 1e12 ? t : (t * 1000);
  try { return new Date(ms).toLocaleTimeString(); } catch { return '-'; }
}
</script>

<style scoped>
.card {
  background: rgba(38,38,38,0.6);
  border: 1px solid rgba(64,64,64,0.6);
  border-radius: 0.5rem;
  padding: 1rem;
}
</style>