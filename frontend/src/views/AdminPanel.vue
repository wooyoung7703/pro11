<template>
  <div class="space-y-6">
    <!-- Page-open loading bar -->
    <div v-if="loadBar.active" class="px-3 py-2 rounded border border-neutral-700 bg-neutral-800/60">
      <div class="flex items-center justify-between text-[11px] text-neutral-300 mb-1">
        <div>초기 로딩 중… {{ loadBar.label }}</div>
        <div class="font-mono">{{ loadPct }}%</div>
      </div>
      <div class="h-1.5 bg-neutral-900 rounded overflow-hidden">
        <div class="h-1.5 bg-brand-primary/70 transition-all" :style="{ width: loadPct + '%' }"></div>
      </div>
    </div>
    <ConfirmDialog
      :open="confirm.open"
      :title="confirm.title"
      :message="confirm.message"
      :requireText="confirm.requireText"
      :delayMs="confirm.delayMs"
      @confirm="confirm.onConfirm && confirm.onConfirm()"
      @cancel="confirm.open=false"
    />
    <!-- Help banner -->
    <div class="px-3 py-2 text-[11px] rounded border border-neutral-700 bg-neutral-800/40 text-neutral-300">
      <details>
        <summary class="cursor-pointer">도움말</summary>
        <div class="mt-1 space-y-1">
          <div>• Startup: fast_startup으로 지연 루프를 기동합니다. 전체 테이블 생성은 idempotent합니다.</div>
          <div>• Bootstrap: min AUC/max ECE 프리셋을 적용하고 폼은 로컬에 저장됩니다. 결과 JSON 다운로드 지원.</div>
          <div>• Labeler/Risk: 라벨러 배치 파라미터(min_age/limit), Risk 자동 새로고침(15s)을 제공합니다.</div>
          <div>• 안전 장치: 위험 작업은 확인 모달(지연 활성화/확인 단어)로 실수 방지합니다.</div>
          <div>• 성능: 관리자 하위 화면은 지연 로딩되어 초기 로딩이 가볍습니다.</div>
          <div>• Job Center: Backfill/Training/Labeler/Promotion을 카드로 모니터링합니다. 진행률/ETA/최근 이벤트를 확인하고 상세 탭으로 이동할 수 있습니다.</div>
          <div>• 실시간: 대부분 패널은 Live(SSE)와 Auto(폴링)를 병행 지원합니다. Live가 켜지면 Auto는 자동 비활성화되어 중복 요청을 방지합니다.</div>
          <a class="underline text-[11px] text-brand-accent" href="/docs/ko_ops_guide.md" target="_blank" rel="noopener">자세한 가이드 보기</a>
        </div>
      </details>
    </div>
    <section class="card space-y-4">
      <div class="flex items-center justify-between">
        <h1 class="text-xl font-semibold">Admin Controls</h1>
        <div class="flex items-center gap-2 text-xs">
          <!-- 모델 초기화 control -->
          <label class="hidden md:flex items-center gap-2 mr-1 text-[11px] text-neutral-400">
            <input type="checkbox" v-model="dropFeatures" /> Feat runs 포함
          </label>
          <label class="hidden md:flex items-center gap-2 mr-1 text-[11px] text-neutral-400">
            <input type="checkbox" v-model="resetThenBootstrap" /> 초기화 후 부트스트랩
          </label>
          <button class="btn !py-1 !px-2 !bg-rose-800 hover:!bg-rose-700 disabled:opacity-60" :disabled="loading.reset" title="모델 및 트레이닝 데이터 삭제" @click="confirmResetModels">
            <span v-if="loading.reset" class="animate-pulse">초기화 중…</span>
            <span v-else>모델 초기화</span>
          </button>
          <span v-if="lastResetAt" class="text-[10px] text-neutral-400">last reset: {{ new Date(lastResetAt).toLocaleString() }}</span>
          <span v-if="error" class="px-2 py-0.5 rounded bg-brand-danger/20 text-brand-danger">{{ error }}</span>
          <span v-if="successMsg" class="px-2 py-0.5 rounded bg-brand-accent/20 text-brand-accent">{{ successMsg }}</span>
        </div>
      </div>
      <p class="text-xs text-neutral-400">운영/테스트 편의를 위한 관리 작업을 수동으로 실행할 수 있습니다.</p>
      
      <div class="grid md:grid-cols-2 gap-6">
        <!-- OHLCV Controls -->
        <div class="p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-3">
          <h2 class="text-sm font-semibold">OHLCV Controls</h2>
          <div class="flex flex-wrap items-end gap-2 text-[11px]">
            <label class="flex flex-col">
              <span class="text-neutral-400 text-[10px]">Interval</span>
              <input v-model="ohlcv.interval" class="input !py-1 !px-2 w-24" placeholder="1m" />
            </label>
            <label class="flex flex-col">
              <span class="text-neutral-400 text-[10px]">Limit</span>
              <input type="number" v-model.number="ohlcv.limit" class="input !py-1 !px-2 w-24" min="50" max="1000" />
            </label>
            <button class="btn !py-0.5 !px-2" :disabled="ohlcv.loading" @click="ohlcv.fetchRecent({ includeOpen: true })">Refresh</button>
            <button class="btn !py-0.5 !px-2" @click="ohlcvWsToggle">WS: {{ ohlcvWsStatus }}</button>
            <button class="btn !py-0.5 !px-2" :disabled="ohlcvFilling" @click="ohlcvFillGaps">Gaps</button>
            <button class="btn !py-0.5 !px-2" :disabled="ohlcv.yearBackfillPolling" @click="ohlcv.startYearBackfill()">Year Backfill</button>
          </div>
          <div v-if="ohlcv.yearBackfill" class="text-[10px] text-neutral-400 flex items-center gap-1">
            <span>{{ (ohlcv.yearBackfill.percent||0).toFixed(1) }}%</span>
            <span v-if="ohlcv.yearBackfill.status==='running'">⏳</span>
            <span v-else-if="ohlcv.yearBackfill.status==='success'" class="text-emerald-400">✔</span>
            <span v-else-if="ohlcv.yearBackfill.status==='error'" class="text-red-400">✖</span>
            <span v-if="ohlcvEtaDisplay">ETA {{ ohlcvEtaDisplay }}</span>
          </div>
        </div>
        <!-- Calibration Controls (migrated from Calibration tab) -->
        <div class="p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-3">
          <h2 class="text-sm font-semibold">Calibration Controls</h2>
          <div class="flex flex-wrap items-center gap-2 text-[11px]">
            <label class="flex items-center gap-1">
              <span class="text-neutral-400">인터벌</span>
              <select class="input !py-1 !px-2" v-model="ohlcv.interval" @change="onCalibIntervalChange">
                <option v-for="iv in calibIntervals" :key="iv" :value="iv">{{ iv }}</option>
              </select>
            </label>
            <button class="btn !py-0.5 !px-2" :disabled="calibLoading" @click="calibFetchAll">새로고침</button>
            <label class="flex items-center gap-1">
              <input type="checkbox" v-model="calibAuto" @change="calibToggleAuto()" /> 자동
            </label>
            <label class="flex items-center gap-1">
              <span class="text-neutral-400">주기</span>
              <input class="input !py-1 !px-2 w-24" type="number" min="5" max="120" v-model.number="calibIntervalSecModel" @change="setCalibInterval()" />
              <span class="ml-1">s</span>
            </label>
          </div>
          <div class="flex flex-wrap items-center gap-2 text-[11px]">
            <label class="flex items-center gap-1">
              <span class="text-neutral-400">live_window</span>
              <input class="input !py-1 !px-2 w-28" type="number" min="60" step="60" v-model.number="calibLiveWindowModel" @change="applyLiveWindow" />
              <span class="text-neutral-400 text-[10px] ml-1">sec</span>
            </label>
            <label class="flex items-center gap-1">
              <span class="text-neutral-400">bins</span>
              <input class="input !py-1 !px-2 w-20" type="number" min="5" max="50" step="1" v-model.number="calibLiveBinsModel" @change="applyLiveBins" />
            </label>
            <div class="flex items-center gap-1">
              <span class="text-neutral-400">라벨 수</span>
              <span class="px-2 py-0.5 rounded bg-neutral-700/60 font-mono">{{ calibSampleCountDisplay }}</span>
            </div>
          </div>
          <div class="text-[10px] text-neutral-500">보정 관련 모든 제어는 관리자에서 일원화되었습니다.</div>
        </div>
        <!-- Inference Playground Controls (migrated from Inference tab) -->
        <div class="p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-3">
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-semibold">Inference Playground Controls</h2>
            <div class="text-[10px] text-neutral-500">컨텍스트: <span class="font-mono">{{ ohlcv.symbol }}</span> · <span class="font-mono">{{ ohlcv.interval || '—' }}</span></div>
          </div>
          <div class="flex flex-wrap items-center gap-2 text-[11px]">
            <label class="flex items-center gap-1">
              <span class="text-neutral-400">인터벌</span>
              <select class="input !py-1 !px-2" v-model="ohlcv.interval" @change="onInfIntervalChange">
                <option v-for="iv in calibIntervals" :key="iv" :value="iv">{{ iv }}</option>
              </select>
            </label>
            <button class="btn !py-0.5 !px-2" :disabled="infLoading" @click="infRunOnce">한 번 실행</button>
            <label class="flex items-center gap-1"><input type="checkbox" v-model="infAuto" @change="infToggleAuto(($event.target && ($event.target as HTMLInputElement).checked) || false)" /> 자동</label>
            <label class="flex items-center gap-1" title="루프 주기(초)">
              <span class="text-neutral-400">주기</span>
              <input class="input !py-1 !px-2 w-20" type="number" min="1" max="30" v-model.number="infIntervalSecModel" @change="setInfInterval" />
              <span class="ml-1">s</span>
            </label>
          </div>
          <div class="flex flex-wrap items-center gap-2 text-[11px]">
            <label class="flex items-center gap-1">
              <span class="text-neutral-400">threshold</span>
              <input class="input !py-1 !px-2 w-24" type="number" min="0" max="1" step="0.01" v-model.number="infThresholdModel" @change="applyInfThreshold" />
            </label>
            <span class="text-neutral-500 ml-1">Presets:</span>
            <button class="btn btn-xs" @click="() => { infThresholdModel = 0.90 as any; applyInfThreshold(); }">0.90</button>
            <button class="btn btn-xs" @click="() => { infThresholdModel = 0.92 as any; applyInfThreshold(); }">0.92</button>
            <button class="btn btn-xs" @click="() => { infThresholdModel = 0.94 as any; applyInfThreshold(); }">0.94</button>
            <span class="text-[10px] text-neutral-500">(플레이그라운드 실행 시 사용되는 클라이언트 임계값)</span>
          </div>
        </div>
        <div class="p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-3">
          <h2 class="text-sm font-semibold">Startup & Scheduling</h2>
          <div class="flex flex-wrap gap-2 text-xs">
            <button class="btn" title="초기 지연 루프 업그레이드/기동" :disabled="loading.fastUpgrade" @click="confirmFastUpgrade">fast_startup upgrade</button>
            <button class="btn" title="라벨러 1회 실행" :disabled="loading.labeler" @click="confirmLabeler">run labeler</button>
            <button class="btn" title="학습 트리거" :disabled="loading.training" @click="confirmTraining">trigger training</button>
            <button class="btn !bg-amber-700 hover:!bg-amber-600" title="핵심 테이블 보장/생성" :disabled="loading.schema" @click="confirmEnsureTables">전체 테이블 생성</button>
          </div>
          <div class="grid grid-cols-2 gap-2 text-[11px]">
            <label class="flex items-center gap-2" title="lookahead (bars)"><span class="text-neutral-400 w-28">bottom lookahead</span>
              <input class="input w-full" type="number" min="1" v-model.number="bottomLookahead" />
            </label>
            <label class="flex items-center gap-2" title="min drawdown"><span class="text-neutral-400 w-28">bottom drawdown</span>
              <input class="input w-full" type="number" step="0.001" min="0" v-model.number="bottomDrawdown" />
            </label>
            <label class="flex items-center gap-2" title="min rebound"><span class="text-neutral-400 w-28">bottom rebound</span>
              <input class="input w-full" type="number" step="0.001" min="0" v-model.number="bottomRebound" />
            </label>
          </div>
          <div class="text-[11px]">
            <button class="btn btn-xs !py-0.5 !px-2" title="서버 프리뷰 기반 기본값 재동기화" @click="handleReloadBottomDefaults">서버 기본값 다시 불러오기</button>
            <span class="ml-2 text-neutral-500">로컬에 저장된 값이 없을 때 자동 동기화됩니다.</span>
          </div>
          <div class="grid grid-cols-2 gap-2 text-[11px]">
            <label class="flex items-center gap-2" title="라벨러 입력 최소 연령(초)"><span class="text-neutral-400 w-28">labeler min_age</span>
              <input class="input w-full" type="number" min="0" v-model.number="labelerMinAge" />
            </label>
            <label class="flex items-center gap-2" title="라벨러 배치 제한"><span class="text-neutral-400 w-28">labeler limit</span>
              <input class="input w-full" type="number" min="1" v-model.number="labelerBatch" />
            </label>
          </div>
          <div class="flex items-center gap-3 text-[11px]">
            <label class="flex items-center gap-2" :title="`Risk 패널 자동 새로고침 ${riskAutoSec}s`"><input type="checkbox" v-model="riskAuto" /> {{ `risk auto ${riskAutoSec}s` }}</label>
            <label class="flex items-center gap-2" title="SSE 실시간 갱신"><input type="checkbox" v-model="riskLive" /> risk live (SSE)</label>
            <button class="btn !py-0.5 !px-2" :disabled="loading.risk" @click="fetchRisk">Risk 즉시 Refresh</button>
          </div>
          <div class="text-[11px] text-neutral-500">
            fast_startup: 초기 구동 시 heavy loop 지연된 상태라면 upgrade 호출 후 백그라운드 루프 시작.
            <br>전체 테이블 생성: `/api/admin/schema/ensure` 호출 (idempotent). 기존에 없던 모든 핵심 테이블을 생성.
          </div>
          <div v-if="schemaResult.length" class="mt-2 text-[10px] font-mono text-neutral-300 flex flex-wrap gap-1">
            <span v-for="t in schemaResult" :key="t" class="px-1 py-0.5 bg-neutral-700/70 rounded">{{ t }}</span>
          </div>
        </div>
        <div class="p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-3">
          <h2 class="text-sm font-semibold">Initial Model Bootstrap</h2>
          <div class="grid grid-cols-2 gap-2 text-[11px]">
            <label class="flex items-center gap-2"><input type="checkbox" v-model="bootstrap.backfill_year"> year backfill</label>
            <label class="flex items-center gap-2"><input type="checkbox" v-model="bootstrap.fill_gaps"> fill gaps</label>
            <label class="flex items-center gap-2"><input type="checkbox" v-model="bootstrap.retry_fill_gaps"> retry gaps</label>
            <label class="flex items-center gap-2"><input type="checkbox" v-model="bootstrap.dry_run"> dry run</label>
            <label class="flex items-center gap-2"><input type="checkbox" v-model="bootstrap.train_sentiment"> sentiment</label>
            <label class="flex items-center gap-2"><input type="checkbox" v-model="bootstrap.skip_promotion"> skip promotion</label>
          </div>
          <div class="grid grid-cols-3 gap-2 text-[11px]">
            <div>
              <div class="text-neutral-400" title="피처 스냅샷 목표 수">feature target</div>
              <input class="input w-full" type="number" min="100" v-model.number="bootstrap.feature_target" />
            </div>
            <div>
              <div class="text-neutral-400" title="자동 계산 대신 강제 윈도우">feature window (override)</div>
              <input class="input w-full" type="number" min="1" placeholder="auto" v-model.number="bootstrap.feature_window" />
            </div>
            <div>
              <div class="text-neutral-400" title="최소 ROC AUC (구분력 하한)">min AUC</div>
              <input class="input w-full" type="number" step="0.01" min="0" max="1" v-model.number="bootstrap.min_auc" />
            </div>
            <div>
              <div class="text-neutral-400" title="최대 허용 ECE (보정오차 상한)">max ECE</div>
              <input class="input w-full" type="number" step="0.01" min="0" max="1" v-model.number="bootstrap.max_ece" />
            </div>
          </div>
          <div class="flex flex-wrap gap-2 text-[11px] items-center">
            <span class="text-neutral-400">Presets:</span>
            <button class="btn btn-xs" title="보수적: AUC≥0.70, ECE≤0.03" @click="applyPreset('conservative')">conservative</button>
            <button class="btn btn-xs" title="표준: AUC≥0.65, ECE≤0.05" @click="applyPreset('standard')">standard</button>
            <button class="btn btn-xs" title="완화: AUC≥0.60, ECE≤0.08" @click="applyPreset('relaxed')">relaxed</button>
            <span class="text-[10px] text-neutral-500">권장: 표준(도메인/리스크 따라 조정)</span>
          </div>
          <div class="flex flex-wrap gap-2 text-xs">
            <button class="btn !bg-emerald-700 hover:!bg-emerald-600" :disabled="loading.bootstrap" @click="confirmBootstrap(false)">Create initial model</button>
            <button class="btn !bg-neutral-700 hover:!bg-neutral-600" :disabled="loading.bootstrap" @click="confirmBootstrap(true)">Dry run</button>
            <button class="btn !bg-indigo-700 hover:!bg-indigo-600" :disabled="loading.featBackfill" @click="confirmFeatBackfill">Backfill features</button>
            <button v-if="bootstrapResult" class="btn !bg-slate-700 hover:!bg-slate-600" @click="downloadBootstrapResult">Download JSON</button>
            <button v-if="bootstrapResult" class="btn !bg-slate-700 hover:!bg-slate-600" @click="copyBootstrapResult">Copy JSON</button>
            <button class="btn !bg-amber-800 hover:!bg-amber-700" :disabled="loading.bootstrap" @click="resetBootstrapDefaults">Reset to defaults</button>
          </div>
          <div class="text-[11px] text-neutral-400 mt-1">
            진행 순서:
            <span class="font-mono">
              {{ bootstrap.backfill_year ? '① 1년 OHLCV 백필' : '① (백필 건너뜀)' }}
              > {{ bootstrap.fill_gaps ? (bootstrap.retry_fill_gaps ? '② 갭 채우기(리트라이 포함)' : '② 갭 채우기') : '② (갭 채우기 건너뜀)' }}
              > ③ 피처 스냅샷 백필(target: {{ bootstrap.feature_target }})
              > ④ 학습{{ bootstrap.train_sentiment ? '+감성' : '' }}{{ bootstrap.dry_run ? ' (드라이런: 시뮬레이션)' : '' }}
              > ⑤ {{ bootstrap.skip_promotion ? '프로모션 건너뜀' : '임계치 검증 후 프로모션' }}
              {{ (bootstrap.min_auc!=null && bootstrap.max_ece!=null) ? `(AUC≥${bootstrap.min_auc}, ECE≤${bootstrap.max_ece})` : '' }}{{ bootstrap.dry_run && !bootstrap.skip_promotion ? ' (드라이런: 시뮬레이션)' : '' }}
              > ⑥ 리포트 출력
            </span>
            <div class="mt-0.5 text-[10px] text-neutral-500">
              드라이런 안내: 학습/감성/프로모션 단계는 실행하지 않고 결과를 시뮬레이션합니다. OHLCV 백필·갭 채우기·피처 백필은 실제로 수행됩니다.
            </div>
          </div>
          <div v-if="bootstrapResult" class="text-[11px] bg-neutral-900/70 border border-neutral-700 rounded">
            <!-- Tabs -->
            <div class="flex items-center gap-2 border-b border-neutral-800 px-2 py-1">
              <button class="px-2 py-0.5 rounded" :class="bootstrapTab==='summary' ? 'bg-neutral-800 text-neutral-200' : 'text-neutral-400 hover:text-neutral-200'" @click="bootstrapTab='summary'">Summary</button>
              <button class="px-2 py-0.5 rounded" :class="bootstrapTab==='meta' ? 'bg-neutral-800 text-neutral-200' : 'text-neutral-400 hover:text-neutral-200'" @click="bootstrapTab='meta'">Meta</button>
              <button class="px-2 py-0.5 rounded" :class="bootstrapTab==='raw' ? 'bg-neutral-800 text-neutral-200' : 'text-neutral-400 hover:text-neutral-200'" @click="bootstrapTab='raw'">Raw</button>
              <span class="ml-auto text-[10px] text-neutral-500">결과 요약/메타/원본을 분리 렌더합니다.</span>
            </div>
            <!-- Summary -->
            <div v-if="bootstrapTab==='summary'" class="p-2 grid grid-cols-2 md:grid-cols-3 gap-2">
              <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700">
                <div class="text-neutral-400">AUC</div>
                <div class="font-mono" :class="summary.aucPass===true ? 'text-emerald-400' : (summary.aucPass===false ? 'text-rose-400' : 'text-neutral-300')">{{ summary.aucDisplay }}</div>
              </div>
              <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700">
                <div class="text-neutral-400">ECE</div>
                <div class="font-mono" :class="summary.ecePass===true ? 'text-emerald-400' : (summary.ecePass===false ? 'text-rose-400' : 'text-neutral-300')">{{ summary.eceDisplay }}</div>
              </div>
              <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700">
                <div class="text-neutral-400">Samples</div>
                <div class="font-mono text-neutral-200">{{ summary.samplesDisplay }}</div>
              </div>
              <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700">
                <div class="text-neutral-400">Window</div>
                <div class="font-mono text-neutral-200">{{ summary.windowDisplay }}</div>
              </div>
              <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700 col-span-2 md:col-span-1 flex items-center justify-between">
                <div class="text-neutral-400">Gate</div>
                <div>
                  <span class="px-2 py-0.5 rounded text-[10px] font-mono" :class="summary.gate==='PASS' ? 'bg-emerald-700/30 text-emerald-300 border border-emerald-700/40' : (summary.gate==='FAIL' ? 'bg-rose-700/30 text-rose-300 border border-rose-700/40' : 'bg-neutral-700/30 text-neutral-300 border border-neutral-700/40')">{{ summary.gate }}</span>
                  <span class="ml-2 text-[10px] text-neutral-500">(AUC≥{{ summary.minAucDisplay }}, ECE≤{{ summary.maxEceDisplay }})</span>
                </div>
              </div>
            </div>
            <!-- Meta -->
            <div v-else-if="bootstrapTab==='meta'" class="p-2">
              <table class="w-full text-[11px]">
                <thead><tr class="text-neutral-500"><th class="text-left py-1 px-1">Key</th><th class="text-left py-1 px-1">Value</th></tr></thead>
                <tbody>
                  <tr v-for="p in metaPairs" :key="p.key" class="border-t border-neutral-800/60">
                    <td class="py-1 px-1 text-neutral-400">{{ p.key }}</td>
                    <td class="py-1 px-1 font-mono">{{ p.value }}</td>
                  </tr>
                  <tr v-if="metaPairs.length===0"><td colspan="2" class="py-2 text-center text-neutral-600">No meta</td></tr>
                </tbody>
              </table>
            </div>
            <!-- Raw (lazy render) -->
            <div v-else class="p-2 font-mono">
              <pre class="whitespace-pre-wrap">{{ rawJson }}</pre>
            </div>
          </div>
        </div>
        <div class="p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-3">
          <h2 class="text-sm font-semibold flex items-center gap-2">Risk State
            <button class="btn !py-0.5 !px-2 text-[11px]" :disabled="loading.risk" @click="fetchRisk">Refresh</button>
          </h2>
          <div v-if="riskState" class="space-y-2 text-xs font-mono">
            <div class="flex justify-between"><span>Equity</span><span>{{ riskState.session.current_equity }}</span></div>
            <div class="flex justify-between"><span>Peak</span><span>{{ riskState.session.peak_equity }}</span></div>
            <div class="flex justify-between"><span>Start</span><span>{{ riskState.session.starting_equity }}</span></div>
            <div class="flex justify-between"><span>PnL</span><span>{{ riskState.session.cumulative_pnl }}</span></div>
            <div class="pt-1 text-neutral-400">Positions ({{ riskState.positions.length }})</div>
            <table class="w-full text-[10px]"><thead><tr class="text-neutral-500"><th class="text-left">Sym</th><th class="text-right">Size</th><th class="text-right">Entry</th></tr></thead>
              <tbody>
                <tr v-for="p in riskState.positions" :key="p.symbol" class="border-t border-neutral-800/50">
                  <td class="py-0.5">{{ p.symbol }}</td>
                  <td class="py-0.5 text-right">{{ p.size }}</td>
                  <td class="py-0.5 text-right">{{ p.entry_price }}</td>
                </tr>
                <tr v-if="riskState.positions.length===0"><td colspan="3" class="text-center text-neutral-600 py-1">None</td></tr>
              </tbody>
            </table>
          </div>
          <div v-else class="text-xs text-neutral-500">No data yet.</div>
        </div>
        <!-- Artifacts verify / remediation -->
        <div class="p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-3">
          <h2 class="text-sm font-semibold flex items-center gap-2">Artifacts 상태
            <button class="btn !py-0.5 !px-2 text-[11px]" :disabled="loading.artifacts" @click="verifyArtifacts">Verify</button>
            <button class="btn !py-0.5 !px-2 text-[11px] !bg-indigo-700 hover:!bg-indigo-600" :disabled="loading.training" @click="confirmTraining">Quick train</button>
          </h2>
          <div class="text-[11px] text-neutral-400">
            모델 레지스트리의 artifact 파일 존재 여부를 점검합니다. 파일이 없으면 빠른 학습으로 재생성할 수 있습니다.
          </div>
          <div class="grid grid-cols-2 gap-2 text-[11px]">
            <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700 flex items-center justify-between">
              <span class="text-neutral-400">ok</span>
              <span class="font-mono" :class="artifacts.summary?.ok ? 'text-emerald-300' : 'text-neutral-300'">{{ artifacts.summary?.ok ?? '-' }}</span>
            </div>
            <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700 flex items-center justify-between">
              <span class="text-neutral-400">missing</span>
              <span class="font-mono" :class="artifacts.summary?.missing ? 'text-amber-300' : 'text-neutral-300'">{{ artifacts.summary?.missing ?? '-' }}</span>
            </div>
            <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700 flex items-center justify-between">
              <span class="text-neutral-400">file_not_found</span>
              <span class="font-mono" :class="artifacts.summary?.file_not_found ? 'text-rose-300' : 'text-neutral-300'">{{ artifacts.summary?.file_not_found ?? '-' }}</span>
            </div>
            <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700 flex items-center justify-between">
              <span class="text-neutral-400">file_check_error</span>
              <span class="font-mono" :class="artifacts.summary?.file_check_error ? 'text-rose-300' : 'text-neutral-300'">{{ artifacts.summary?.file_check_error ?? '-' }}</span>
            </div>
          </div>
          <div class="text-[10px] text-neutral-500">
            마지막 점검: {{ artifacts.lastChecked ? new Date(artifacts.lastChecked).toLocaleString() : '-' }}
          </div>
          <details v-if="artifacts.rows.length" class="text-[11px] opacity-90">
            <summary class="cursor-pointer">최근 결과 일부</summary>
            <div class="mt-1 space-y-1 font-mono">
              <div v-for="(r, idx) in artifacts.rows.slice(0,5)" :key="idx" class="truncate" :title="r.artifact_path">
                • {{ r.model_id ?? r.id }} {{ r.version ? ('(' + r.version + ')') : '' }} → <span :class="(r.status||r.check) === 'ok' ? 'text-emerald-300' : 'text-rose-300'">{{ r.status || r.check || 'unknown' }}</span>
              </div>
            </div>
          </details>
          <div class="text-[10px] text-neutral-500">
            팁: 백엔드는 MODEL_ARTIFACT_DIR가 영속 볼륨에 마운트되어야 합니다.
          </div>
        </div>
        <!-- Inference / Gating Controls -->
        <div class="p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-3">
          <h2 class="text-sm font-semibold flex items-center gap-2">Inference / Gating
            <button class="btn !py-0.5 !px-2 text-[11px]" :disabled="threshold.loading" @click="fetchThresholds">Refresh</button>
          </h2>
          <div class="grid grid-cols-2 gap-2 text-[11px]">
            <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700 flex items-center justify-between">
              <span class="text-neutral-400">effective threshold</span>
              <span class="font-mono text-neutral-200">{{ threshold.effective ?? '-' }}</span>
            </div>
            <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700 flex items-center justify-between">
              <span class="text-neutral-400">override</span>
              <span class="font-mono" :class="threshold.override!=null ? 'text-amber-300' : 'text-neutral-400'">{{ threshold.override ?? 'none' }}</span>
            </div>
            <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700 flex items-center justify-between">
              <span class="text-neutral-400">auto enabled</span>
              <span class="font-mono" :class="threshold.auto_enabled ? 'text-emerald-300' : 'text-neutral-400'">{{ String(!!threshold.auto_enabled) }}</span>
            </div>
            <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700 flex items-center justify-between">
              <span class="text-neutral-400">interval(s)</span>
              <span class="font-mono text-neutral-200">{{ threshold.interval_sec ?? '-' }}</span>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2 text-[11px]">
            <label class="flex items-center gap-1">
              <span class="text-neutral-400">set override</span>
              <input class="input !py-1 !px-2 w-24" type="number" step="0.01" min="0" max="1" v-model.number="threshold.newOverride" />
            </label>
            <button class="btn !py-0.5 !px-2" :disabled="threshold.loading || threshold.newOverride==null" @click="applyThresholdOverride">Apply</button>
            <button class="btn !py-0.5 !px-2 !bg-neutral-700 hover:!bg-neutral-600" :disabled="threshold.loading" @click="clearThresholdOverride">Clear override</button>
            <span class="text-neutral-500 ml-1">Presets:</span>
            <button class="btn btn-xs" @click="() => threshold.newOverride = 0.90">0.90</button>
            <button class="btn btn-xs" @click="() => threshold.newOverride = 0.92">0.92</button>
            <button class="btn btn-xs" @click="() => threshold.newOverride = 0.94">0.94</button>
          </div>
          <div class="text-[10px] text-neutral-500">임계치 오버라이는 런타임 레버입니다. 영구값은 환경설정에 반영하는 것을 권장합니다.</div>
        </div>
        <!-- Auto Inference Controls -->
        <div class="p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-3">
          <h2 class="text-sm font-semibold flex items-center gap-2">Auto Inference
            <button class="btn !py-0.5 !px-2 text-[11px]" :disabled="autoInf.loading" @click="fetchAutoStatus">Refresh</button>
          </h2>
          <div class="grid grid-cols-2 gap-2 text-[11px]">
            <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700 flex items-center justify-between">
              <span class="text-neutral-400">enabled</span>
              <span class="font-mono" :class="autoInf.enabled ? 'text-emerald-300' : 'text-neutral-400'">{{ String(!!autoInf.enabled) }}</span>
            </div>
            <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700 flex items-center justify-between">
              <span class="text-neutral-400">task_running</span>
              <span class="font-mono" :class="autoInf.task_running ? 'text-emerald-300' : 'text-neutral-400'">{{ String(!!autoInf.task_running) }}</span>
            </div>
            <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700 flex items-center justify-between">
              <span class="text-neutral-400">interval(s)</span>
              <span class="font-mono text-neutral-200">{{ autoInf.interval_sec ?? '-' }}</span>
            </div>
            <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700 flex items-center justify-between">
              <span class="text-neutral-400">last_heartbeat</span>
              <span class="font-mono text-neutral-200">{{ autoInf.last_heartbeat ? new Date(autoInf.last_heartbeat).toLocaleTimeString() : '-' }}</span>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2 text-[11px]">
            <label class="flex items-center gap-1" title="루프 간격(초)">
              <span class="text-neutral-400">interval</span>
              <input class="input !py-1 !px-2 w-24" type="number" min="3" max="600" v-model.number="autoInf.newInterval" />
            </label>
            <button class="btn !py-0.5 !px-2" :disabled="autoInf.loading" @click="enableAutoInf">Enable</button>
            <button class="btn !py-0.5 !px-2 !bg-neutral-700 hover:!bg-neutral-600" :disabled="autoInf.loading" @click="disableAutoInf">Disable</button>
            <span class="text-neutral-500 ml-1">Presets:</span>
            <button class="btn btn-xs" @click="() => autoInf.newInterval = 10">10s</button>
            <button class="btn btn-xs" @click="() => autoInf.newInterval = 15">15s</button>
            <button class="btn btn-xs" @click="() => autoInf.newInterval = 30">30s</button>
          </div>
          <div class="text-[10px] text-neutral-500">Enable 시 interval이 지정되면 서버에 전달됩니다(미지정 시 서버 기본값).</div>
        </div>

        <!-- Inference Diagnostics (Histogram + Decision rate) -->
        <div class="p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-3">
          <h2 class="text-sm font-semibold flex items-center gap-2">Inference Diagnostics
            <button class="btn !py-0.5 !px-2 text-[11px]" :disabled="diag.loading" @click="fetchDiagnostics">Refresh</button>
          </h2>
          <div class="flex items-center gap-2 text-[11px]">
            <span class="text-neutral-400">window</span>
            <button class="btn btn-xs" :class="diag.windowSec===900 ? '!bg-brand-primary/50' : ''" @click="() => { diag.windowSec=900; fetchDiagnostics(); }">15m</button>
            <button class="btn btn-xs" :class="diag.windowSec===3600 ? '!bg-brand-primary/50' : ''" @click="() => { diag.windowSec=3600; fetchDiagnostics(); }">1h</button>
            <button class="btn btn-xs" :class="diag.windowSec===21600 ? '!bg-brand-primary/50' : ''" @click="() => { diag.windowSec=21600; fetchDiagnostics(); }">6h</button>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-[11px]">
            <!-- Histogram -->
            <div>
              <div class="text-neutral-400 mb-1">Probability histogram ({{ (diag.windowSec/60) }}m)</div>
              <div v-if="diag.hist && diag.hist.buckets?.length" class="space-y-1">
                <div v-for="(b, i) in diag.hist.buckets" :key="i" class="flex items-center gap-2">
                  <span class="w-14 text-right font-mono">{{ b.range }}</span>
                  <div class="flex-1 h-2 bg-neutral-900 rounded relative">
                    <div class="h-2 bg-brand-primary/70 rounded" :style="{ width: (b.count / Math.max(1, diag.hist.maxCount) * 100).toFixed(2) + '%' }"></div>
                  </div>
                  <span class="w-10 text-right font-mono">{{ b.count }}</span>
                </div>
                <div class="text-[10px] text-neutral-500 mt-1">samples: {{ diag.hist.total }}</div>
              </div>
              <div v-else class="text-neutral-500">데이터 없음</div>
            </div>
            <!-- Decision rate -->
            <div>
              <div class="text-neutral-400 mb-1">Recent decision rate (last {{ diag.logsN }} logs)</div>
              <div class="p-2 rounded bg-neutral-800/40 border border-neutral-700">
                <div class="flex items-center justify-between">
                  <span>decision rate</span>
                  <span class="font-mono text-neutral-200">{{ diag.decisionRate != null ? (diag.decisionRate*100).toFixed(1) + '%' : '-' }}</span>
                </div>
                <div class="flex items-center justify-between">
                  <span>decisions / total</span>
                  <span class="font-mono text-neutral-200">{{ diag.decisions }} / {{ diag.total }}</span>
                </div>
                <div class="flex items-center justify-between">
                  <span>last log</span>
                  <span class="font-mono text-neutral-200">{{ diag.lastLogTs ? new Date(diag.lastLogTs).toLocaleTimeString() : '-' }}</span>
                </div>
              </div>
              <!-- Decision rate guard -->
              <div class="mt-2 p-2 rounded bg-neutral-800/40 border border-neutral-700 space-y-2">
                <div class="flex items-center justify-between">
                  <span class="text-neutral-400">Decision rate guard</span>
                  <label class="flex items-center gap-2 text-[11px]"><input type="checkbox" v-model="guard.enabled" /> enabled</label>
                </div>
                <div class="grid grid-cols-3 gap-2 text-[11px]">
                  <label class="flex items-center gap-1"><span class="text-neutral-400">min</span><input class="input !py-1 !px-2" type="number" step="0.005" min="0" max="1" v-model.number="guard.min" /></label>
                  <label class="flex items-center gap-1"><span class="text-neutral-400">max</span><input class="input !py-1 !px-2" type="number" step="0.005" min="0" max="1" v-model.number="guard.max" /></label>
                  <label class="flex items-center gap-1"><span class="text-neutral-400">step</span><input class="input !py-1 !px-2" type="number" step="0.005" min="0.001" max="0.2" v-model.number="guard.step" /></label>
                </div>
                <div class="grid grid-cols-2 gap-2 text-[11px]">
                  <label class="flex items-center gap-1" title="조정 쿨다운(ms)"><span class="text-neutral-400">cooldown</span><input class="input !py-1 !px-2" type="number" min="10000" step="10000" v-model.number="guard.cooldownMs" /></label>
                  <label class="flex items-center gap-1" title="검사 주기(ms)"><span class="text-neutral-400">check every</span><input class="input !py-1 !px-2" type="number" min="10000" step="10000" v-model.number="guard.tickMs" /></label>
                </div>
                <div class="text-[10px] text-neutral-500">결정률이 min 미만이면 임계치를 낮추고, max 초과면 임계치를 올립니다(쿨다운 적용).</div>
              </div>
            </div>
          </div>
          <div class="text-[10px] text-neutral-500">히스토그램은 선택한 시간창 기준이며, 결정률은 최근 로그 표본으로 계산합니다.</div>
        </div>
      </div>
    </section>

    <!-- Feature Backfill Runs Panel -->
    <section class="card space-y-3">
      <div class="flex items-center justify-between">
        <h2 class="text-sm font-semibold">Feature Backfill Runs</h2>
        <div class="flex flex-wrap items-center gap-2 text-[11px]">
          <label class="flex items-center gap-1"><span class="text-neutral-400">symbol</span>
            <input class="input !py-1 !px-2 w-24" v-model="bf.symbol" placeholder="auto" />
          </label>
          <label class="flex items-center gap-1"><span class="text-neutral-400">interval</span>
            <input class="input !py-1 !px-2 w-24" v-model="bf.interval" placeholder="auto" />
          </label>
          <label class="flex items-center gap-1"><span class="text-neutral-400">status</span>
            <select class="input !py-1 !px-2" v-model="bf.status">
              <option value="">all</option>
              <option value="running">running</option>
              <option value="success">success</option>
              <option value="error">error</option>
            </select>
          </label>
          <label class="flex items-center gap-1"><span class="text-neutral-400">from</span>
            <input class="input !py-1 !px-2" type="datetime-local" v-model="bf.startedFrom" />
          </label>
          <label class="flex items-center gap-1"><span class="text-neutral-400">to</span>
            <input class="input !py-1 !px-2" type="datetime-local" v-model="bf.startedTo" />
          </label>
          <label class="flex items-center gap-1"><span class="text-neutral-400">page</span>
            <input class="input !py-1 !px-2 w-16" type="number" min="1" v-model.number="bf.page" />
          </label>
          <label class="flex items-center gap-1"><span class="text-neutral-400">size</span>
            <input class="input !py-1 !px-2 w-16" type="number" min="1" max="200" v-model.number="bf.pageSize" />
          </label>
          <label class="flex items-center gap-1"><span class="text-neutral-400">sort</span>
            <select class="input !py-1 !px-2" v-model="bf.sortBy">
              <option value="started_at">started_at</option>
              <option value="finished_at">finished_at</option>
              <option value="inserted">inserted</option>
              <option value="requested_target">requested_target</option>
              <option value="used_window">used_window</option>
              <option value="status">status</option>
              <option value="id">id</option>
            </select>
          </label>
          <label class="flex items-center gap-1">
            <select class="input !py-1 !px-2" v-model="bf.order">
              <option value="desc">desc</option>
              <option value="asc">asc</option>
            </select>
          </label>
          <label class="flex items-center gap-1"><input type="checkbox" v-model="bf.auto" class="accent-brand-primary" :disabled="bf.live" /> {{ `auto ${backfillAutoSec}s` }}</label>
          <label class="flex items-center gap-1"><input type="checkbox" v-model="bf.live" class="accent-brand-primary" /> live (SSE)</label>
          <button class="btn btn-xs" :disabled="bf.loading" @click="fetchBackfillRuns">Refresh</button>
        </div>
      </div>
      <div class="overflow-auto">
        <table class="w-full text-[11px]">
          <thead>
            <tr class="text-neutral-500 border-b border-neutral-800">
              <th class="text-left py-1 px-1">ID</th>
              <th class="text-left py-1 px-1">Status</th>
              <th class="text-left py-1 px-1">Started</th>
              <th class="text-left py-1 px-1">Finished</th>
              <th class="text-right py-1 px-1">Req</th>
              <th class="text-right py-1 px-1">Inserted</th>
              <th class="text-right py-1 px-1">Window</th>
              <th class="text-left py-1 px-1">Open range</th>
              <th class="text-left py-1 px-1">Error</th>
              <th class="text-left py-1 px-1">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in bf.items" :key="r.id" class="border-b border-neutral-800/40 hover:bg-neutral-800/30">
              <td class="py-1 px-1">{{ r.id }}</td>
              <td class="py-1 px-1">
                <span :class="statusClass(r.status)" class="px-2 py-0.5 rounded">{{ r.status }}</span>
              </td>
              <td class="py-1 px-1">{{ fmtTs(r.started_at) }}</td>
              <td class="py-1 px-1">{{ fmtTs(r.finished_at) }}</td>
              <td class="py-1 px-1 text-right">{{ r.requested_target }}</td>
              <td class="py-1 px-1 text-right">{{ r.inserted }}</td>
              <td class="py-1 px-1 text-right">{{ r.used_window }}</td>
              <td class="py-1 px-1">
                <div>{{ r.from_open_time }} → {{ r.to_open_time }}</div>
              </td>
              <td class="py-1 px-1 text-[10px] max-w-[280px] truncate" :title="r.error || ''">{{ r.error || '' }}</td>
              <td class="py-1 px-1">
                <button class="btn btn-xs" @click="viewRun(r)">Detail</button>
              </td>
            </tr>
            <tr v-if="bf.items.length===0"><td colspan="10" class="text-center text-neutral-600 py-2">No runs</td></tr>
          </tbody>
        </table>
      </div>
      <div class="flex items-center justify-between text-[11px]">
        <div class="text-neutral-400">Total: {{ bf.total }} • Page {{ bf.page }} / {{ totalPages }}</div>
        <div class="flex items-center gap-2">
          <button class="btn btn-xs" :disabled="bf.page<=1 || bf.loading" @click="prevPage">Prev</button>
          <button class="btn btn-xs" :disabled="bf.page>=totalPages || bf.loading" @click="nextPage">Next</button>
        </div>
      </div>
      <details v-if="bf.detail" class="text-[11px] opacity-90"><summary class="cursor-pointer">Run #{{ bf.detail.id }} detail</summary>
        <pre class="bg-neutral-900/70 border border-neutral-700 rounded p-2 overflow-auto">{{ bf.detail }}</pre>
      </details>
    </section>
    <section class="card space-y-4">
      <h2 class="text-sm font-semibold">Raw Logs / Debug</h2>
      <textarea readonly class="w-full h-40 bg-neutral-900 border border-neutral-700 rounded p-2 text-[11px] font-mono" :value="logLines.join('\n')"></textarea>
      <div class="flex gap-2 text-xs">
        <button class="btn !py-1 !px-2" @click="clearLogs">Clear</button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue';
import { storeToRefs } from 'pinia';
import { useCalibrationStore } from '../stores/calibration';
import { useOhlcvStore } from '../stores/ohlcv';
import { useInferenceStore } from '../stores/inference';
import { useOhlcvDeltaSync } from '../composables/useOhlcvDeltaSync';
import { useRoute } from 'vue-router';
import ConfirmDialog from '../components/ConfirmDialog.vue';
import http from '../lib/http';
import { connectSSE } from '../lib/sse';
import { confirmPresets } from '../lib/confirmPresets';

interface RiskState {
  session: { starting_equity: number; peak_equity: number; current_equity: number; cumulative_pnl: number; last_reset_ts?: number };
  positions: { symbol: string; size: number; entry_price: number }[];
}

// ------------------------------
// Admin page open loading bar
// ------------------------------
const loadBar = ref<{ active: boolean; total: number; done: number; label: string }>({ active: false, total: 0, done: 0, label: '' });
const loadPct = computed(() => loadBar.value.total > 0 ? Math.round(loadBar.value.done / loadBar.value.total * 100) : 0);
let _loadTimer: any | null = null;
function startLoad(total: number, autoHideMs: number | null = 10000) {
  loadBar.value.active = true;
  loadBar.value.total = Math.max(1, total);
  loadBar.value.done = 0;
  loadBar.value.label = '';
  if (_loadTimer) { clearTimeout(_loadTimer); _loadTimer = null; }
  // Safety auto-hide in case a step hangs (can be disabled by passing null)
  if (autoHideMs != null) {
    _loadTimer = setTimeout(() => { if (loadBar.value.active) loadBar.value.active = false; }, autoHideMs);
  }
}
function endLoad() {
  if (_loadTimer) { clearTimeout(_loadTimer); _loadTimer = null; }
  loadBar.value.active = false;
}
function stepLoad(label: string) {
  loadBar.value.done = Math.min(loadBar.value.total, loadBar.value.done + 1);
  loadBar.value.label = label;
  if (loadBar.value.done >= loadBar.value.total) {
    setTimeout(() => { endLoad(); }, 400);
  }
}

// ------------------------------
// Calibration Controls wiring
// ------------------------------
const calibStore = useCalibrationStore();
const { loading: calibLoading, auto: calibAuto, intervalSec: calibIntervalSec, liveWindowSeconds, liveBins, monitor: calibMonitor } = storeToRefs(calibStore);
const calibIntervals = ['1m','3m','5m','15m','30m','1h','2h','4h','6h','12h','1d'];
const ohlcv = useOhlcvStore();
// Inference store for Playground controls
const infStore = useInferenceStore();
const { auto: infAuto, intervalSec: infIntervalSec, threshold: infThreshold, loading: infLoading } = storeToRefs(infStore);
const { runOnce: infRunOnce, toggleAuto: infToggleAuto, setIntervalSec: infSetIntervalSec, setThreshold: infSetThreshold } = infStore;
// Local models for inputs
const calibIntervalSecModel = ref<number>(15);
const calibLiveWindowModel = ref<number>(3600);
const calibLiveBinsModel = ref<number>(10);
onMounted(() => {
  // Initialize local models from store
  try {
    calibIntervalSecModel.value = calibIntervalSec.value || 15;
    calibLiveWindowModel.value = liveWindowSeconds.value || 3600;
    calibLiveBinsModel.value = liveBins.value || 10;
    if (!ohlcv.interval) ohlcv.initDefaults(ohlcv.symbol, '15m');
    // Init inference local models
    infIntervalSecModel.value = infIntervalSec.value || 5;
    infThresholdModel.value = infThreshold.value || 0.5;
  } catch { /* ignore */ }
});
function onCalibIntervalChange(){ calibFetchAll(); }
function calibFetchAll(){ try { calibStore.fetchAll(); } catch { /* ignore */ } }
function calibToggleAuto(){ try { calibStore.toggleAuto(); } catch { /* ignore */ } }
function setCalibInterval(){ try { calibStore.setIntervalSec(Math.max(5, Math.min(120, Math.floor(calibIntervalSecModel.value||15)))); } catch { /* ignore */ } }
function applyLiveWindow(){
  const v = Math.max(60, Math.floor(calibLiveWindowModel.value||3600));
  if (typeof (calibStore as any).setLiveWindowSeconds === 'function') {
    (calibStore as any).setLiveWindowSeconds(v);
  } else {
    liveWindowSeconds.value = v;
  }
  calibFetchAll();
}
function applyLiveBins(){
  const v = Math.max(5, Math.min(50, Math.floor(calibLiveBinsModel.value||10)));
  if (typeof (calibStore as any).setLiveBins === 'function') {
    (calibStore as any).setLiveBins(v);
  } else {
    liveBins.value = v;
  }
  calibFetchAll();
}
const calibSampleCountDisplay = computed(() => {
  const sc = calibMonitor.value?.last_snapshot?.sample_count;
  return typeof sc === 'number' ? sc : '—';
});

// ------------------------------
// Inference Playground Controls wiring
// ------------------------------
const infIntervalSecModel = ref<number>(5);
const infThresholdModel = ref<number>(0.5);
function onInfIntervalChange(){
  // When interval (ohlcv) changes, just re-run inference once to reflect new context
  try { infRunOnce(); } catch { /* ignore */ }
}
function setInfInterval(){
  try { infSetIntervalSec(Math.max(1, Math.min(30, Math.floor(infIntervalSecModel.value||5)))); } catch { /* ignore */ }
}
function applyInfThreshold(){
  const v = Number(infThresholdModel.value);
  if (!isFinite(v)) return;
  try { infSetThreshold(Math.max(0, Math.min(1, v))); } catch { /* ignore */ }
}

// ------------------------------
// OHLCV Controls wiring
// ------------------------------
const { wsCtl: ohlcvWsCtl } = useOhlcvDeltaSync();
const ohlcvFilling = ref<boolean>(false);
const ohlcvWsStatus = computed(() => ohlcvWsCtl.connected.value ? 'On' : 'Off');
function ohlcvWsToggle(){ if (ohlcvWsCtl.connected.value) ohlcvWsCtl.disconnect(); else ohlcvWsCtl.connect(); }
const ohlcvEtaDisplay = computed(() => {
  const st: any = ohlcv.yearBackfill;
  if (!st || st.eta_seconds == null) return '';
  const s = Math.round(st.eta_seconds);
  if (s < 60) return s + 's';
  const m = Math.floor(s/60); const sec = s % 60; return m + 'm' + (sec>0 ? sec + 's' : '');
});
async function ohlcvFillGaps(){
  if (ohlcvFilling.value) return;
  ohlcvFilling.value = true;
  try {
    const url = `/api/ohlcv/gaps/fill?symbol=${encodeURIComponent(ohlcv.symbol)}&interval=${encodeURIComponent(ohlcv.interval)}`;
    await (await fetch(url, { method: 'POST' })).json();
    await ohlcv.fetchRecent();
    await ohlcv.fetchGaps();
    await ohlcv.fetchMeta();
  } catch { /* ignore */ }
  finally { ohlcvFilling.value = false; }
}

const loading = ref({ fastUpgrade: false, training: false, labeler: false, risk: false, schema: false, bootstrap: false, featBackfill: false, artifacts: false, reset: false });
const error = ref<string | null>(null);
const successMsg = ref<string | null>(null);
const riskState = ref<RiskState | null>(null);
const logLines = ref<string[]>([]);
const schemaResult = ref<string[]>([]);
const lastResetAt = ref<string | null>(null);
const resetThenBootstrap = ref<boolean>(false);
const dropFeatures = ref<boolean>(false);
// Artifacts verify state
interface ArtifactSummary { ok: number; missing: number; file_not_found: number; file_check_error: number }
const artifacts = ref<{ summary: ArtifactSummary | null; rows: any[]; lastChecked: string | null }>({ summary: null, rows: [], lastChecked: null });
// Env helpers (allow runtime override via window for local debugging)
const ENV: any = (import.meta as any).env || {};
function readEnvMs(name: string, def: number): number {
  const v = (globalThis as any)[name] ?? ENV[name];
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : def;
}
function readEnvFloat(name: string, def: number): number {
  const v = (globalThis as any)[name] ?? ENV[name];
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : def;
}
const RISK_AUTO_MS = readEnvMs('VITE_RISK_AUTO_INTERVAL_MS', 15000);
const BACKFILL_POLL_MS = readEnvMs('VITE_BACKFILL_POLL_MS', 10000);
const DEFAULT_MIN_AUC = readEnvFloat('VITE_BOOTSTRAP_MIN_AUC', 0.65);
const DEFAULT_MAX_ECE = readEnvFloat('VITE_BOOTSTRAP_MAX_ECE', 0.05);
const riskAutoSec = Math.max(1, Math.round(RISK_AUTO_MS / 1000));
const backfillAutoSec = Math.max(1, Math.round(BACKFILL_POLL_MS / 1000));

const bootstrap = ref<{ backfill_year: boolean; fill_gaps: boolean; retry_fill_gaps: boolean; dry_run: boolean; train_sentiment: boolean; skip_promotion: boolean; feature_target: number; feature_window?: number | null; min_auc?: number | null; max_ece?: number | null }>({ backfill_year: true, fill_gaps: true, retry_fill_gaps: true, dry_run: true, train_sentiment: true, skip_promotion: false, feature_target: 600, feature_window: null, min_auc: DEFAULT_MIN_AUC, max_ece: DEFAULT_MAX_ECE });
const bootstrapResult = ref<any | null>(null);
// UI state for Bootstrap viewer tabs
const bootstrapTab = ref<'summary'|'meta'|'raw'>('summary');
// Computed summary/meta/raw views derived from bootstrapResult
const summary = computed(() => {
  const r = bootstrapResult.value || {};
  // Try common shapes: r.metrics.{auc,ece,samples,window}, r.report, r.eval
  const auc = r?.metrics?.auc ?? r?.report?.auc ?? r?.eval?.auc ?? null;
  const ece = r?.metrics?.ece ?? r?.report?.ece ?? r?.eval?.ece ?? null;
  const samples = r?.metrics?.samples ?? r?.report?.samples ?? r?.n_samples ?? r?.samples ?? null;
  const windowVal = r?.metrics?.window ?? r?.used_window ?? r?.window ?? bootstrap.value.feature_window ?? null;
  const minA = bootstrap.value.min_auc ?? null;
  const maxE = bootstrap.value.max_ece ?? null;
  const aucPass = (typeof auc === 'number' && typeof minA === 'number') ? (auc >= minA) : null;
  const ecePass = (typeof ece === 'number' && typeof maxE === 'number') ? (ece <= maxE) : null;
  let gate: 'PASS'|'FAIL'|'N/A' = 'N/A';
  if (aucPass != null && ecePass != null) gate = (aucPass && ecePass) ? 'PASS' : 'FAIL';
  return {
    auc, ece, samples, window: windowVal, minAuc: minA, maxEce: maxE,
    aucPass, ecePass, gate,
    aucDisplay: (typeof auc === 'number' ? auc.toFixed(3) : '-'),
    eceDisplay: (typeof ece === 'number' ? ece.toFixed(3) : '-'),
    samplesDisplay: (samples != null ? String(samples) : '-'),
    windowDisplay: (windowVal != null ? String(windowVal) : '-'),
    minAucDisplay: (typeof minA === 'number' ? minA.toFixed(2) : '-'),
    maxEceDisplay: (typeof maxE === 'number' ? maxE.toFixed(2) : '-'),
  };
});
const metaPairs = computed((): Array<{ key: string; value: any }> => {
  const r = bootstrapResult.value;
  if (!r || typeof r !== 'object') return [];
  const meta: Record<string, any> = {
    status: r.status,
    started_at: r.started_at ?? r.start_time,
    finished_at: r.finished_at ?? r.end_time,
    model_id: r.model_id ?? r.model?.id,
    version: r.model?.version,
    used_window: r.used_window ?? r.metrics?.window,
    requested_target: r.requested_target ?? r.metrics?.target ?? bootstrap.value.feature_target,
    train_sentiment: bootstrap.value.train_sentiment,
    dry_run: bootstrap.value.dry_run,
    skip_promotion: bootstrap.value.skip_promotion,
  };
  return Object.entries(meta)
    .filter(([_, v]) => v !== undefined)
    .map(([key, value]) => ({ key, value }));
});
const rawJson = computed(() => bootstrapResult.value ? JSON.stringify(bootstrapResult.value, null, 2) : '');
const labelerMinAge = ref<number>(120);
const labelerBatch = ref<number>(1000);
const riskAuto = ref<boolean>(false);
const riskLive = ref<boolean>(false);
let _riskSSE: { close: () => void } | null = null;

// Bottom training params (bottom-only)
const bottomLookahead = ref<number>(30);
const bottomDrawdown = ref<number>(0.005);
const bottomRebound = ref<number>(0.003);
// Track if localStorage provided bottom params to avoid overriding user's saved values
const hasLocalBottomParams = ref<boolean>(false);

// Load bottom default params from server preview to keep AdminPanel in sync with backend defaults
async function loadBottomDefaultsFromServer() {
  try {
    // If user has saved local params, respect them and skip auto-sync
    if (hasLocalBottomParams.value) return;
    const r = await http.get('/api/training/bottom/preview', { params: { limit: 1200 } });
    const p = (r as any)?.data?.params;
    if (p && typeof p.lookahead === 'number' && typeof p.drawdown === 'number' && typeof p.rebound === 'number') {
      bottomLookahead.value = Math.max(1, Math.floor(p.lookahead));
      bottomDrawdown.value = Math.max(0, Number(p.drawdown));
      bottomRebound.value = Math.max(0, Number(p.rebound));
    }
  } catch {
    // ignore network/auth errors; keep current values
  }
}

// Manual refresh button handler: clear local overrides and pull from server
async function handleReloadBottomDefaults() {
  try {
    try {
      localStorage.removeItem('admin_bottom_lookahead');
      localStorage.removeItem('admin_bottom_drawdown');
      localStorage.removeItem('admin_bottom_rebound');
    } catch { /* ignore */ }
    hasLocalBottomParams.value = false;
    await loadBottomDefaultsFromServer();
    successMsg.value = '서버 기본값을 불러왔습니다';
  } catch (e:any) {
    error.value = e?.__friendlyMessage || e?.message || '기본값 불러오기 실패';
  }
}

// Inference threshold / gating controls state
const threshold = ref<{ effective: number | null; override: number | null; auto_enabled?: boolean; interval_sec?: number | null; newOverride: number | null; loading: boolean }>({
  effective: null,
  override: null,
  auto_enabled: undefined,
  interval_sec: undefined,
  newOverride: null,
  loading: false,
});

async function fetchThresholds() {
  threshold.value.loading = true; error.value = null; successMsg.value = null;
  try {
    const r = await http.get('/admin/inference/thresholds');
    const d: any = r.data || {};
    threshold.value.effective = (typeof d.effective_threshold === 'number') ? d.effective_threshold : (typeof d.effective === 'number' ? d.effective : (typeof d.current === 'number' ? d.current : null));
    threshold.value.override = (d.override ?? d.threshold_override ?? (d.overrides ? d.overrides.threshold : null));
    threshold.value.auto_enabled = (d.auto_enabled ?? d.enabled ?? (d.auto ? d.auto.enabled : undefined));
    threshold.value.interval_sec = (d.interval_sec ?? (d.auto ? d.auto.interval_sec : undefined));
    successMsg.value = 'thresholds fetched';
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
  } finally {
    threshold.value.loading = false;
  }
}

async function applyThresholdOverride() {
  if (threshold.value.newOverride == null || !(threshold.value.newOverride >= 0 && threshold.value.newOverride <= 1)) {
    error.value = '0과 1 사이의 값을 입력하세요';
    return;
  }
  threshold.value.loading = true; error.value = null; successMsg.value = null;
  try {
    const payload = { threshold_override: threshold.value.newOverride } as any;
    const r = await http.post('/admin/inference/auto/threshold', payload);
    successMsg.value = r.data?.status || 'override applied';
    await fetchThresholds();
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
  } finally {
    threshold.value.loading = false;
  }
}

async function clearThresholdOverride() {
  threshold.value.loading = true; error.value = null; successMsg.value = null;
  try {
    const r = await http.post('/admin/inference/auto/threshold', { threshold_override: null });
    successMsg.value = r.data?.status || 'override cleared';
    threshold.value.newOverride = null;
    await fetchThresholds();
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
  } finally {
    threshold.value.loading = false;
  }
}

// Auto Inference controls
const autoInf = ref<{ enabled: boolean; task_running: boolean; interval_sec: number | null; last_heartbeat: string | null; newInterval: number | null; loading: boolean }>({
  enabled: false,
  task_running: false,
  interval_sec: null,
  last_heartbeat: null,
  newInterval: null,
  loading: false,
});

async function fetchAutoStatus() {
  autoInf.value.loading = true; error.value = null; successMsg.value = null;
  try {
    const r = await http.get('/admin/inference/auto/status');
    const d: any = r.data || {};
    autoInf.value.enabled = !!(d.enabled ?? d.auto_enabled ?? d.active);
    autoInf.value.task_running = !!(d.task_running ?? d.running ?? d.worker_running);
    autoInf.value.interval_sec = (typeof d.interval_sec === 'number') ? d.interval_sec : (typeof d.interval === 'number' ? d.interval : null);
    autoInf.value.last_heartbeat = d.last_heartbeat ?? d.last_run_at ?? null;
    successMsg.value = 'auto inference status fetched';
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
  } finally {
    autoInf.value.loading = false;
  }
}

async function enableAutoInf() {
  autoInf.value.loading = true; error.value = null; successMsg.value = null;
  try {
    const params: any = {};
    if (autoInf.value.newInterval && autoInf.value.newInterval > 0) params.interval_sec = Math.floor(autoInf.value.newInterval);
    const r = await http.post('/admin/inference/auto/enable', null, { params });
    successMsg.value = r.data?.status || 'auto inference enabled';
    await fetchAutoStatus();
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
  } finally {
    autoInf.value.loading = false;
  }
}

async function disableAutoInf() {
  autoInf.value.loading = true; error.value = null; successMsg.value = null;
  try {
    const r = await http.post('/admin/inference/auto/disable');
    successMsg.value = r.data?.status || 'auto inference disabled';
    await fetchAutoStatus();
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
  } finally {
    autoInf.value.loading = false;
  }
}

// Confirm dialog state & helpers
type ConfirmFn = () => void;
interface ConfirmState { open: boolean; title: string; message: string; requireText?: string; delayMs?: number; onConfirm?: ConfirmFn | null }
const confirm = ref<ConfirmState>({ open: false, title: '', message: '', requireText: undefined, delayMs: 1200, onConfirm: null });
function openConfirm(opts: { title: string; message: string; requireText?: string; delayMs?: number; onConfirm: ConfirmFn }) {
  confirm.value.title = opts.title;
  confirm.value.message = opts.message;
  confirm.value.requireText = opts.requireText;
  confirm.value.delayMs = opts.delayMs ?? 1200;
  confirm.value.onConfirm = () => { try { opts.onConfirm(); } finally { confirm.value.open = false; } };
  confirm.value.open = true;
}

// Local persistence for bootstrap form
const LS_BOOTSTRAP_KEY = 'admin_bootstrap_form_v1';
onMounted(() => {
  try {
    const saved = localStorage.getItem(LS_BOOTSTRAP_KEY);
    if (saved) {
      const v = JSON.parse(saved);
      bootstrap.value = { ...bootstrap.value, ...v };
    }
  } catch { /* ignore */ }
});
watch(bootstrap, (v) => {
  try { localStorage.setItem(LS_BOOTSTRAP_KEY, JSON.stringify(v)); } catch { /* ignore */ }
}, { deep: true });

function pushLog(msg: string) { logLines.value.unshift(new Date().toLocaleTimeString() + ' ' + msg); if (logLines.value.length > 200) logLines.value.pop(); }
function clearLogs() { logLines.value = []; }

async function fastUpgrade() {
  loading.value.fastUpgrade = true; error.value = null; successMsg.value = null;
  try {
    const r = await http.post('/admin/fast_startup/upgrade');
    successMsg.value = r.data?.status || 'upgraded';
    pushLog('[upgrade] ' + JSON.stringify(r.data));
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
    pushLog('[upgrade:error] ' + error.value);
  } finally { loading.value.fastUpgrade = false; }
}

async function runTraining() {
  loading.value.training = true; error.value = null; successMsg.value = null;
  try {
    // Bottom-only payload
    const payload: any = {
      trigger: 'manual_ui',
      target: 'bottom',
      bottom_lookahead: bottomLookahead.value,
      bottom_drawdown: bottomDrawdown.value,
      bottom_rebound: bottomRebound.value,
    };
    const r = await http.post('/api/training/run', payload);
    successMsg.value = r.data?.status || 'training started';
    pushLog('[training] ' + JSON.stringify(r.data));
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
    pushLog('[training:error] ' + error.value);
  } finally { loading.value.training = false; }
}

async function verifyArtifacts() {
  loading.value.artifacts = true; error.value = null; successMsg.value = null;
  try {
    const r = await http.get('/admin/models/artifacts/verify');
    const s = r.data?.summary as ArtifactSummary | undefined;
    artifacts.value.summary = s ?? null;
    const rows = Array.isArray(r.data?.rows) ? r.data.rows : (Array.isArray(r.data) ? r.data : []);
    artifacts.value.rows = rows;
    artifacts.value.lastChecked = new Date().toISOString();
    successMsg.value = 'artifacts verified';
    pushLog('[artifacts:verify] ' + JSON.stringify(r.data?.summary || {}));
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
    pushLog('[artifacts:verify:error] ' + error.value);
  } finally { loading.value.artifacts = false; }
}

async function runLabeler() {
  loading.value.labeler = true; error.value = null; successMsg.value = null;
  try {
    // Use force=true to work even if AUTO_LABELER_ENABLED is false at runtime
    const params = new URLSearchParams({ force: 'true', min_age_seconds: String(Math.max(0, labelerMinAge.value||0)), limit: String(Math.max(1, labelerBatch.value||1)) });
    const r = await http.post('/api/inference/labeler/run?' + params.toString());
    successMsg.value = r.data?.status || 'labeler done';
    pushLog('[labeler] ' + JSON.stringify(r.data));
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
    pushLog('[labeler:error] ' + error.value);
  } finally { loading.value.labeler = false; }
}

async function fetchRisk() {
  loading.value.risk = true; error.value = null; successMsg.value = null;
  try {
    const r = await http.get('/api/risk/state');
    riskState.value = r.data;
    pushLog('[risk] fetched');
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
    pushLog('[risk:error] ' + error.value);
  } finally { loading.value.risk = false; }
}

async function ensureAllTables() {
  loading.value.schema = true; error.value = null; successMsg.value = null; schemaResult.value = [];
  try {
    const r = await http.post('/api/admin/schema/ensure');
    const tables: string[] = r.data?.tables || [];
    schemaResult.value = tables;
    successMsg.value = 'tables ensured (' + tables.length + ')';
    pushLog('[schema] ensured ' + tables.length + ' tables');
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
    pushLog('[schema:error] ' + error.value);
  } finally { loading.value.schema = false; }
}

async function runBootstrap(dry: boolean) {
  loading.value.bootstrap = true; error.value = null; successMsg.value = null; bootstrapResult.value = null;
  try {
    const payload = { ...bootstrap.value, dry_run: dry || bootstrap.value.dry_run };
    const r = await http.post('/admin/bootstrap', payload);
    bootstrapResult.value = r.data;
    successMsg.value = r.data?.status || 'ok';
    pushLog('[bootstrap] ' + JSON.stringify(r.data));
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
    pushLog('[bootstrap:error] ' + error.value);
  } finally {
    loading.value.bootstrap = false;
  }
}

function applyPreset(name: 'conservative'|'standard'|'relaxed') {
  if (name === 'conservative') { bootstrap.value.min_auc = 0.70; bootstrap.value.max_ece = 0.03; }
  else if (name === 'standard') { bootstrap.value.min_auc = 0.65; bootstrap.value.max_ece = 0.05; }
  else { bootstrap.value.min_auc = 0.60; bootstrap.value.max_ece = 0.08; }
}

function downloadBootstrapResult() {
  try {
    const blob = new Blob([JSON.stringify(bootstrapResult.value, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'bootstrap_result.json';
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 0);
  } catch { /* ignore */ }
}

function copyBootstrapResult() {
  try {
    if (!bootstrapResult.value) return;
    const txt = JSON.stringify(bootstrapResult.value, null, 2);
    navigator.clipboard.writeText(txt);
    successMsg.value = 'copied to clipboard';
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message || 'copy failed';
  }
}

function resetBootstrapDefaults() {
  bootstrap.value.min_auc = DEFAULT_MIN_AUC;
  bootstrap.value.max_ece = DEFAULT_MAX_ECE;
  successMsg.value = 'reset to defaults';
}

async function runFeatBackfill() {
  loading.value.featBackfill = true; error.value = null; successMsg.value = null;
  try {
    const params: any = { target: bootstrap.value.feature_target };
    if (bootstrap.value.feature_window && bootstrap.value.feature_window > 0) params.window = bootstrap.value.feature_window;
    const r = await http.post('/admin/features/backfill', null, { params });
    successMsg.value = r.data?.status || 'ok';
    bootstrapResult.value = r.data;
    pushLog('[features:backfill] ' + JSON.stringify(r.data));
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
    pushLog('[features:backfill:error] ' + error.value);
  } finally {
    loading.value.featBackfill = false;
  }
}

// (auth UI removed per request)

// Auto-run a dry-run bootstrap once per session on entering Admin panel
let riskTimer: any | null = null;
const route = useRoute();
onMounted(async () => {
  // restore last reset time if any
  try { const t = localStorage.getItem('admin_last_reset_at'); if (t) lastResetAt.value = t; } catch {}
  // If no model exists, keep loading bar up and bootstrap until a model is available
  try {
    const exists = await modelExists();
    if (!exists) {
      await bootstrapUntilModel();
    }
  } catch { /* ignore */ }

  // Load initial data with progress bar (only if not already showing blocking bootstrap bar)
  if (!loadBar.value.active) startLoad(5);
  try { await fetchThresholds(); } catch {} finally { stepLoad('thresholds'); }
  try { await fetchAutoStatus(); } catch {} finally { stepLoad('auto'); }
  try { await fetchBackfillRuns(); } catch {} finally { stepLoad('feature runs'); }
  try { await fetchRisk(); } catch {} finally { stepLoad('risk'); }
  try { await verifyArtifacts(); } catch {} finally { stepLoad('artifacts'); }
  // Final guard to ensure hiding even if counts drift
  if (loadBar.value.active) endLoad();
  // Start guard if enabled
  if (guard.value.enabled) startGuardTimer();
  // Apply deep-link filters from query (bf_symbol, bf_interval, bf_status, bf_live)
  try {
    const q: any = route.query || {};
    if (typeof q.bf_symbol === 'string') bf.value.symbol = q.bf_symbol;
    if (typeof q.bf_interval === 'string') bf.value.interval = q.bf_interval;
    if (typeof q.bf_status === 'string') bf.value.status = q.bf_status as any;
    if (q.bf_live === '1' || q.bf_live === 1 || q.bf_live === true) { bf.value.live = true; bf.value.auto = false; }
  } catch { /* ignore */ }
  // Load persisted toggles
  try {
    const v1 = localStorage.getItem('admin_risk_auto'); if (v1!=null) riskAuto.value = v1 === '1';
    const v2 = localStorage.getItem('admin_risk_live'); if (v2!=null) riskLive.value = v2 === '1';
    const b1 = localStorage.getItem('admin_bottom_lookahead');
    const b2 = localStorage.getItem('admin_bottom_drawdown');
    const b3 = localStorage.getItem('admin_bottom_rebound');
    if (b1!=null || b2!=null || b3!=null) hasLocalBottomParams.value = true;
    if (b1!=null) bottomLookahead.value = Math.max(1, Number(b1) || bottomLookahead.value);
    if (b2!=null) bottomDrawdown.value = Math.max(0, Number(b2) || bottomDrawdown.value);
    if (b3!=null) bottomRebound.value = Math.max(0, Number(b3) || bottomRebound.value);
  } catch { /* ignore */ }
  // If user has no saved params, sync defaults from server preview
  if (!hasLocalBottomParams.value) {
    loadBottomDefaultsFromServer();
  }
  try {
    const v3 = localStorage.getItem('admin_bf_auto'); if (v3!=null) bf.value.auto = v3 === '1';
    const v4 = localStorage.getItem('admin_bf_live'); if (v4!=null) bf.value.live = v4 === '1';
  } catch { /* ignore */ }
  // kick initial runs fetch and start polling if enabled (SSE will start via watcher when live=true)
  startRunsPolling();
  // initial risk fetch
  // start risk auto refresh
  riskTimer = setInterval(() => { if (riskAuto.value && !riskLive.value) fetchRisk(); }, RISK_AUTO_MS);
});
onBeforeUnmount(() => {
  stopRunsPolling();
  stopRunsSSE();
  if (riskTimer) { clearInterval(riskTimer); riskTimer = null; }
  try { _riskSSE && _riskSSE.close(); } catch {}
  _riskSSE = null;
  stopGuardTimer();
});

// ------------------------------
// Feature Backfill Runs helpers
// ------------------------------
interface BackfillRunRow {
  id: number; symbol: string; interval: string; requested_target: number; used_window: number; inserted: number;
  from_open_time?: number|null; to_open_time?: number|null; from_close_time?: number|null; to_close_time?: number|null;
  source_fetched?: number|null; start_index?: number|null; end_index?: number|null; status: string; error?: string|null;
  started_at: string; finished_at?: string|null;
}
interface BackfillRunsResponse { items: BackfillRunRow[]; total: number; page: number; page_size: number }
const bf = ref<{
  // data
  items: BackfillRunRow[];
  total: number;
  // filters
  symbol: string;
  interval: string;
  status: ''|'running'|'success'|'error';
  startedFrom: string; // datetime-local
  startedTo: string;   // datetime-local
  // pagination/sort
  page: number;
  pageSize: number;
  sortBy: 'started_at'|'finished_at'|'inserted'|'requested_target'|'used_window'|'status'|'id';
  order: 'asc'|'desc';
  // ui
  detail: BackfillRunRow | null;
  auto: boolean;
  live: boolean;
  loading: boolean;
  _timer: any | null;
  _sse: { close: () => void } | null;
}>(
  {
    items: [], total: 0,
    symbol: '', interval: '', status: '', startedFrom: '', startedTo: '',
    page: 1, pageSize: 50, sortBy: 'started_at', order: 'desc',
    detail: null, auto: true, live: false, loading: false, _timer: null, _sse: null,
  }
);
const totalPages = computed(() => Math.max(1, Math.ceil(bf.value.total / Math.max(1, bf.value.pageSize))));
function fmtTs(ts: any): string { if (!ts) return '-'; try { return new Date(ts).toLocaleString(); } catch { return String(ts); } }
function statusClass(s: string) {
  if (s === 'success') return 'bg-emerald-700/30 text-emerald-300 border border-emerald-700/40';
  if (s === 'running') return 'bg-indigo-700/30 text-indigo-300 border border-indigo-700/40';
  if (s === 'error') return 'bg-rose-700/30 text-rose-300 border border-rose-700/40';
  return 'bg-neutral-700/30 text-neutral-300 border border-neutral-700/40';
}
function toEpochSeconds(dtLocal: string | undefined): number | null {
  if (!dtLocal) return null;
  try {
    const ms = new Date(dtLocal).getTime();
    if (!isFinite(ms)) return null;
    return Math.floor(ms / 1000);
  } catch { return null; }
}
async function fetchBackfillRuns() {
  bf.value.loading = true; error.value = null;
  try {
    const params: any = {
      page: Math.max(1, bf.value.page),
      page_size: Math.max(1, Math.min(200, bf.value.pageSize)),
      sort_by: bf.value.sortBy,
      order: bf.value.order,
    };
    if (bf.value.status) params.status = bf.value.status;
    if (bf.value.symbol) params.symbol = bf.value.symbol;
    if (bf.value.interval) params.interval = bf.value.interval;
    const sf = toEpochSeconds(bf.value.startedFrom); if (sf) params.started_from = sf;
    const st = toEpochSeconds(bf.value.startedTo); if (st) params.started_to = st;
    const r = await http.get('/api/features/backfill/runs', { params });
    // Support new and legacy response shapes
    if (Array.isArray(r.data)) {
      bf.value.items = r.data as BackfillRunRow[];
      bf.value.total = r.data.length;
      bf.value.page = 1;
    } else {
      const data = r.data as BackfillRunsResponse;
      bf.value.items = Array.isArray(data.items) ? data.items : [];
      bf.value.total = typeof data.total === 'number' ? data.total : 0;
      bf.value.page = typeof data.page === 'number' ? data.page : bf.value.page;
      // Keep local pageSize to maintain control locally (server echoes page_size)
    }
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
  } finally { bf.value.loading = false; }
}
async function viewRun(row: BackfillRunRow) {
  try {
    const r = await http.get(`/api/features/backfill/runs/${row.id}`);
    bf.value.detail = r.data || row;
  } catch { /* noop */ }
}
function startRunsPolling() {
  stopRunsPolling();
  bf.value._timer = setInterval(() => { if (bf.value.auto && !bf.value.live) fetchBackfillRuns(); }, BACKFILL_POLL_MS);
}
function stopRunsPolling() { if (bf.value._timer) { clearInterval(bf.value._timer); bf.value._timer = null; } }

function startRunsSSE() {
  stopRunsSSE();
  const params = new URLSearchParams();
  if (bf.value.symbol) params.set('symbol', bf.value.symbol);
  if (bf.value.interval) params.set('interval', bf.value.interval);
  const url = '/stream/runs' + (params.toString() ? ('?' + params.toString()) : '');
  bf.value._sse = connectSSE({ url, heartbeatTimeoutMs: 20000 }, {
    onMessage: (evt) => {
      const d = evt?.data as any;
      if (d && Array.isArray(d.items)) {
        bf.value.items = d.items as BackfillRunRow[];
        // keep total at least items length
        bf.value.total = Math.max(bf.value.total, bf.value.items.length);
      }
    }
  });
}
function stopRunsSSE() { try { bf.value._sse && bf.value._sse.close(); } catch {} bf.value._sse = null; }

function prevPage() { if (bf.value.page > 1) { bf.value.page -= 1; fetchBackfillRuns(); } }
function nextPage() { if (bf.value.page < totalPages.value) { bf.value.page += 1; fetchBackfillRuns(); } }

// Filters change -> reset to first page and fetch (debounced lightly by watch scheduling)
watch(() => [bf.value.symbol, bf.value.interval, bf.value.status, bf.value.startedFrom, bf.value.startedTo, bf.value.pageSize, bf.value.sortBy, bf.value.order], () => {
  bf.value.page = 1;
  fetchBackfillRuns();
  if (bf.value.live) startRunsSSE();
});

watch(() => bf.value.live, (v) => {
  if (v) {
    bf.value.auto = false; // prevent double updates
    startRunsSSE();
  } else {
    stopRunsSSE();
  }
});

watch(riskLive, (v) => {
  if (v) {
    // stop auto polling to avoid duplicate updates
    riskAuto.value = false;
    try { _riskSSE && _riskSSE.close(); } catch {}
    _riskSSE = connectSSE({ url: '/stream/risk', heartbeatTimeoutMs: 15000 }, {
      onMessage: (evt) => {
        const d = evt?.data as any;
        if (d && typeof d === 'object') riskState.value = d as any;
      }
    });

  } else {
    try { _riskSSE && _riskSSE.close(); } catch {}
    _riskSSE = null;
  }
  try { localStorage.setItem('admin_risk_live', v ? '1' : '0'); } catch {}
});

watch(riskAuto, (v) => { try { localStorage.setItem('admin_risk_auto', v ? '1' : '0'); } catch {} });
watch(() => bf.value.auto, (v) => { try { localStorage.setItem('admin_bf_auto', v ? '1' : '0'); } catch {} });
watch(() => bf.value.live, (v) => { try { localStorage.setItem('admin_bf_live', v ? '1' : '0'); } catch {} });

// Decision rate guard state (declare before watchers to avoid TDZ)
const guard = ref<{ enabled: boolean; min: number; max: number; step: number; cooldownMs: number; tickMs: number; _lastAdjAt: number | null; _timer: any | null }>({
  enabled: false,
  min: 0.01,
  max: 0.15,
  step: 0.01,
  cooldownMs: 5 * 60_000,
  tickMs: 60_000,
  _lastAdjAt: null,
  _timer: null,
});

// Start/stop decision rate guard when toggled
watch(() => guard.value.enabled, (v) => {
  if (v) startGuardTimer(); else stopGuardTimer();
});

// Persist bottom params once (avoid duplicate watchers that leak over time)
watch(bottomLookahead, (v) => { try { localStorage.setItem('admin_bottom_lookahead', String(v)); } catch {} });
watch(bottomDrawdown, (v) => { try { localStorage.setItem('admin_bottom_drawdown', String(v)); } catch {} });
watch(bottomRebound, (v) => { try { localStorage.setItem('admin_bottom_rebound', String(v)); } catch {} });

// No target switching; bottom-only

// Confirm wrappers for risky actions
function confirmEnsureTables() {
  openConfirm({ ...confirmPresets.ensureTables(), onConfirm: () => { ensureAllTables(); } });
}
function confirmBootstrap(dry: boolean) {
  openConfirm({ ...confirmPresets.bootstrapRun(dry), onConfirm: () => { runBootstrap(dry); } });
}
function confirmFeatBackfill() {
  openConfirm({ ...confirmPresets.featBackfill(bootstrap.value.feature_target, bootstrap.value.feature_window), onConfirm: () => { runFeatBackfill(); } });
}
function confirmTraining() {
  openConfirm({ ...confirmPresets.trainingTrigger(), onConfirm: () => { runTraining(); } });
}
function confirmLabeler() {
  openConfirm({ ...confirmPresets.labelerRun(labelerMinAge.value, labelerBatch.value), onConfirm: () => { runLabeler(); } });
}
function confirmFastUpgrade() {
  openConfirm({ ...confirmPresets.fastUpgrade(), onConfirm: () => { fastUpgrade(); } });
}

// ------------------------------
// Model reset (danger)
// ------------------------------
async function resetModels() {
  error.value = null; successMsg.value = null; loading.value.reset = true;
  try {
    const payload: any = { drop_features: !!dropFeatures.value };
    const r = await http.post('/admin/models/reset', payload);
    successMsg.value = r.data?.status || 'reset done';
    pushLog('[models:reset] ' + JSON.stringify(r.data || {}));
    lastResetAt.value = new Date().toISOString();
    try { localStorage.setItem('admin_last_reset_at', lastResetAt.value); } catch {}
    // Refresh key panels
    try { await verifyArtifacts(); } catch {}
    try { await fetchThresholds(); } catch {}
    try { await fetchAutoStatus(); } catch {}
    try { await fetchBackfillRuns(); } catch {}
    // Optionally run bootstrap immediately
    if (resetThenBootstrap.value) {
      try {
        const payload = {
          backfill_year: false,
          fill_gaps: true,
          feature_target: 400,
          train_sentiment: false,
          min_auc: 0.6,
          max_ece: 0.08,
          dry_run: false,
          retry_fill_gaps: true,
          skip_promotion: false,
        };
        const rb = await http.post('/admin/bootstrap', payload);
        pushLog('[bootstrap] ' + JSON.stringify(rb.data || {}));
        successMsg.value = 'reset + bootstrap ok';
        // brief delay then fetch summary
        await new Promise(res => setTimeout(res, 1200));
        const sm = await http.get('/api/models/summary');
        pushLog('[models:summary] ' + JSON.stringify({ status: sm.data?.status, has_model: sm.data?.has_model, production: sm.data?.production }, null, 2));
      } catch (e:any) {
        pushLog('[bootstrap:error] ' + (e?.__friendlyMessage || e?.message || 'failed'));
        error.value = e?.__friendlyMessage || e?.message || 'bootstrap failed';
      }
    }
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message || 'reset failed';
    pushLog('[models:reset:error] ' + error.value);
  } finally { loading.value.reset = false; }
}
function confirmResetModels() {
  const includeTxt = dropFeatures.value ? '\n• Feature backfill runs (테이블)도 함께 삭제됩니다.' : '';
  const postTxt = resetThenBootstrap.value ? '\n\n초기화 후 즉시 부트스트랩을 실행합니다.' : '';
  openConfirm({
    title: '모델 초기화',
    message: '이 작업은 다음 항목을 삭제합니다:\n• 모델 레지스트리/메트릭/라인리지/프로모션/재학습/트레이닝 잡\n• 아티팩트 파일(.json)' + includeTxt + postTxt + '\n\n되돌릴 수 없습니다. 계속하려면 "RESET"을 입력하세요.',
    requireText: 'RESET',
    delayMs: 1500,
    onConfirm: () => { resetModels(); }
  });
}

// ------------------------------
// Inference Diagnostics helpers
// ------------------------------
const diag = ref<{
  loading: boolean;
  hist: { buckets: Array<{ range: string; count: number }>; total: number; maxCount: number } | null;
  logsN: number;
  windowSec: number;
  decisions: number;
  total: number;
  decisionRate: number | null;
  lastLogTs: string | null;
}>({ loading: false, hist: null, logsN: 200, windowSec: 3600, decisions: 0, total: 0, decisionRate: null, lastLogTs: null });

async function fetchDiagnostics() {
  diag.value.loading = true; error.value = null;
  try {
    // 1) Histogram (selectable window)
    const r1 = await http.get('/api/inference/probability/histogram', { params: { window: diag.value.windowSec } });
    const d1: any = r1.data || {};
    const bucketsRaw: Array<{ bucket?: string; range?: string; count?: number }> = Array.isArray(d1.buckets) ? d1.buckets : [];
    const buckets = bucketsRaw.map((b) => ({ range: (b.range ?? b.bucket ?? ''), count: Number(b.count || 0) }));
    const total = buckets.reduce((a, x) => a + (x.count || 0), 0);
    const maxCount = buckets.reduce((m, x) => Math.max(m, x.count || 0), 0);
    diag.value.hist = { buckets, total, maxCount };
    // 2) Recent logs to compute decision rate
    const r2 = await http.get('/api/inference/logs', { params: { limit: diag.value.logsN } });
    const items: any[] = Array.isArray(r2.data?.items) ? r2.data.items : (Array.isArray(r2.data) ? r2.data : []);
    const decisions = items.filter((it) => (it.decision === 1 || it.decision === 0 || it.decision === -1) && it.decision !== -1).length;
    const totalLogs = items.length;
    const rate = totalLogs > 0 ? decisions / totalLogs : null;
    const lastTs = items[0]?.ts || items[0]?.created_at || null;
    diag.value.decisions = decisions;
    diag.value.total = totalLogs;
    diag.value.decisionRate = rate;
    diag.value.lastLogTs = lastTs;
  } catch (e:any) {
    error.value = e.__friendlyMessage || e.message;
  } finally {
    diag.value.loading = false;
  }
}

// Decision rate guard: auto-nudge threshold within [min, max] via override

function startGuardTimer() {
  stopGuardTimer();
  guard.value._timer = setInterval(async () => {
    if (!guard.value.enabled) return;
    try {
      await fetchDiagnostics();
      const rate = diag.value.decisionRate;
      if (rate == null) return;
      const now = Date.now();
      if (guard.value._lastAdjAt && now - guard.value._lastAdjAt < guard.value.cooldownMs) return;
      // fetch current thresholds to get latest effective
      await fetchThresholds();
      const current = threshold.value.override ?? threshold.value.effective;
      if (typeof current !== 'number') return;
      let next = current;
      if (rate < guard.value.min) next = Math.max(0, current - guard.value.step);
      else if (rate > guard.value.max) next = Math.min(1, current + guard.value.step);
      if (next !== current) {
        threshold.value.newOverride = Number(next.toFixed(3));
        await applyThresholdOverride();
        guard.value._lastAdjAt = now;
        successMsg.value = `guard: threshold ${current.toFixed(3)} → ${next.toFixed(3)} (rate ${(rate*100).toFixed(1)}%)`;
      }
    } catch (e:any) {
      // fail-soft
      console.warn('guard tick failed', e?.message || e);
    }
  }, guard.value.tickMs);
}
function stopGuardTimer() { if (guard.value._timer) { clearInterval(guard.value._timer); guard.value._timer = null; } }

// ------------------------------
// Model existence check + blocking bootstrap on first load
// ------------------------------
async function modelExists(): Promise<boolean> {
  try {
    const r = await http.get('/api/models/summary');
    const d: any = r.data || {};
    // shapes: { status: 'ok'|'no_models', has_model?: boolean }
    if (d.has_model === true) return true;
    if (d.status && String(d.status).toLowerCase() === 'ok') return true;
    return false;
  } catch {
    return false;
  }
}

async function bootstrapUntilModel() {
  // Hold loading bar without auto-hide and show step labels
  startLoad(6, null);
  try {
    loadBar.value.label = 'checking model';
    if (await modelExists()) { endLoad(); return; }

    // 1) Quick OHLCV backfill year (idempotent; backend can no-op if already done)
    try {
      loadBar.value.label = 'year backfill';
      await http.post('/api/ohlcv/backfill/year/start');
      // poll brief status window to warm up
      await http.get('/api/ohlcv/backfill/year/status');
    } catch { /* ignore */ }
    stepLoad('ohlcv');

    // 2) Feature backfill to target
    try {
      loadBar.value.label = 'feature backfill';
      const params: any = { target: bootstrap.value.feature_target };
      if (bootstrap.value.feature_window && bootstrap.value.feature_window > 0) params.window = bootstrap.value.feature_window;
      await http.post('/admin/features/backfill', null, { params });
    } catch { /* ignore */ }
    stepLoad('features');

    // 3) Train bottom model (quick)
    try {
      loadBar.value.label = 'training';
      const payload: any = {
        trigger: 'auto_admin_init',
        target: 'bottom',
        bottom_lookahead: bottomLookahead.value,
        bottom_drawdown: bottomDrawdown.value,
        bottom_rebound: bottomRebound.value,
        store: true,
        wait: true,
        limit: 2000,
      };
      await http.post('/api/training/run', payload);
    } catch { /* ignore */ }
    stepLoad('train');

    // 4) Promotion via bootstrap gate or direct admin bootstrap (non-dry)
    try {
      loadBar.value.label = 'promotion';
      const payload = {
        backfill_year: false,
        fill_gaps: true,
        feature_target: Math.max(200, bootstrap.value.feature_target || 400),
        train_sentiment: false,
        min_auc: bootstrap.value.min_auc ?? DEFAULT_MIN_AUC,
        max_ece: bootstrap.value.max_ece ?? DEFAULT_MAX_ECE,
        dry_run: false,
        retry_fill_gaps: true,
        skip_promotion: false,
      };
      await http.post('/admin/bootstrap', payload);
    } catch { /* ignore */ }
    stepLoad('promote');

    // 5) Verify artifacts
    try { loadBar.value.label = 'artifacts'; await verifyArtifacts(); } catch { /* ignore */ }
    stepLoad('artifacts');

    // 6) Final summary check; keep polling briefly until model appears
    loadBar.value.label = 'finalize';
    for (let i = 0; i < 10; i++) {
      if (await modelExists()) break;
      await new Promise(res => setTimeout(res, 800));
    }
  } finally {
    endLoad();
  }
}
</script>

<style scoped>
textarea { resize: vertical; }
</style>