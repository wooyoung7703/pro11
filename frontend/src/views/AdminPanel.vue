<template>
  <div class="space-y-6">
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
          <span v-if="error" class="px-2 py-0.5 rounded bg-brand-danger/20 text-brand-danger">{{ error }}</span>
          <span v-if="successMsg" class="px-2 py-0.5 rounded bg-brand-accent/20 text-brand-accent">{{ successMsg }}</span>
        </div>
      </div>
      <p class="text-xs text-neutral-400">운영/테스트 편의를 위한 관리 작업을 수동으로 실행할 수 있습니다.</p>
      
      <div class="grid md:grid-cols-2 gap-6">
        <div class="p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-3">
          <h2 class="text-sm font-semibold">Startup & Scheduling</h2>
          <div class="flex flex-wrap gap-2 text-xs">
            <button class="btn" title="초기 지연 루프 업그레이드/기동" :disabled="loading.fastUpgrade" @click="confirmFastUpgrade">fast_startup upgrade</button>
            <button class="btn" title="라벨러 1회 실행" :disabled="loading.labeler" @click="confirmLabeler">run labeler</button>
            <button class="btn" title="학습 트리거" :disabled="loading.training" @click="confirmTraining">trigger training</button>
            <button class="btn !bg-amber-700 hover:!bg-amber-600" title="핵심 테이블 보장/생성" :disabled="loading.schema" @click="confirmEnsureTables">전체 테이블 생성</button>
          </div>
          <div class="grid grid-cols-2 gap-2 text-[11px]">
            <label class="flex items-center gap-2" title="학습 타겟"><span class="text-neutral-400 w-28">training target</span>
              <select class="input w-full !py-1 !px-2" v-model="trainingTarget">
                <option value="direction">direction</option>
                <option value="bottom">bottom</option>
              </select>
            </label>
            <label v-if="trainingTarget==='bottom'" class="flex items-center gap-2" title="lookahead (bars)"><span class="text-neutral-400 w-28">bottom lookahead</span>
              <input class="input w-full" type="number" min="1" v-model.number="bottomLookahead" />
            </label>
            <label v-if="trainingTarget==='bottom'" class="flex items-center gap-2" title="min drawdown"><span class="text-neutral-400 w-28">bottom drawdown</span>
              <input class="input w-full" type="number" step="0.001" min="0" v-model.number="bottomDrawdown" />
            </label>
            <label v-if="trainingTarget==='bottom'" class="flex items-center gap-2" title="min rebound"><span class="text-neutral-400 w-28">bottom rebound</span>
              <input class="input w-full" type="number" step="0.001" min="0" v-model.number="bottomRebound" />
            </label>
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
import { useRoute } from 'vue-router';
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore - Vue SFC default export is provided by shims
import ConfirmDialog from '../components/ConfirmDialog.vue';
import http from '../lib/http';
import { connectSSE } from '../lib/sse';
import { confirmPresets } from '../lib/confirmPresets';

interface RiskState {
  session: { starting_equity: number; peak_equity: number; current_equity: number; cumulative_pnl: number; last_reset_ts?: number };
  positions: { symbol: string; size: number; entry_price: number }[];
}

const loading = ref({ fastUpgrade: false, training: false, labeler: false, risk: false, schema: false, bootstrap: false, featBackfill: false, artifacts: false });
const error = ref<string | null>(null);
const successMsg = ref<string | null>(null);
const riskState = ref<RiskState | null>(null);
const logLines = ref<string[]>([]);
const schemaResult = ref<string[]>([]);
// Artifacts verify state
interface ArtifactSummary { ok: number; missing: number; file_not_found: number; file_check_error: number }
const artifacts = ref<{ summary: ArtifactSummary | null; rows: any[]; lastChecked: string | null }>({ summary: null, rows: [], lastChecked: null });
// Env helpers (allow runtime override via window for local debugging)
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore - Vite provides import.meta.env at build time
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

// Training target & bottom params
const trainingTarget = ref<'direction'|'bottom'>('direction');
const bottomLookahead = ref<number>(30);
const bottomDrawdown = ref<number>(0.005);
const bottomRebound = ref<number>(0.003);

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
    // Send selected target and bottom params if applicable
    const payload: any = { trigger: 'manual_ui', target: trainingTarget.value };
    if (trainingTarget.value === 'bottom') {
      payload.bottom_lookahead = bottomLookahead.value;
      payload.bottom_drawdown = bottomDrawdown.value;
      payload.bottom_rebound = bottomRebound.value;
    }
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
onMounted(() => {
  try {
    const k = 'bootstrap_auto_ran';
    if (!sessionStorage.getItem(k)) {
      sessionStorage.setItem(k, '1');
      runBootstrap(true);
    }
  } catch {
    // fallback: still attempt once
    runBootstrap(true);
  }
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
    const t1 = localStorage.getItem('admin_training_target'); if (t1 === 'direction' || t1 === 'bottom') trainingTarget.value = t1 as any;
    const b1 = localStorage.getItem('admin_bottom_lookahead'); if (b1!=null) bottomLookahead.value = Math.max(1, Number(b1) || bottomLookahead.value);
    const b2 = localStorage.getItem('admin_bottom_drawdown'); if (b2!=null) bottomDrawdown.value = Math.max(0, Number(b2) || bottomDrawdown.value);
    const b3 = localStorage.getItem('admin_bottom_rebound'); if (b3!=null) bottomRebound.value = Math.max(0, Number(b3) || bottomRebound.value);
  } catch { /* ignore */ }
  try {
    const v3 = localStorage.getItem('admin_bf_auto'); if (v3!=null) bf.value.auto = v3 === '1';
    const v4 = localStorage.getItem('admin_bf_live'); if (v4!=null) bf.value.live = v4 === '1';
  } catch { /* ignore */ }
  // kick initial runs fetch and start polling if enabled (SSE will start via watcher when live=true)
  fetchBackfillRuns();
  startRunsPolling();
  // initial risk fetch
  fetchRisk();
  // start risk auto refresh
  riskTimer = setInterval(() => { if (riskAuto.value && !riskLive.value) fetchRisk(); }, RISK_AUTO_MS);
});
onBeforeUnmount(() => {
  stopRunsPolling();
  stopRunsSSE();
  if (riskTimer) { clearInterval(riskTimer); riskTimer = null; }
  try { _riskSSE && _riskSSE.close(); } catch {}
  _riskSSE = null;
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

// Persist training target & bottom params once (avoid duplicate watchers that leak over time)
watch(trainingTarget, (v) => { try { localStorage.setItem('admin_training_target', String(v)); } catch {} });
watch(bottomLookahead, (v) => { try { localStorage.setItem('admin_bottom_lookahead', String(v)); } catch {} });
watch(bottomDrawdown, (v) => { try { localStorage.setItem('admin_bottom_drawdown', String(v)); } catch {} });
watch(bottomRebound, (v) => { try { localStorage.setItem('admin_bottom_rebound', String(v)); } catch {} });

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
</script>

<style scoped>
textarea { resize: vertical; }
</style>