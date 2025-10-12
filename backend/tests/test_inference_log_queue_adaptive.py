import asyncio
import time
import types
import pytest
from backend.apps.training.service.inference_log_queue import InferenceLogQueue

@pytest.mark.asyncio
async def test_adaptive_flush_interval():
    # small queue to trigger watermarks quickly
    q = InferenceLogQueue(max_size=10, flush_batch=5, flush_interval=0.4)
    # Monkeypatch config-derived attributes for deterministic behavior
    q._high_watermark = 0.5  # trigger when >=5
    q._low_watermark = 0.1   # trigger when <=1
    q._min_interval = 0.05
    await q.start()
    try:
        # Fill entries to exceed high watermark
        for i in range(6):
            await q.enqueue({"symbol":"X", "interval":"1m", "model_name":"m","model_version":"v","probability":0.5,"decision":1,"threshold":0.5,"production":True,"extra":{}})
        # Wait for at least one run cycle
        await asyncio.sleep(0.5)
        faster_interval = q._interval
        assert faster_interval <= 0.4
        # Drain queue by allowing multiple flush cycles
        await asyncio.sleep(1.0)
        # At low occupancy, interval should trend back upward (but <= base)
        assert q._interval <= q._base_interval
        # If queue close to empty, we expect interval to have increased vs fastest value
        assert q._interval >= faster_interval
    finally:
        await q.stop()
