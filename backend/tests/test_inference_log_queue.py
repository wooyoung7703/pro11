import pytest
import asyncio
from backend.apps.training.service.inference_log_queue import InferenceLogQueue

@pytest.mark.asyncio
async def test_queue_flush(monkeypatch):
    flushed = []
    class DummyRepo:
        async def bulk_insert(self, items):
            flushed.extend(items)
    q = InferenceLogQueue(max_size=10, flush_batch=3, flush_interval=0.1)
    # inject dummy repo
    q._repo = DummyRepo()  # type: ignore
    await q.start()
    for i in range(5):
        await q.enqueue({
            "symbol":"XRPUSDT","interval":"1m","model_name":"m","model_version":"v","probability":0.1*i,
            "decision":1,"threshold":0.5,"production":True, "extra":None
        })
    await asyncio.sleep(0.35)  # allow several flush cycles
    await q.stop()
    assert len(flushed) >= 5  # all enqueued eventually flushed

@pytest.mark.asyncio
async def test_queue_drop(monkeypatch):
    class DummyRepo:
        async def bulk_insert(self, items):
            pass
    q = InferenceLogQueue(max_size=2, flush_batch=10, flush_interval=1.0)
    q._repo = DummyRepo()  # type: ignore
    await q.start()
    await q.enqueue({"symbol":"X","interval":"1m","model_name":"m","model_version":"v","probability":0.1,"decision":1,"threshold":0.5,"production":True, "extra":None})
    await q.enqueue({"symbol":"X","interval":"1m","model_name":"m","model_version":"v","probability":0.2,"decision":1,"threshold":0.5,"production":True, "extra":None})
    ok = await q.enqueue({"symbol":"X","interval":"1m","model_name":"m","model_version":"v","probability":0.3,"decision":1,"threshold":0.5,"production":True, "extra":None})
    await q.stop()
    assert ok is False  # third should drop
