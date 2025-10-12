from sbtester.engine import Engine, Params, Candle

def test_basic_runs():
    candles = [
        Candle(1,1,1,1,1,0),
        Candle(2,1,1,1,2,0),
        Candle(3,1,1,1,1.5,0),
        Candle(4,1,1,1,0.9,0),
        Candle(5,1,1,1,1.2,0),
    ]
    eng = Engine(Params(force_close_at_end=True))
    out = eng.run(candles)
    assert out["summary"]["bars"] == 5
    assert "events" in out and len(out["events"]) > 0
