from __future__ import annotations
import asyncpg
import time
from typing import Any, Dict, List
from backend.common.db.connection import init_pool
from backend.apps.model_registry.repository.registry_repository import ModelRegistryRepository
from backend.apps.model_registry.service.model_storage import LocalModelStorage
from backend.apps.features.service.feature_service import FeatureService
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, accuracy_score
try:
    from sklearn.metrics import average_precision_score as _avg_prec
except Exception:  # pragma: no cover
    _avg_prec = None
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
import numpy as np
import math
from backend.common.config.base_config import load_config
import os

CFG = load_config()
OHLVC_SENTIMENT_MODEL_NAME = "ohlcv_sentiment_predictor"

MODEL_CACHE: dict[str, dict] = {}
MODEL_CACHE_TTL = 300  # seconds

def _cache_key(name: str) -> str:
    return f"{name}::production"

def _get_cached_model(name: str) -> dict | None:
    entry = MODEL_CACHE.get(_cache_key(name))
    if not entry:
        return None
    if time.time() - entry.get("loaded_at", 0) > MODEL_CACHE_TTL:
        MODEL_CACHE.pop(_cache_key(name), None)
        return None
    return entry

def _set_cached_model(name: str, data: dict):
    data["loaded_at"] = time.time()
    MODEL_CACHE[_cache_key(name)] = data

# Features now stored in long schema (meta/value). Reuse FeatureService.fetch_recent for compatibility.

class TrainingService:
    def __init__(self, symbol: str, interval: str, artifact_dir: str):
        self.symbol = symbol
        self.interval = interval
        self.repo = ModelRegistryRepository()
        self.storage = LocalModelStorage(artifact_dir)

    async def load_recent_features(self, limit: int = 1000) -> list[Dict[str, Any]]:
        # Delegate to FeatureService to assemble wide records from long schema
        svc = FeatureService(self.symbol, self.interval)
        rows = await svc.fetch_recent(limit)
        return list(reversed(rows))  # chronological

    async def run_training(self, limit: int = 1000, mode: str = "baseline", store: bool = False, class_weight: str | None = None, cv_splits: int = 0, time_cv: bool = True) -> Dict[str, Any]:
        """Run training.

        mode:
          - baseline: 기존 feature_snapshot 기반 (ret_*, rsi, rolling_vol, ma 등)
          - ohlcv_sentiment: ad-hoc OHLCV+뉴스 감성 피처 (preview 로직 재사용) (skeleton)
        """
        if mode == "ohlcv_sentiment":
            return await self.run_training_ohlcv_sentiment(limit=limit, store=store, class_weight=class_weight, cv_splits=cv_splits, time_cv=time_cv)
        rows = await self.load_recent_features(limit=limit)
        if len(rows) < 150:
            return {"status": "insufficient_data", "required": 150, "have": len(rows)}
        # Build supervised dataset: features at t predict next ret_1 > 0
        feats: List[List[float]] = []
        labels: List[int] = []
        # reverse rows are chronological already
        for i in range(len(rows) - 1):
            cur = rows[i]
            nxt = rows[i + 1]
            ret1 = cur.get("ret_1")
            ret5 = cur.get("ret_5")
            ret10 = cur.get("ret_10")
            rsi = cur.get("rsi_14")
            vol = cur.get("rolling_vol_20")
            ma20 = cur.get("ma_20")
            ma50 = cur.get("ma_50")
            if None in (ret1, ret5, ret10, rsi, vol, ma20, ma50):
                continue
            target_ret = nxt.get("ret_1")
            if target_ret is None:
                continue
            label = 1 if target_ret > 0 else 0
            feats.append([ret1, ret5, ret10, rsi, vol, ma20, ma50])
            labels.append(label)
        if len(feats) < 100:
            return {"status": "insufficient_data", "required": 100, "have": len(feats)}
        X = np.array(feats, dtype=float)
        y = np.array(labels, dtype=int)
        # Optional time-based cross validation BEFORE committing to final model
        cv_report = None
        if isinstance(cv_splits, int) and cv_splits > 1:
            # Time-based sequential splits: progressively expanding training window, next slice as validation
            # Ensure each validation fold has at least 30 samples
            fold_metrics: list[dict[str, float]] = []
            total_len = len(X)
            # Determine fold boundaries (exclude last fold for training final model; all folds used only for evaluation)
            # Strategy: Split into cv_splits+1 equal segments (rough) and use i segments for train, next segment for val
            seg_size = total_len // (cv_splits + 1)
            completed_folds = 0
            for i in range(1, cv_splits + 1):
                train_end = seg_size * i
                val_end = seg_size * (i + 1)
                if val_end > total_len:
                    break
                X_train_fold = X[:train_end]
                y_train_fold = y[:train_end]
                X_val_fold = X[train_end:val_end]
                y_val_fold = y[train_end:val_end]
                if len(X_val_fold) < 30 or len(np.unique(y_train_fold)) < 2:
                    continue
                lr_params_fold = {"max_iter": 500}
                if class_weight in ("balanced","auto","BALANCED","Balanced"):
                    lr_params_fold["class_weight"] = "balanced"
                pipe_fold = Pipeline([
                    ("scaler", StandardScaler()),
                    ("clf", LogisticRegression(**lr_params_fold)),
                ])
                try:
                    pipe_fold.fit(X_train_fold, y_train_fold)
                    probs_fold = pipe_fold.predict_proba(X_val_fold)[:,1]
                    preds_fold = (probs_fold >= 0.5).astype(int)
                    try:
                        fold_auc = float(roc_auc_score(y_val_fold, probs_fold))
                    except ValueError:
                        fold_auc = float("nan")
                    fold_acc = float(accuracy_score(y_val_fold, preds_fold))
                    # brier
                    try:
                        fold_brier = float(np.mean((probs_fold - y_val_fold) ** 2))
                    except Exception:
                        fold_brier = float("nan")
                    fold_metrics.append({
                        "fold": completed_folds + 1,
                        "train_size": int(len(X_train_fold)),
                        "val_size": int(len(X_val_fold)),
                        "auc": fold_auc,
                        "accuracy": fold_acc,
                        "brier": fold_brier,
                    })
                    completed_folds += 1
                except Exception:
                    continue
            if fold_metrics:
                # aggregate
                aucs = [f["auc"] for f in fold_metrics if isinstance(f.get("auc"),(int,float)) and f["auc"]==f["auc"]]
                accs = [f["accuracy"] for f in fold_metrics if isinstance(f.get("accuracy"),(int,float)) and f["accuracy"]==f["accuracy"]]
                briers = [f["brier"] for f in fold_metrics if isinstance(f.get("brier"),(int,float)) and f["brier"]==f["brier"]]
                def _agg(vals):
                    import math
                    if not vals:
                        return {"mean": None, "std": None}
                    m = sum(vals)/len(vals)
                    if len(vals) > 1:
                        var = sum((v-m)**2 for v in vals)/(len(vals)-1)
                        s = var**0.5
                    else:
                        s = 0.0
                    return {"mean": m, "std": s}
                cv_report = {
                    "folds": fold_metrics,
                    "auc": _agg(aucs),
                    "accuracy": _agg(accs),
                    "brier": _agg(briers),
                    "splits_used": len(fold_metrics),
                    "requested_splits": cv_splits,
                    "time_based": True,
                }
        # Simple hold-out split for final model (configurable)
        try:
            val_frac = float(getattr(CFG, 'training_validation_fraction', 0.2))
        except Exception:
            val_frac = 0.2
        if val_frac <= 0:
            val_frac = 0.2
        if val_frac >= 0.9:
            val_frac = 0.9
        split = int(len(X) * (1.0 - val_frac))
        # Ensure a minimum validation size for stability (e.g., 200 or 10% of data, whichever is smaller but >= 50)
        try:
            min_val = max(50, min(200, int(len(X) * 0.1)))
        except Exception:
            min_val = 50
        if len(X) - split < min_val and len(X) > min_val:
            split = len(X) - min_val
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]
        if len(np.unique(y_train)) < 2:
            out = {"status": "insufficient_class_variation"}
            if cv_report:
                out["cv_report"] = cv_report
            return out
        lr_params = {"max_iter": 500}
        if class_weight in ("balanced","auto","BALANCED","Balanced"):
            lr_params["class_weight"] = "balanced"
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(**lr_params)),
        ])
        pipe.fit(X_train, y_train)
        val_probs = pipe.predict_proba(X_val)[:, 1] if len(X_val) > 0 else np.array([])
        val_preds = (val_probs >= 0.5).astype(int) if len(val_probs) else np.array([])
        try:
            auc = float(roc_auc_score(y_val, val_probs)) if len(val_probs) else float("nan")
        except ValueError:
            auc = float("nan")
        acc = float(accuracy_score(y_val, val_preds)) if len(val_preds) else float("nan")
        # Brier score (mean squared error between probabilities and actual labels)
        if len(val_probs) and len(y_val):
            try:
                brier = float(np.mean((val_probs - y_val) ** 2))
            except Exception:
                brier = float("nan")
        else:
            brier = float("nan")
        # Reliability bins (deciles) & calibration errors (ECE/MCE)
        reliability_bins: list[dict] = []
        ece = float("nan")
        mce = float("nan")
        if len(val_probs) and len(y_val):
            try:
                probs = val_probs
                labels = y_val
                # 10 bins edges 0.0,0.1,...,1.0
                bin_edges = np.linspace(0.0, 1.0, 11)
                bin_indices = np.digitize(probs, bin_edges, right=True) - 1  # 0..9
                bin_indices = np.clip(bin_indices, 0, 9)
                total = len(probs)
                abs_diffs = []
                for b in range(10):
                    mask = bin_indices == b
                    count = int(mask.sum())
                    if count == 0:
                        reliability_bins.append({"bin": b, "count": 0, "mean_prob": None, "empirical": None, "abs_diff": None})
                        continue
                    mean_prob = float(probs[mask].mean())
                    empirical = float(labels[mask].mean())
                    diff = abs(mean_prob - empirical)
                    abs_diffs.append((count/total) * diff)
                    reliability_bins.append({"bin": b, "count": count, "mean_prob": mean_prob, "empirical": empirical, "abs_diff": diff})
                if abs_diffs:
                    ece = float(sum(abs_diffs))
                    mce = float(max(rb["abs_diff"] for rb in reliability_bins if rb["abs_diff"] is not None))
            except Exception:
                pass
        metrics = {
            "samples": int(len(X_train)),
            "val_samples": int(len(X_val)),
            "auc": auc,
            "accuracy": acc,
            "brier": brier,
            "ece": ece,
            "mce": mce,
            "reliability_bins": reliability_bins,
            "symbol": self.symbol,
            "interval": self.interval,
            "feature_set": ["ret_1","ret_5","ret_10","rsi_14","rolling_vol_20","ma_20","ma_50"],
            # Explicit model identity and target for downstream UIs/APIs
            "model_name": "baseline_predictor",
            "name": "baseline_predictor",
            "target": "direction",
        }
        # Use millisecond timestamp + short random suffix to avoid collisions across rapid runs
        try:
            import uuid
            version = f"{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
        except Exception:
            version = str(int(time.time()*1000))
        metrics["scaling"] = {"type": "StandardScaler"}
        if cv_report:
            metrics["cv_report"] = cv_report
        artifact_payload = {"sk_model": pipe, "metrics": metrics}
        artifact_path = self.storage.save(name="baseline_predictor", version=version, payload=artifact_payload)
        model_id = await self.repo.register(
            name="baseline_predictor",
            version=version,
            model_type="supervised",
            status="staging",
            artifact_path=artifact_path,
            metrics=metrics,
        )
        return {"status": "ok", "model_id": model_id, "version": version, "artifact_path": artifact_path, "metrics": metrics}

    async def _generate_ohlcv_sentiment_samples(self, limit: int = 600, horizon: int = 5) -> list[dict[str, Any]]:
        """Build samples with per-candle rolling sentiment windows (primary 60m, secondary 15m).

        Steps:
          1. 최근 OHLCV 캔들 로드 (ascending 정렬)
          2. 필요한 뉴스 기사(earliest_close - max_window .. latest_close) 구간만 단일 쿼리로 로드
          3. 두 개의 deque(60m / 15m) 를 사용해 캔들별 롤링 집계
          4. dataset_builder 로 OHLCV 기반 기본 피처/레이블 생성
          5. 각 샘플에 60m 감성 및 15m 감성(_w2 suffix) 피처 주입

        Fallback: 오류 발생 시 기존 정적 스냅샷 방식(60m/15m)으로 degrade.
        """
        from backend.apps.ingestion.repository.ohlcv_repository import fetch_recent as fetch_kline_recent  # canonical
        from backend.apps.features.service.dataset_builder import build_samples
        from backend.common.db.connection import init_pool as _init_pool
        import collections, math
        primary_win_min = 60
        secondary_win_min = 15
        try:
            raw_rows = await fetch_kline_recent(self.symbol, self.interval, limit=limit)
            candles = list(reversed(raw_rows))
            if not candles:
                return []
            earliest_close_ms = candles[0].get("close_time")
            latest_close_ms = candles[-1].get("close_time")
            if not isinstance(earliest_close_ms, int) or not isinstance(latest_close_ms, int):
                return []
            max_window = max(primary_win_min, secondary_win_min) * 60
            earliest_cutoff_sec = max(0, (earliest_close_ms // 1000) - max_window)
            latest_close_sec = latest_close_ms // 1000
            # Fetch articles once
            pool = await _init_pool()
            articles: list[dict[str, float]] = []
            if pool is not None:
                async with pool.acquire() as conn:  # type: ignore
                    rows_news = await conn.fetch(
                        """
                        SELECT published_ts, sentiment FROM news_articles
                        WHERE published_ts BETWEEN $1 AND $2
                          AND sentiment IS NOT NULL
                          AND symbol = $3
                        ORDER BY published_ts ASC
                        """,
                        earliest_cutoff_sec, latest_close_sec, self.symbol,
                    )
                    for r in rows_news:
                        try:
                            articles.append({
                                "published_ts": int(r["published_ts"]),
                                "sentiment": float(r["sentiment"]),
                            })
                        except Exception:
                            pass
            # Rolling aggregation structures
            primary_sec = primary_win_min * 60
            secondary_sec = secondary_win_min * 60
            dq1 = collections.deque()
            dq2 = collections.deque()
            sum1 = 0.0; pos1 = 0; neg1 = 0
            sum2 = 0.0; pos2 = 0; neg2 = 0
            art_idx = 0
            total_articles = len(articles)
            sentiment_map: dict[int, dict] = {}
            sentiment_map_secondary: dict[int, dict] = {}
            for c in candles:
                ct_ms = c.get("close_time")
                if not isinstance(ct_ms, int):
                    continue
                ct_sec = ct_ms // 1000
                cutoff1 = ct_sec - primary_sec
                cutoff2 = ct_sec - secondary_sec
                # Push new articles
                while art_idx < total_articles and articles[art_idx]["published_ts"] <= ct_sec:
                    a = articles[art_idx]
                    s = a["sentiment"]
                    dq1.append(a); sum1 += s
                    if s > 0.05:
                        pos1 += 1
                    elif s < -0.05:
                        neg1 += 1
                    dq2.append(a); sum2 += s
                    if s > 0.05:
                        pos2 += 1
                    elif s < -0.05:
                        neg2 += 1
                    art_idx += 1
                # Pop old (primary)
                while dq1 and dq1[0]["published_ts"] < cutoff1:
                    old = dq1.popleft(); s_old = old["sentiment"]; sum1 -= s_old
                    if s_old > 0.05:
                        pos1 -= 1
                    elif s_old < -0.05:
                        neg1 -= 1
                # Pop old (secondary)
                while dq2 and dq2[0]["published_ts"] < cutoff2:
                    old2 = dq2.popleft(); s_old2 = old2["sentiment"]; sum2 -= s_old2
                    if s_old2 > 0.05:
                        pos2 -= 1
                    elif s_old2 < -0.05:
                        neg2 -= 1
                count1 = len(dq1); neutral1 = count1 - pos1 - neg1 if count1 >= (pos1+neg1) else 0
                avg1 = (sum1 / count1) if count1 else None
                sentiment_map[ct_ms] = {
                    "window_minutes": primary_win_min,
                    "count": count1,
                    "avg": avg1,
                    "pos": pos1,
                    "neg": neg1,
                    "neutral": neutral1,
                    "symbol": self.symbol,
                }
                count2 = len(dq2); neutral2 = count2 - pos2 - neg2 if count2 >= (pos2+neg2) else 0
                avg2 = (sum2 / count2) if count2 else None
                sentiment_map_secondary[ct_ms] = {
                    "window_minutes": secondary_win_min,
                    "count": count2,
                    "avg": avg2,
                    "pos": pos2,
                    "neg": neg2,
                    "neutral": neutral2,
                    "symbol": self.symbol,
                }
            # Build base samples
            samples = build_samples(candles, sentiment_map, horizon=horizon)
            # Augment with secondary window & derived deltas/ratios
            for s in samples:
                ct = s.get("close_time")
                if not isinstance(ct, int):
                    continue
                sm2 = sentiment_map_secondary.get(ct)
                sm1 = sentiment_map.get(ct)
                if sm2:
                    s["sent_avg_w2"] = sm2.get("avg")
                    s["sent_count_w2"] = sm2.get("count")
                    s["sent_pos_w2"] = sm2.get("pos")
                    s["sent_neg_w2"] = sm2.get("neg")
                    s["sent_neutral_w2"] = sm2.get("neutral")
                    s["sent_window_w2"] = sm2.get("window_minutes")
                    # Derived features: short vs long difference & ratio
                    avg1 = sm1.get("avg") if sm1 else None
                    avg2 = sm2.get("avg")
                    if isinstance(avg1, (int,float)) and isinstance(avg2, (int,float)) and avg1 is not None and avg2 is not None:
                        try:
                            s["sent_avg_diff_w2"] = avg2 - avg1  # short - long
                            if abs(avg1) > 1e-9:
                                s["sent_avg_ratio_w2"] = avg2 / (avg1 if avg1 != 0 else 1e-9)
                        except Exception:
                            pass
                    # Pos/neg ratio (long & short)
                    pos1 = sm1.get("pos") if sm1 else None
                    neg1 = sm1.get("neg") if sm1 else None
                    pos2 = sm2.get("pos")
                    neg2 = sm2.get("neg")
                    def _safe_ratio(a,b):
                        try:
                            if isinstance(a,(int,float)) and isinstance(b,(int,float)) and b not in (0,None):
                                return float(a)/float(b)
                        except Exception:
                            return None
                        return None
                    r_long = _safe_ratio(pos1, neg1)
                    r_short = _safe_ratio(pos2, neg2)
                    if r_long is not None:
                        s["sent_pos_neg_ratio"] = r_long
                    if r_short is not None:
                        s["sent_pos_neg_ratio_w2"] = r_short
                    if r_long is not None and r_short is not None:
                        try:
                            s["sent_pos_neg_ratio_diff_w2"] = r_short - r_long
                        except Exception:
                            pass
            return samples
        except Exception as gen_err:
            # Fallback to legacy snapshot approach if any error encountered in rolling logic
            from logging import getLogger
            log = getLogger("training.sentiment")
            try:
                from prometheus_client import Counter  # lazy import
                FALLBACK_COUNTER = Counter("sentiment_generation_fallback_total", "Count of sentiment sample generation fallbacks", ["phase"])
                FALLBACK_COUNTER.labels(phase="exception").inc()
            except Exception:
                pass
            try:
                # Record exception event for auto-disable logic
                from backend.apps.training.sentiment_mode import record_event
                record_event("exception")
            except Exception:
                pass
            log.warning("rolling sentiment generation failed; falling back to snapshot approach err=%s", gen_err)
            try:
                from backend.apps.news.repository.news_repository import NewsRepository
                from backend.apps.features.service.dataset_builder import build_samples as _build
                from backend.apps.ingestion.repository.ohlcv_repository import fetch_recent as fetch_kline_recent  # canonical
                raw_rows = await fetch_kline_recent(self.symbol, self.interval, limit=limit)
                if not raw_rows:
                    return []
                candles = list(reversed(raw_rows))
                news_repo = NewsRepository()
                sent_primary = await news_repo.sentiment_window_stats(minutes=60, symbol=self.symbol)
                sent_secondary = await news_repo.sentiment_window_stats(minutes=15, symbol=self.symbol)
                if not isinstance(sent_primary, dict):
                    return []
                sentiment_map: dict[int, dict] = {}
                for c in candles:
                    ct = c.get("close_time")
                    if isinstance(ct, int):
                        sentiment_map[ct] = sent_primary
                samples = _build(candles, sentiment_map, horizon=horizon)
                # minimal secondary augmentation
                for s in samples:
                    if isinstance(sent_secondary, dict):
                        s["sent_avg_w2"] = sent_secondary.get("avg")
                        s["sent_count_w2"] = sent_secondary.get("count")
                try:
                    FALLBACK_COUNTER.labels(phase="success").inc()  # type: ignore
                except Exception:
                    pass
                try:
                    from backend.apps.training.sentiment_mode import record_event
                    record_event("success")
                except Exception:
                    pass
                return samples
            except Exception as fb_err:
                try:
                    FALLBACK_COUNTER.labels(phase="failure").inc()  # type: ignore
                except Exception:
                    pass
                try:
                    from backend.apps.training.sentiment_mode import record_event
                    record_event("failure")
                except Exception:
                    pass
                log.error("sentiment fallback failed err=%s", fb_err)
                return []

    async def run_training_ohlcv_sentiment(self, limit: int = 800, horizon: int = 5, store: bool = False, class_weight: str | None = None, cv_splits: int = 0, time_cv: bool = True, ablation: bool = False) -> Dict[str, Any]:
        """Skeleton training for combined OHLCV + sentiment features.

        현재: baseline 대비 성능 측정 목적의 LogisticRegression 시도.
        향후: 더 많은 피처 & scaling & class weighting & CV 구조 반영 예정.
        """
        samples = await self._generate_ohlcv_sentiment_samples(limit=limit, horizon=horizon)
        if len(samples) < 200:
            return {"status": "insufficient_data", "required": 200, "have": len(samples), "mode": "ohlcv_sentiment"}
        # Feature selection: drop meta columns and construct sentiment feature set
        drop_cols = {"open_time","close_time","label","horizon_return"}
        first = samples[0]
        all_candidate_feats = [k for k in first.keys() if k not in drop_cols]
        # Determine sentiment feature names
        sent_list_env = os.getenv("TRAINING_SENTIMENT_FEATURES", "").strip()
        if sent_list_env:
            sentiment_feats = {x.strip() for x in sent_list_env.split(",") if x.strip()}
        else:
            sentiment_feats = {k for k in all_candidate_feats if k.startswith("sent_")}
        # Base inclusion from ENV
        include_sent_env = str(os.getenv("TRAINING_INCLUDE_SENTIMENT", "true")).lower() not in ("0","false","no")
        impute_mode = str(os.getenv("TRAINING_IMPUTE_SENTIMENT", "ffill")).lower()  # ffill|zero|none

        def _build_Xy(include_sentiment: bool) -> tuple[list[str], list[list[float]], list[int]]:
            # choose features based on include_sentiment
            if include_sentiment:
                feat_names = [k for k in all_candidate_feats if isinstance(first.get(k), (int,float)) or k in sentiment_feats]
            else:
                feat_names = [k for k in all_candidate_feats if (k not in sentiment_feats) and isinstance(first.get(k), (int,float))]
            # forward-fill cache for sentiment features
            last_seen: dict[str, float] = {}
            X_rows: list[list[float]] = []
            y_rows: list[int] = []
            for s in samples:
                if s.get("label") is None:
                    continue
                row: list[float] = []
                numeric_ok = True
                for f in feat_names:
                    v = s.get(f)
                    if isinstance(v, (int, float)) and not (isinstance(v, float) and v != v):
                        row.append(float(v))
                        if f in sentiment_feats:
                            last_seen[f] = float(v)
                        continue
                    # handle missing/non-numeric
                    if f in sentiment_feats:
                        if impute_mode == "ffill":
                            if f in last_seen:
                                row.append(float(last_seen[f]))
                            else:
                                row.append(0.0)
                        elif impute_mode == "zero":
                            row.append(0.0)
                        else:  # none -> drop row
                            numeric_ok = False
                            break
                    else:
                        numeric_ok = False
                        break
                if not numeric_ok:
                    continue
                y_rows.append(int(s["label"]))
                X_rows.append(row)
            return feat_names, X_rows, y_rows

        # Build with base inclusion
        feat_names, X_rows, y_rows = _build_Xy(include_sent_env)
        if len(X_rows) < 150:
            return {"status": "insufficient_data", "required": 150, "have": len(X_rows), "mode": "ohlcv_sentiment"}
        import numpy as np  # local import kept though already imported
        X = np.array(X_rows, dtype=float)
        y = np.array(y_rows, dtype=int)
        cv_report = None
        if isinstance(cv_splits, int) and cv_splits > 1:
            fold_metrics: list[dict[str, float]] = []
            total_len = len(X)
            seg_size = total_len // (cv_splits + 1)
            completed_folds = 0
            for i in range(1, cv_splits + 1):
                train_end = seg_size * i
                val_end = seg_size * (i + 1)
                if val_end > total_len:
                    break
                X_train_fold = X[:train_end]
                y_train_fold = y[:train_end]
                X_val_fold = X[train_end:val_end]
                y_val_fold = y[train_end:val_end]
                if len(X_val_fold) < 30 or len(np.unique(y_train_fold)) < 2:
                    continue
                lr_params_fold = {"max_iter": 500}
                if class_weight in ("balanced","auto","BALANCED","Balanced"):
                    lr_params_fold["class_weight"] = "balanced"
                pipe_fold = Pipeline([
                    ("scaler", StandardScaler()),
                    ("clf", LogisticRegression(**lr_params_fold)),
                ])
                try:
                    pipe_fold.fit(X_train_fold, y_train_fold)
                    probs_fold = pipe_fold.predict_proba(X_val_fold)[:,1]
                    preds_fold = (probs_fold >= 0.5).astype(int)
                    try:
                        fold_auc = float(roc_auc_score(y_val_fold, probs_fold))
                    except ValueError:
                        fold_auc = float("nan")
                    fold_acc = float(accuracy_score(y_val_fold, preds_fold))
                    try:
                        fold_brier = float(np.mean((probs_fold - y_val_fold) ** 2))
                    except Exception:
                        fold_brier = float("nan")
                    fold_metrics.append({
                        "fold": completed_folds + 1,
                        "train_size": int(len(X_train_fold)),
                        "val_size": int(len(X_val_fold)),
                        "auc": fold_auc,
                        "accuracy": fold_acc,
                        "brier": fold_brier,
                    })
                    completed_folds += 1
                except Exception:
                    continue
            if fold_metrics:
                aucs = [f["auc"] for f in fold_metrics if f.get("auc") == f.get("auc")]
                accs = [f["accuracy"] for f in fold_metrics if f.get("accuracy") == f.get("accuracy")]
                briers = [f["brier"] for f in fold_metrics if f.get("brier") == f.get("brier")]
                def _agg(vals):
                    if not vals:
                        return {"mean": None, "std": None}
                    m = sum(vals)/len(vals)
                    if len(vals) > 1:
                        var = sum((v-m)**2 for v in vals)/(len(vals)-1)
                        s = var ** 0.5
                    else:
                        s = 0.0
                    return {"mean": m, "std": s}
                cv_report = {
                    "folds": fold_metrics,
                    "auc": _agg(aucs),
                    "accuracy": _agg(accs),
                    "brier": _agg(briers),
                    "splits_used": len(fold_metrics),
                    "requested_splits": cv_splits,
                    "time_based": True,
                }
        # Configurable validation fraction for sentiment path as well
        try:
            val_frac = float(getattr(CFG, 'training_validation_fraction', 0.2))
        except Exception:
            val_frac = 0.2
        if val_frac <= 0:
            val_frac = 0.2
        if val_frac >= 0.9:
            val_frac = 0.9
        split = int(len(X) * (1.0 - val_frac))
        try:
            min_val = max(50, min(200, int(len(X) * 0.1)))
        except Exception:
            min_val = 50
        if len(X) - split < min_val and len(X) > min_val:
            split = len(X) - min_val
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]
        if len(np.unique(y_train)) < 2:
            return {"status": "insufficient_class_variation", "mode": "ohlcv_sentiment"}
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_auc_score, accuracy_score
        lr_params = {"max_iter": 500}
        if class_weight in ("balanced","auto","BALANCED","Balanced"):
            lr_params["class_weight"] = "balanced"
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(**lr_params)),
        ])
        pipe.fit(X_train, y_train)
        val_probs = pipe.predict_proba(X_val)[:, 1] if len(X_val) else []
        val_preds = (val_probs >= 0.5).astype(int) if len(X_val) else []
        try:
            auc = float(roc_auc_score(y_val, val_probs)) if len(X_val) else float("nan")
        except ValueError:
            auc = float("nan")
        try:
            from sklearn.metrics import average_precision_score as _ap
            pr_auc = float(_ap(y_val, val_probs)) if len(X_val) else float("nan")
        except Exception:
            pr_auc = float("nan")
        from math import isnan
        acc = float(accuracy_score(y_val, val_preds)) if len(X_val) else float("nan")
        brier = float(np.mean((val_probs - y_val) ** 2)) if len(X_val) else float("nan")
        # Simple reliability bins (10)
        reliability_bins: list[dict] = []
        ece = float("nan"); mce = float("nan")
        if len(X_val):
            try:
                probs = val_probs
                labels = y_val
                import numpy as _np
                bin_edges = _np.linspace(0.0, 1.0, 11)
                bin_indices = _np.digitize(probs, bin_edges, right=True) - 1
                bin_indices = _np.clip(bin_indices, 0, 9)
                total = len(probs)
                abs_diffs = []
                for b in range(10):
                    mask = bin_indices == b
                    count = int(mask.sum())
                    if count == 0:
                        reliability_bins.append({"bin": b, "count": 0, "mean_prob": None, "empirical": None, "abs_diff": None})
                        continue
                    mean_prob = float(probs[mask].mean())
                    empirical = float(labels[mask].mean())
                    diff = abs(mean_prob - empirical)
                    abs_diffs.append((count/total) * diff)
                    reliability_bins.append({"bin": b, "count": count, "mean_prob": mean_prob, "empirical": empirical, "abs_diff": diff})
                if abs_diffs:
                    ece = float(sum(abs_diffs))
                    mce = float(max(rb["abs_diff"] for rb in reliability_bins if rb["abs_diff"] is not None))
            except Exception:
                pass
        metrics = {
            "samples": int(len(X_train)),
            "val_samples": int(len(X_val)),
            "auc": auc,
            "pr_auc": pr_auc,
            "accuracy": acc,
            "brier": brier,
            "ece": ece,
            "mce": mce,
            "mode": "ohlcv_sentiment",
            "symbol": self.symbol,
            "interval": self.interval,
            "feature_count": len(feat_names),
            "features": feat_names,
        }
        metrics["scaling"] = {"type": "StandardScaler"}
        if cv_report:
            metrics["cv_report"] = cv_report
        # Optional ablation report (with vs without sentiment)
        try:
            ablation_enabled = bool(str(os.getenv("TRAINING_ABLATION_REPORT", "true")).lower() not in ("0","false","no"))
        except Exception:
            ablation_enabled = True
        ablation_payload = None
        if ablation and ablation_enabled:
            try:
                # Build without sentiment features
                feat_names_wo, X_rows_wo, y_rows_wo = _build_Xy(False)
                if len(X_rows_wo) >= 150:
                    Xwo = np.array(X_rows_wo, dtype=float)
                    ywo = np.array(y_rows_wo, dtype=int)
                    # Use same split strategy for fair comparison
                    split_wo = int(len(Xwo) * (1.0 - val_frac))
                    if len(Xwo) - split_wo < min_val and len(Xwo) > min_val:
                        split_wo = len(Xwo) - min_val
                    Xtr_wo, Xval_wo = Xwo[:split_wo], Xwo[split_wo:]
                    ytr_wo, yval_wo = ywo[:split_wo], ywo[split_wo:]
                    pipe_wo = Pipeline([
                        ("scaler", StandardScaler()),
                        ("clf", LogisticRegression(**lr_params)),
                    ])
                    pipe_wo.fit(Xtr_wo, ytr_wo)
                    vpro_wo = pipe_wo.predict_proba(Xval_wo)[:,1] if len(Xval_wo) else []
                    vpred_wo = (vpro_wo >= 0.5).astype(int) if len(Xval_wo) else []
                    try:
                        auc_wo = float(roc_auc_score(yval_wo, vpro_wo)) if len(Xval_wo) else float("nan")
                    except ValueError:
                        auc_wo = float("nan")
                    try:
                        from sklearn.metrics import average_precision_score as _ap2
                        pr_auc_wo = float(_ap2(yval_wo, vpro_wo)) if len(Xval_wo) else float("nan")
                    except Exception:
                        pr_auc_wo = float("nan")
                    acc_wo = float(accuracy_score(yval_wo, vpred_wo)) if len(Xval_wo) else float("nan")
                    brier_wo = float(np.mean((vpro_wo - yval_wo) ** 2)) if len(Xval_wo) else float("nan")
                    # quick ECE estimate (reuse bins)
                    ece_wo = float("nan")
                    try:
                        if len(Xval_wo):
                            import numpy as _np2
                            bin_edges2 = _np2.linspace(0.0, 1.0, 11)
                            bin_idx2 = _np2.digitize(vpro_wo, bin_edges2, right=True) - 1
                            bin_idx2 = _np2.clip(bin_idx2, 0, 9)
                            total2 = len(vpro_wo)
                            diffs2 = []
                            for b in range(10):
                                msk = bin_idx2 == b
                                cnt = int(msk.sum())
                                if cnt == 0:
                                    continue
                                mp = float(vpro_wo[msk].mean())
                                emp = float(yval_wo[msk].mean())
                                diffs2.append((cnt/total2) * abs(mp-emp))
                            if diffs2:
                                ece_wo = float(sum(diffs2))
                    except Exception:
                        pass
                    ablation_payload = {
                        "with_sentiment": {"auc": auc, "pr_auc": pr_auc, "accuracy": acc, "brier": brier, "ece": ece},
                        "without_sentiment": {"auc": auc_wo, "pr_auc": pr_auc_wo, "accuracy": acc_wo, "brier": brier_wo, "ece": ece_wo},
                        "delta": {
                            "auc": (auc - auc_wo) if (auc == auc and auc_wo == auc_wo) else None,
                            "pr_auc": (pr_auc - pr_auc_wo) if (pr_auc == pr_auc and pr_auc_wo == pr_auc_wo) else None,
                            "accuracy": (acc - acc_wo) if (acc == acc and acc_wo == acc_wo) else None,
                            "brier": (brier - brier_wo) if (brier == brier and brier_wo == brier_wo) else None,
                            "ece": (ece - ece_wo) if (ece == ece and ece_wo == ece_wo) else None,
                        }
                    }
            except Exception:
                pass
        if not store:
            out = {"status": "ok", "metrics": metrics, "mode": "ohlcv_sentiment", "stored": False}
            if ablation_payload is not None:
                out["ablation"] = ablation_payload
            return out
        # 모델 아티팩트 저장 및 레지스트리 등록
        try:
            from pathlib import Path
            try:
                import uuid as _uuid
                version = f"{int(time.time()*1000)}-{_uuid.uuid4().hex[:6]}"
            except Exception:
                version = str(int(time.time()*1000))
            artifact_payload = {"sk_model": pipe, "metrics": metrics}
            artifact_path = self.storage.save(name=OHLVC_SENTIMENT_MODEL_NAME, version=version, payload=artifact_payload)
            model_id = await self.repo.register(
                name=OHLVC_SENTIMENT_MODEL_NAME,
                version=version,
                model_type="supervised",
                status="staging",  # 자동 프로덕션 전환은 별도 승격 로직에서 처리
                artifact_path=artifact_path,
                metrics=metrics,
            )
            return {
                "status": "ok",
                "metrics": metrics,
                "mode": "ohlcv_sentiment",
                "stored": True,
                "model_id": model_id,
                "version": version,
                "artifact_path": artifact_path,
                "model_name": OHLVC_SENTIMENT_MODEL_NAME,
            }
        except Exception as e:
            return {"status": "store_error", "error": str(e), "metrics": metrics, "mode": "ohlcv_sentiment", "stored": False}

    async def predict_latest(self, threshold: float | None = None, debug: bool = False, *, prefer_latest: bool = False, version: str | None = None) -> Dict[str, Any]:
        """Return single-step inference using production (or latest) baseline_predictor.

        threshold: optional override for decision boundary (0-1). If invalid, config default 사용.
        """
        rows = await self.load_recent_features(limit=2)
        if not rows:
            return {"status": "no_data"}
        latest = rows[-1]
        feat_names = ["ret_1","ret_5","ret_10","rsi_14","rolling_vol_20","ma_20","ma_50"]
        missing = [f for f in feat_names if latest.get(f) is None]
        if missing:
            # include context for easier troubleshooting by API/UI callers
            out = {
                "status": "insufficient_features",
                "missing": missing,
            }
            # Attach snapshot timing when available to help identify staleness/misalignment
            try:
                ft_open = latest.get("open_time") if isinstance(latest, dict) else None
                ft_close = latest.get("close_time") if isinstance(latest, dict) else None
                out["feature_open_time"] = ft_open
                out["feature_close_time"] = ft_close
            except Exception:
                pass
            return out
        repo = self.repo
        # Row selection policy: explicit version > prefer_latest > production-first
        prod_row = None
        target_row = None
        try:
            prods = await repo.fetch_production_history("baseline_predictor", "supervised", limit=1)
            if prods:
                prod_row = prods[0]
        except Exception:
            prod_row = None
        if isinstance(version, str) and version:
            try:
                # fetch a few latest and match by version (cheap path)
                models_any = await repo.fetch_latest("baseline_predictor", "supervised", limit=10)
                for r in models_any:
                    if str(r.get("version")) == version:
                        target_row = r
                        break
            except Exception:
                target_row = None
        if target_row is None and prefer_latest:
            try:
                models = await repo.fetch_latest("baseline_predictor", "supervised", limit=1)
                target_row = models[0] if models else None
            except Exception:
                target_row = None
        if target_row is None:
            # Default: production if exists, else most recent
            if prod_row is None:
                models = await repo.fetch_latest("baseline_predictor", "supervised", limit=1)
                target_row = models[0] if models else None
            else:
                target_row = prod_row
        if not target_row:
            # Lazy seed attempt
            try:
                baseline_metrics = {"auc":0.50,"accuracy":0.50,"brier":0.25,"ece":0.05,"mce":0.05,"seed_baseline": True}
                await self.repo.register(
                    name="baseline_predictor", version="seed", model_type="supervised", status="production", artifact_path=None, metrics=baseline_metrics
                )
                from backend.apps.api.metrics_seed import SEED_AUTO_SEED_TOTAL  # type: ignore
                try:
                    SEED_AUTO_SEED_TOTAL.labels(source="lazy_predict", result="success").inc()
                except Exception:
                    pass
                models = await repo.fetch_latest("baseline_predictor", "supervised", limit=3)
                target_row = models[0] if models else None
            except Exception:
                try:
                    from backend.apps.api.metrics_seed import SEED_AUTO_SEED_TOTAL  # type: ignore
                    SEED_AUTO_SEED_TOTAL.labels(source="lazy_predict", result="error").inc()
                except Exception:
                    pass
                return {"status": "no_model"}
        if not target_row:
            return {"status": "no_model"}
        version_sel = target_row.get("version")
        artifact_path = target_row.get("artifact_path")
        metrics_row = target_row.get("metrics") or {}
        if not artifact_path:
            if metrics_row.get("seed_baseline"):
                thr = CFG.inference_prob_threshold if (threshold is None or not (0 < threshold < 1)) else threshold
                prob = 0.5
                decision = 1 if prob >= thr else -1
                return {"status":"ok","probability":prob,"decision":decision,"threshold":thr,"model_version":version_sel,"used_production": True, "seed_fallback": True}
            return {"status": "artifact_missing"}
        cached = _get_cached_model("baseline_predictor")
        model_obj = None
        if cached and cached.get("version") == version_sel:
            model_obj = cached.get("model")
        else:
            try:
                from pathlib import Path
                p = Path(artifact_path)
                if not p.exists():
                    return {"status": "artifact_not_found"}
                import json, base64, pickle
                with open(p, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                b64 = payload.get("sk_model_b64")
                if not b64:
                    return {"status": "artifact_corrupt"}
                raw = base64.b64decode(b64)
                model_obj = pickle.loads(raw)
                _set_cached_model("baseline_predictor", {"model": model_obj, "version": version_sel})
            except Exception as e:  # pragma: no cover
                return {"status": "artifact_load_error", "error": str(e)}
        if model_obj is None:
            return {"status": "model_materialization_failed"}
        try:
            X = np.array([[latest[f] for f in feat_names]], dtype=float)
            prob = float(model_obj.predict_proba(X)[0][1])  # type: ignore
        except Exception as e:  # pragma: no cover
            return {"status": "inference_error", "error": str(e)}
        thr = CFG.inference_prob_threshold if (threshold is None or not (0 < threshold < 1)) else threshold
        decision = 1 if prob >= thr else -1
        # Attach feature snapshot timing (if available) for observability
        ft_open = latest.get("open_time") if isinstance(latest, dict) else None
        ft_close = latest.get("close_time") if isinstance(latest, dict) else None
        ft_age = None
        try:
            if isinstance(ft_close, (int, float)):
                # feature_snapshot close_time stored in ms; convert to seconds for age
                ft_age = max(0.0, time.time() - (float(ft_close) / 1000.0))
        except Exception:
            ft_age = None
        # simple heuristic flag for stale features (e.g., over 3 minutes old)
        ft_stale = None
        try:
            if isinstance(ft_age, (int, float)):
                ft_stale = bool(ft_age > 180)
        except Exception:
            ft_stale = None
        out: Dict[str, Any] = {
            "status": "ok",
            "probability": prob,
            "decision": decision,
            "threshold": thr,
            "model_version": version_sel,
            "used_production": bool(prod_row is not None and prod_row.get("version") == version_sel),
            "feature_open_time": ft_open,
            "feature_close_time": ft_close,
            "feature_age_seconds": ft_age,
            "feature_stale": ft_stale,
        }
        if debug:
            try:
                dbg: Dict[str, Any] = {"features": {f: float(latest.get(f)) for f in feat_names}}
                # If pipeline, try to extract scaler/clf for introspection
                from sklearn.pipeline import Pipeline as _SkPipeline  # type: ignore
                from sklearn.preprocessing import StandardScaler as _SkScaler  # type: ignore
                from sklearn.linear_model import LogisticRegression as _SkLR  # type: ignore
                pipe = model_obj
                scaler = None
                clf = None
                if isinstance(pipe, _SkPipeline):
                    try:
                        scaler = pipe.named_steps.get("scaler")
                    except Exception:
                        scaler = None
                    try:
                        clf = pipe.named_steps.get("clf")
                    except Exception:
                        clf = None
                # Compute standardized features if scaler present
                z = None
                if scaler is not None and hasattr(scaler, "mean_") and hasattr(scaler, "scale_"):
                    try:
                        mean = np.asarray(getattr(scaler, "mean_"), dtype=float)
                        scale = np.asarray(getattr(scaler, "scale_"), dtype=float)
                        z = ((X[0] - mean) / np.where(scale == 0, 1.0, scale)).tolist()
                        dbg["z_features"] = {feat_names[i]: float(z[i]) for i in range(len(feat_names))}
                    except Exception:
                        pass
                # Linear logit reconstruction if LR present
                if clf is not None and hasattr(clf, "coef_") and hasattr(clf, "intercept_"):
                    try:
                        coef = np.asarray(getattr(clf, "coef_"), dtype=float)
                        inter = np.asarray(getattr(clf, "intercept_"), dtype=float)
                        vec = np.asarray(z if z is not None else X[0], dtype=float)
                        lin = float(coef.reshape(-1).dot(vec.reshape(-1)) + inter.reshape(-1)[0])
                        dbg["logit_linear"] = lin
                        dbg["sigmoid(lin)"] = float(1.0 / (1.0 + np.exp(-lin)))
                    except Exception:
                        pass
                out["debug"] = dbg
            except Exception:
                pass
        return out

    async def predict_latest_bottom(self, threshold: float | None = None, debug: bool = False, *, prefer_latest: bool = False, version: str | None = None) -> Dict[str, Any]:
        """Inference using the production (or latest) bottom_predictor.

        Returns probability = P(bottom_event).
        """
        rows = await self.load_recent_features(limit=2)
        if not rows:
            return {"status": "no_data"}
        latest = rows[-1]
        feat_names = ["ret_1","ret_5","ret_10","rsi_14","rolling_vol_20","ma_20","ma_50"]
        missing = [f for f in feat_names if latest.get(f) is None]
        if missing:
            out = {"status": "insufficient_features", "missing": missing}
            try:
                ft_open = latest.get("open_time") if isinstance(latest, dict) else None
                ft_close = latest.get("close_time") if isinstance(latest, dict) else None
                out["feature_open_time"] = ft_open
                out["feature_close_time"] = ft_close
            except Exception:
                pass
            return out
        repo = self.repo
        # Row selection policy: explicit version > prefer_latest > production-first
        prod_row = None
        target_row = None
        try:
            prods = await repo.fetch_production_history("bottom_predictor", "supervised", limit=1)
            prod_row = prods[0] if prods else None
        except Exception:
            prod_row = None
        if isinstance(version, str) and version:
            try:
                models_any = await repo.fetch_latest("bottom_predictor", "supervised", limit=10)
                for r in models_any:
                    if str(r.get("version")) == version:
                        target_row = r
                        break
            except Exception:
                target_row = None
        if target_row is None and prefer_latest:
            try:
                models = await repo.fetch_latest("bottom_predictor", "supervised", limit=1)
                target_row = models[0] if models else None
            except Exception:
                return {"status": "no_model"}
        if target_row is None:
            target_row = prod_row
            if target_row is None:
                try:
                    models = await repo.fetch_latest("bottom_predictor", "supervised", limit=1)
                    target_row = models[0] if models else None
                except Exception:
                    return {"status": "no_model"}
        if not target_row:
            return {"status": "no_model"}
        rowd = target_row if isinstance(target_row, dict) else dict(target_row)
        version_sel = rowd.get("version")
        artifact_path = rowd.get("artifact_path")
        if not artifact_path:
            return {"status": "artifact_missing"}
        # cache by version for bottom family
        name = "bottom_predictor"
        cached = _get_cached_model(name)
        model_obj = None
        if cached and cached.get("version") == version_sel:
            model_obj = cached.get("model")
        else:
            try:
                from pathlib import Path
                p = Path(artifact_path)
                if not p.exists():
                    return {"status": "artifact_not_found"}
                import json, base64, pickle
                with open(p, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                b64 = payload.get("sk_model_b64")
                if not b64:
                    return {"status": "artifact_corrupt"}
                raw = base64.b64decode(b64)
                model_obj = pickle.loads(raw)
                _set_cached_model(name, {"model": model_obj, "version": version_sel})
            except Exception as e:
                return {"status": "artifact_load_error", "error": str(e)}
        if model_obj is None:
            return {"status": "model_materialization_failed"}
        import numpy as np
        try:
            X = np.array([[latest[f] for f in feat_names]], dtype=float)
            prob = float(model_obj.predict_proba(X)[0][1])  # type: ignore
        except Exception as e:
            return {"status": "inference_error", "error": str(e)}
        thr = CFG.inference_prob_threshold if (threshold is None or not (0 < threshold < 1)) else threshold
        decision = 1 if prob >= thr else -1
        out: Dict[str, Any] = {
            "status": "ok",
            "probability": prob,
            "decision": decision,
            "threshold": thr,
            "model_version": version_sel,
            "used_production": bool(prod_row is not None and prod_row.get("version") == version_sel),
            "target": "bottom",
        }
        return out

    async def run_training_bottom(self, limit: int = 1000, store: bool = True, class_weight: str | None = None,
                                  lookahead: int | None = None, drawdown: float | None = None, rebound: float | None = None,
                                  cv_splits: int = 0, time_cv: bool = True) -> Dict[str, Any]:
        """Train a bottom-event classifier and register as bottom_predictor.

        Labels computed from OHLCV: drawdown/rebound within lookahead window as in bottom_labeler.
        Features: reuse baseline feature set for v1.
        """
        rows = await self.load_recent_features(limit=limit)
        if len(rows) < 200:
            return {"status": "insufficient_data", "required": 200, "have": len(rows)}
        # Fetch OHLCV chronologically
        try:
            from backend.apps.ingestion.repository.ohlcv_repository import fetch_recent as _fetch_kline_recent
            k_rows = await _fetch_kline_recent(self.symbol, self.interval, limit=min(max(len(rows) + 64, 300), 2000))
            candles = list(reversed(k_rows))
        except Exception:
            candles = []
        if not candles:
            return {"status": "no_ohlcv"}
        # Params
        L = int(lookahead if lookahead is not None else getattr(CFG, 'bottom_lookahead', 30))
        D = float(drawdown if drawdown is not None else getattr(CFG, 'bottom_drawdown', 0.005))
        R = float(rebound if rebound is not None else getattr(CFG, 'bottom_rebound', 0.003))
        # Index mapping by close_time for candles
        idx_by_ct = {}
        for i, c in enumerate(candles):
            ct = c.get("close_time")
            if isinstance(ct, int):
                idx_by_ct[ct] = i
        def _label_at_ct(ct: int) -> int | None:
            j = idx_by_ct.get(ct)
            if j is None:
                return None
            end = min(len(candles) - 1, j + L)
            if end <= j:
                return None
            try:
                p0 = float(candles[j]["close"])
            except Exception:
                return None
            # find min low in (j+1..end)
            min_low = None; min_idx = None
            for t in range(j + 1, end + 1):
                try:
                    lo = float(candles[t]["low"])  # drawdown check
                except Exception:
                    continue
                if min_low is None or lo < min_low:
                    min_low = lo; min_idx = t
            if min_low is None or min_idx is None:
                return None
            try:
                drop = (min_low - p0) / p0
            except Exception:
                return None
            if drop > (-abs(D)):
                return 0
            # rebound from min_idx to end
            max_high = None
            for t in range(min_idx, end + 1):
                try:
                    hi = float(candles[t]["high"])
                except Exception:
                    continue
                if max_high is None or hi > max_high:
                    max_high = hi
            if max_high is None:
                return 0
            try:
                rb = (max_high - min_low) / min_low
            except Exception:
                return 0
            return 1 if rb >= abs(R) else 0
        # Build dataset
        feat_names = ["ret_1","ret_5","ret_10","rsi_14","rolling_vol_20","ma_20","ma_50"]
        X_list: List[List[float]] = []
        y_list: List[int] = []
        for r in rows:
            if any(r.get(f) is None for f in feat_names if f in r):
                continue
            ct = r.get("close_time")
            if not isinstance(ct, int):
                continue
            yv = _label_at_ct(ct)
            if yv is None:
                continue
            X_list.append([float(r.get(f)) for f in feat_names])
            y_list.append(int(yv))
        try:
            min_labels = int(getattr(CFG, 'bottom_min_labels', 150))
        except Exception:
            min_labels = 150
        if len(X_list) < int(min_labels) or len(set(y_list)) < 2:
            return {"status": "insufficient_labels", "have": len(X_list), "pos_ratio": (float(sum(y_list))/len(y_list) if y_list else None), "required": int(min_labels)}
        import numpy as np
        X = np.array(X_list, dtype=float)
        y = np.array(y_list, dtype=int)
        # Hold-out split
        try:
            val_frac = float(getattr(CFG, 'training_validation_fraction', 0.2))
        except Exception:
            val_frac = 0.2
        val_frac = 0.2 if not (0.0 < val_frac < 0.9) else val_frac
        split = int(len(X) * (1.0 - val_frac))
        if len(X) - split < 50 and len(X) > 50:
            split = len(X) - 50
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]
        if len(np.unique(y_train)) < 2:
            return {"status": "insufficient_class_variation"}
        lr_params = {"max_iter": 500}
        if class_weight in ("balanced","auto","BALANCED","Balanced"):
            lr_params["class_weight"] = "balanced"
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(**lr_params)),
        ])
        pipe.fit(X_train, y_train)
        val_probs = pipe.predict_proba(X_val)[:, 1] if len(X_val) else np.array([])
        val_preds = (val_probs >= 0.5).astype(int) if len(val_probs) else np.array([])
        # Metrics
        # Robust AUC: handle edge cases where ROC AUC is undefined (single-class validation, constant scores)
        def _safe_auc(y_true, probs):
            try:
                if len(probs) == 0:
                    return None, "empty_val"
                if len(y_true) == 0:
                    return None, "empty_val"
                import numpy as _np
                if len(_np.unique(y_true)) < 2:
                    return 0.5, "single_class"
                # If all predicted probabilities are identical, ROC curve is a diagonal => AUC 0.5
                if float(_np.std(probs)) == 0.0:
                    return 0.5, "constant_scores"
                return float(roc_auc_score(y_true, probs)), None
            except Exception as e:
                return None, f"error:{type(e).__name__}"

        auc, auc_note = _safe_auc(y_val, val_probs)
        acc = float(accuracy_score(y_val, val_preds)) if len(val_preds) else float("nan")
        if _avg_prec is not None and len(val_probs):
            try:
                pr_auc = float(_avg_prec(y_val, val_probs))
            except Exception:
                pr_auc = float("nan")
        else:
            pr_auc = float("nan")
        brier = float(np.mean((val_probs - y_val) ** 2)) if len(val_probs) else float("nan")
        # Simple ECE/MCE via deciles
        reliability_bins: list[dict] = []
        ece = float("nan"); mce = float("nan")
        if len(val_probs):
            try:
                bin_edges = np.linspace(0.0, 1.0, 11)
                bin_indices = np.digitize(val_probs, bin_edges, right=True) - 1
                bin_indices = np.clip(bin_indices, 0, 9)
                total = len(val_probs)
                abs_diffs = []
                for b in range(10):
                    mask = bin_indices == b
                    count = int(mask.sum())
                    if count == 0:
                        reliability_bins.append({"bin": b, "count": 0, "mean_prob": None, "empirical": None, "abs_diff": None})
                        continue
                    mean_prob = float(val_probs[mask].mean())
                    empirical = float(y_val[mask].mean())
                    diff = abs(mean_prob - empirical)
                    abs_diffs.append((count/total) * diff)
                    reliability_bins.append({"bin": b, "count": count, "mean_prob": mean_prob, "empirical": empirical, "abs_diff": diff})
                if abs_diffs:
                    ece = float(sum(abs_diffs))
                    mce = float(max(rb["abs_diff"] for rb in reliability_bins if rb["abs_diff"] is not None))
            except Exception:
                pass
        pos_ratio = float(y.mean()) if len(y) else float("nan")
        metrics = {
            "samples": int(len(X_train)),
            "val_samples": int(len(X_val)),
            "auc": auc,
            "auc_note": auc_note,
            "pr_auc": pr_auc,
            "accuracy": acc,
            "brier": brier,
            "ece": ece,
            "mce": mce,
            "reliability_bins": reliability_bins,
            "symbol": self.symbol,
            "interval": self.interval,
            "feature_set": feat_names,
            "target": "bottom",
            "label_definition": "lookahead-drawdown-rebound",
            "label_params": {"lookahead": L, "drawdown": D, "rebound": R},
            "positives_ratio": pos_ratio,
            # Explicit model identity for downstream UIs/APIs
            "model_name": "bottom_predictor",
            "name": "bottom_predictor",
        }
        # Version and artifact
        try:
            import uuid
            version = f"{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
        except Exception:
            version = str(int(time.time()*1000))
        artifact_payload = {"sk_model": pipe, "metrics": metrics}
        artifact_path = self.storage.save(name="bottom_predictor", version=version, payload=artifact_payload)
        model_id = await self.repo.register(
            name="bottom_predictor",
            version=version,
            model_type="supervised",
            status="staging",
            artifact_path=artifact_path,
            metrics=metrics,
        )
        return {"status": "ok", "model_id": model_id, "version": version, "artifact_path": artifact_path, "metrics": metrics}

    async def run_training_for_horizon(self, limit: int = 1000, horizon_label: str = "1m", store: bool = True, class_weight: str | None = None, cv_splits: int = 0, time_cv: bool = True) -> Dict[str, Any]:
        """Train a horizon-specific baseline model and register as baseline_predictor_<h>.

        Labeling: Use OHLCV close prices to compute future return over <steps> bars.
        label[t] = 1 if close[t+steps] - close[t] > 0 else 0 (skip if future is missing)

        horizon_label examples: "1m","5m","15m". Assumes feature snapshots align 1:1 with closed OHLCV candles.
        """
        rows = await self.load_recent_features(limit=limit)
        if len(rows) < 150:
            return {"status": "insufficient_data", "required": 150, "have": len(rows)}
        # Parse horizon steps from label like '15m' → 15
        try:
            steps = int(str(horizon_label).strip().lower().replace('m',''))
        except Exception:
            steps = 1
        feats: List[List[float]] = []
        labels: List[int] = []
        feat_names = ["ret_1","ret_5","ret_10","ret_15","rsi_14","rolling_vol_20","ma_20","ma_50"]

        # Try to fetch recent OHLCV closes to compute accurate labels by time alignment
        closes_by_ct: dict[int, float] = {}
        ordered_cts: list[int] = []
        try:
            from backend.apps.ingestion.repository.ohlcv_repository import fetch_recent as _fetch_kline_recent
            # over-fetch a bit to be safe
            k_rows = await _fetch_kline_recent(self.symbol, self.interval, limit=min(max(len(rows) + steps + 5, 50), 2000))
            # k_rows are DESC by open_time per repo; reverse to chronological by open_time which implies chronological close_time as well
            k_chrono = list(reversed(k_rows))
            for k in k_chrono:
                ct = k.get("close_time")
                cl = k.get("close")
                if isinstance(ct, int) and isinstance(cl, (int, float)):
                    closes_by_ct[ct] = float(cl)
                    ordered_cts.append(ct)
        except Exception:
            # If OHLCV fetch fails, we'll fallback to index-based label using rows
            closes_by_ct = {}
            ordered_cts = []

        # Build a mapping from close_time to index to allow O(1) future lookup
        idx_by_ct = {ct: i for i, ct in enumerate(ordered_cts)} if ordered_cts else {}

        for i in range(len(rows)):
            cur = rows[i]
            # feature availability check
            if any(cur.get(f) is None for f in feat_names if f in cur):
                continue
            # Determine label using OHLCV closes if available; else fallback to rows index if enough future rows
            label_val: int | None = None
            ct = cur.get("close_time")
            if isinstance(ct, int) and idx_by_ct:
                j = idx_by_ct.get(ct)
                if j is not None and (j + steps) < len(ordered_cts):
                    c_now = closes_by_ct.get(ordered_cts[j])
                    c_fut = closes_by_ct.get(ordered_cts[j + steps])
                    if isinstance(c_now, float) and isinstance(c_fut, float):
                        if c_now == 0:
                            # avoid div-by-zero semantics; treat as skip if malformed
                            pass
                        else:
                            label_val = 1 if (c_fut - c_now) > 0 else 0
            # Fallback: use rows alignment (chronological) when OHLCV mapping missing
            if label_val is None:
                nxt_idx = i + steps
                if nxt_idx < len(rows):
                    # We cannot use nxt.ret_1 proxy; instead, approximate with price-relative signal is unavailable.
                    # As a best-effort fallback, use ret_1 at the future index to determine direction at that step.
                    target = rows[nxt_idx].get("ret_1")
                    if isinstance(target, (int, float)):
                        label_val = 1 if float(target) > 0 else 0
            if label_val is None:
                continue
            row_feats = [
                cur.get("ret_1"), cur.get("ret_5"), cur.get("ret_10"), cur.get("ret_15"),
                cur.get("rsi_14"), cur.get("rolling_vol_20"), cur.get("ma_20"), cur.get("ma_50")
            ]
            if any(v is None for v in row_feats):
                continue
            feats.append([float(v) for v in row_feats])
            labels.append(int(label_val))
        if len(feats) < 100:
            return {"status": "insufficient_data", "required": 100, "have": len(feats)}
        import numpy as _np
        X = _np.array(feats, dtype=float)
        y = _np.array(labels, dtype=int)
        # Optional CV
        cv_report = None
        if isinstance(cv_splits, int) and cv_splits > 1:
            # Use a simple forward-chaining CV similar to run_training
            fold_metrics: list[dict[str, float]] = []
            total_len = len(X)
            seg_size = total_len // (cv_splits + 1)
            completed_folds = 0
            for i in range(1, cv_splits + 1):
                train_end = seg_size * i
                val_end = seg_size * (i + 1)
                if val_end > total_len:
                    break
                X_train_fold = X[:train_end]
                y_train_fold = y[:train_end]
                X_val_fold = X[train_end:val_end]
                y_val_fold = y[train_end:val_end]
                if len(X_val_fold) < 30 or len(set(y_train_fold.tolist())) < 2:
                    continue
                lr_params_fold = {"max_iter": 500}
                if class_weight in ("balanced","auto","BALANCED","Balanced"):
                    lr_params_fold["class_weight"] = "balanced"
                pipe_fold = Pipeline([
                    ("scaler", StandardScaler()),
                    ("clf", LogisticRegression(**lr_params_fold)),
                ])
                try:
                    pipe_fold.fit(X_train_fold, y_train_fold)
                    probs_fold = pipe_fold.predict_proba(X_val_fold)[:,1]
                    preds_fold = (probs_fold >= 0.5).astype(int)
                    try:
                        fold_auc = float(roc_auc_score(y_val_fold, probs_fold))
                    except Exception:
                        fold_auc = float('nan')
                    fold_acc = float(accuracy_score(y_val_fold, preds_fold))
                    try:
                        fold_brier = float(_np.mean((probs_fold - y_val_fold) ** 2))
                    except Exception:
                        fold_brier = float('nan')
                    fold_metrics.append({"fold": completed_folds+1, "train_size": int(len(X_train_fold)), "val_size": int(len(X_val_fold)), "auc": fold_auc, "accuracy": fold_acc, "brier": fold_brier})
                    completed_folds += 1
                except Exception:
                    continue
            if fold_metrics:
                aucs = [f["auc"] for f in fold_metrics if isinstance(f.get("auc"),(int,float)) and f["auc"]==f["auc"]]
                accs = [f["accuracy"] for f in fold_metrics if isinstance(f.get("accuracy"),(int,float)) and f["accuracy"]==f["accuracy"]]
                briers = [f["brier"] for f in fold_metrics if isinstance(f.get("brier"),(int,float)) and f["brier"]==f["brier"]]
                def _agg(vals):
                    if not vals:
                        return {"mean": None, "std": None}
                    m = sum(vals)/len(vals)
                    if len(vals) > 1:
                        var = sum((v-m)**2 for v in vals)/(len(vals)-1)
                        s = var**0.5
                    else:
                        s = float('nan')
                    return {"mean": m, "std": s}
                cv_report = {"auc": _agg(aucs), "accuracy": _agg(accs), "brier": _agg(briers), "folds": len(fold_metrics)}
        # Fit final model
        lr_params = {"max_iter": 500}
        if class_weight in ("balanced","auto","BALANCED","Balanced"):
            lr_params["class_weight"] = "balanced"
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(**lr_params)),
        ])
        pipe.fit(X, y)
        probs = pipe.predict_proba(X)[:, 1]
        preds = (probs >= 0.5).astype(int)
        try:
            auc = float(roc_auc_score(y, probs))
        except Exception:
            auc = float("nan")
        acc = float(accuracy_score(y, preds))
        try:
            brier = float(_np.mean((probs - y) ** 2))
        except Exception:
            brier = float("nan")
        metrics = {
            "auc": auc,
            "accuracy": acc,
            "brier": brier,
            "ece": None,
            "mce": None,
            "mode": f"baseline_{horizon_label}",
            "symbol": self.symbol,
            "interval": self.interval,
            "feature_count": len(feat_names),
            "features": feat_names,
            "label_source": "ohlcv_close",
            "label_horizon_steps": int(steps),
            "label_definition": "1 if close[t+steps] > close[t] else 0",
        }
        if cv_report:
            metrics["cv_report"] = cv_report
        # store
        if not store:
            return {"status": "ok", "metrics": metrics, "stored": False}
        try:
            import uuid as _uuid
            version = f"{int(time.time()*1000)}-{_uuid.uuid4().hex[:6]}"
            artifact_payload = {"sk_model": pipe, "metrics": metrics}
            artifact_path = self.storage.save(name=f"baseline_predictor_{horizon_label}", version=version, payload=artifact_payload)
            model_id = await self.repo.register(
                name=f"baseline_predictor_{horizon_label}",
                version=version,
                model_type="supervised",
                status="staging",
                artifact_path=artifact_path,
                metrics=metrics,
            )
            return {"status": "ok", "metrics": metrics, "stored": True, "model_id": model_id, "version": version, "artifact_path": artifact_path, "model_name": f"baseline_predictor_{horizon_label}"}
        except Exception as e:
            return {"status": "store_error", "error": str(e), "metrics": metrics, "stored": False}

    async def predict_latest_sentiment(self, horizon: int = 5) -> Dict[str, Any]:
        """Inference using the latest OHLCV + rolling sentiment (60m + 15m) for ohlcv_sentiment_predictor.

        Steps:
          1. 최근 preview 와 유사하게 rolling sentiment 계산 (여기서는 _generate_ohlcv_sentiment_samples 재활용)
          2. 마지막 샘플 feature 추출 -> 저장된 sentiment 모델 로드 -> 확률 산출
        """
        # Generate minimal samples (reuse generation but limit for speed)
        samples = await self._generate_ohlcv_sentiment_samples(limit=400, horizon=horizon)
        if not samples:
            return {"status": "no_data"}
        latest = samples[-1]
        # Identify numeric feature columns (exclude label/meta)
        drop_cols = {"open_time","close_time","label","horizon_return"}
        feat_names = [k for k,v in latest.items() if k not in drop_cols and isinstance(v,(int,float))]
        if not feat_names:
            return {"status": "no_features"}
        # Load sentiment model artifact (production first)
        repo = self.repo
        rows = await repo.fetch_latest(OHLVC_SENTIMENT_MODEL_NAME, "supervised", limit=5)
        prod_row = next((r for r in rows if r.get("status") == "production"), None)
        target_row = prod_row or (rows[0] if rows else None)
        if not target_row:
            return {"status": "no_model"}
        artifact_path = target_row.get("artifact_path")
        version = target_row.get("version")
        if not artifact_path:
            return {"status": "artifact_missing"}
        # Cache key distinct
        cache_key = f"sentiment::{version}"
        cache_entry = MODEL_CACHE.get(cache_key)
        model_obj = None
        if cache_entry and time.time() - cache_entry.get("loaded_at",0) < MODEL_CACHE_TTL:
            model_obj = cache_entry.get("model")
        else:
            try:
                import json, base64, pickle
                from pathlib import Path
                p = Path(artifact_path)
                if not p.exists():
                    return {"status": "artifact_not_found"}
                with open(p, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                b64 = payload.get("sk_model_b64")
                if not b64:
                    return {"status": "artifact_corrupt"}
                raw = base64.b64decode(b64)
                model_obj = pickle.loads(raw)
                MODEL_CACHE[cache_key] = {"model": model_obj, "loaded_at": time.time()}
            except Exception as e:
                return {"status": "artifact_load_error", "error": str(e)}
        if model_obj is None:
            return {"status": "model_materialization_failed"}
        # Align feature ordering: try metrics.features list if present
        metrics = target_row.get("metrics") or {}
        registry_feats = metrics.get("features") or metrics.get("feature_set")
        use_feats = registry_feats if registry_feats and set(registry_feats).issubset(set(feat_names)) else feat_names
        import numpy as np
        try:
            X = np.array([[latest[f] for f in use_feats]], dtype=float)
            prob = float(model_obj.predict_proba(X)[0][1])  # type: ignore
        except Exception as e:
            return {"status": "inference_error", "error": str(e)}
        threshold = CFG.inference_prob_threshold
        decision = 1 if prob >= threshold else -1
        return {
            "status": "ok",
            "probability": prob,
            "decision": decision,
            "threshold": threshold,
            "model_version": version,
            "used_production": bool(prod_row is not None),
            "feature_count": len(use_feats),
            "features_used": use_feats,
        }

    async def predict_latest_direction_multi(self, horizons: list[str]) -> list[dict[str, Any]]:
        """Compute per-horizon probabilities using horizon-specific models.

        Model naming convention: baseline_predictor_<h>, e.g., baseline_predictor_1m, _5m, _15m

        Returns a list of {horizon, up_prob, model_version, used_production} for those available.
        If no models are available, returns an empty list.
        """
        try:
            rows = await self.load_recent_features(limit=2)
            if not rows:
                return []
            latest = rows[-1]
            feat_names = ["ret_1","ret_5","ret_10","rsi_14","rolling_vol_20","ma_20","ma_50"]
            if any(latest.get(f) is None for f in feat_names):
                return []
            import numpy as _np
            X = _np.array([[latest[f] for f in feat_names]], dtype=float)
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for h in horizons:
            try:
                name = f"baseline_predictor_{h}"
                # try cache first
                cached = _get_cached_model(name)
                model_obj = None
                version = None
                used_production = False
                if cached and cached.get("model") is not None:
                    model_obj = cached.get("model")
                    version = cached.get("version")
                    used_production = True  # cache is keyed on production
                else:
                    # fetch registry
                    rows = await self.repo.fetch_latest(name, "supervised", limit=5)
                    prod_row = next((r for r in rows if r.get("status") == "production"), None)
                    target_row = prod_row or (rows[0] if rows else None)
                    if not target_row:
                        continue
                    version = target_row.get("version")
                    artifact_path = target_row.get("artifact_path")
                    if not artifact_path:
                        continue
                    try:
                        import json, base64, pickle
                        from pathlib import Path as _P
                        p = _P(artifact_path)
                        if not p.exists():
                            continue
                        with open(p, "r", encoding="utf-8") as f:
                            payload = json.load(f)
                        b64 = payload.get("sk_model_b64")
                        if not b64:
                            continue
                        raw = base64.b64decode(b64)
                        model_obj = pickle.loads(raw)
                        _set_cached_model(name, {"model": model_obj, "version": version})
                        used_production = bool(prod_row is not None)
                    except Exception:
                        continue
                if model_obj is None:
                    continue
                try:
                    prob = float(model_obj.predict_proba(X)[0][1])  # type: ignore
                except Exception:
                    continue
                out.append({
                    "horizon": h,
                    "up_prob": prob,
                    "model_version": version,
                    "used_production": used_production,
                })
            except Exception:
                # continue other horizons even if one fails
                continue
        return out

__all__ = ["TrainingService"]
