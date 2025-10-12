import pytest
import types
from backend.apps.training.repository.inference_log_repository import InferenceLogRepository, INSERT_SQL_NO_RETURNING

class DummyConn:
    def __init__(self, calls):
        self.calls = calls
    async def executemany(self, sql, records):
        self.calls.append((sql, len(records)))
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        return False
    async def transaction(self):  # not used in optimized path
        class _T:
            async def __aenter__(self_inner):
                return self_inner
            async def __aexit__(self_inner, *a):
                return False
        return _T()

class DummyPool:
    def __init__(self, calls):
        self.calls = calls
    async def acquire(self):
        return DummyConn(self.calls)

@pytest.mark.asyncio
async def test_bulk_insert_executemany(monkeypatch):
    calls = []
    async def _init_pool():
        return DummyPool(calls)
    monkeypatch.setattr('backend.apps.training.repository.inference_log_repository.init_pool', _init_pool)
    repo = InferenceLogRepository()
    items = [
        {"symbol":"X","interval":"1m","model_name":"m","model_version":"v","probability":0.1,"decision":1,"threshold":0.5,"production":True,"extra":None},
        {"symbol":"X","interval":"1m","model_name":"m","model_version":"v2","probability":0.2,"decision":-1,"threshold":0.5,"production":False,"extra":None},
    ]
    await repo.bulk_insert(items)
    assert len(calls) == 1
    assert calls[0][0].strip().startswith("INSERT INTO model_inference_log")
    assert calls[0][1] == 2
