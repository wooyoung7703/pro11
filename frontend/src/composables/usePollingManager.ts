import { onBeforeUnmount } from 'vue';

interface PollJobOptions {
  id: string;              // 고유 ID
  interval: number;        // ms
  enabled?: () => boolean; // 실행 여부 조건
  immediate?: boolean;     // 등록 시 즉시 1회 실행
  run: () => Promise<void> | void; // 실행 함수
}

interface InternalJob extends PollJobOptions { timer?: any }

// Visibility API 기반 탭 비활성화 시 폴링 중단
export function usePollingManager() {
  const jobs = new Map<string, InternalJob>();
  let suspended = false;

  function startJob(job: InternalJob) {
    stopJob(job.id);
    if (job.immediate) safeRun(job);
    job.timer = setInterval(() => {
      if (suspended) return;
      if (job.enabled && !job.enabled()) return;
      safeRun(job);
    }, job.interval);
  }

  function safeRun(job: InternalJob) {
    try {
      const r = job.run();
      if (r && typeof (r as any).then === 'function') {
        (r as Promise<any>).catch(() => {/* 개별 실패는 조용히 무시 (상위 fetch 내부 토스트 처리) */});
      }
    } catch {
      // 개별 런 실패 무시
    }
  }

  function register(opts: PollJobOptions) {
    const existing = jobs.get(opts.id);
    const job: InternalJob = { ...opts };
    jobs.set(opts.id, job);
    if (!existing) startJob(job);
    else startJob(job); // 재시작
  }

  function updateInterval(id: string, interval: number) {
    const job = jobs.get(id);
    if (!job) return;
    job.interval = interval;
    startJob(job);
  }

  function stopJob(id: string) {
    const job = jobs.get(id);
    if (job && job.timer) { clearInterval(job.timer); job.timer = null; }
  }

  function remove(id: string) {
    stopJob(id);
    jobs.delete(id);
  }

  function stopAll() { for (const j of jobs.values()) stopJob(j.id); }
  function restartAll() { for (const j of jobs.values()) startJob(j); }

  function handleVisibility() {
    if (document.hidden) {
      suspended = true;
    } else {
      suspended = false;
      // 다시 활성화 시 즉시 한 번 실행
      for (const j of jobs.values()) safeRun(j);
    }
  }
  document.addEventListener('visibilitychange', handleVisibility);

  onBeforeUnmount(() => {
    stopAll();
    document.removeEventListener('visibilitychange', handleVisibility);
  });

  return {
    register,
    remove,
    updateInterval,
    stopJob,
    restartAll,
    stopAll,
  };
}
