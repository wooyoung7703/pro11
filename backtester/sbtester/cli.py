import json
import click
import pandas as pd
from .engine import Engine, Params, Candle

@click.command()
@click.option("--csv", "csv_path", required=True, help="CSV with columns: timestamp,open,high,low,close,volume")
@click.option("--fee-mode", type=click.Choice(["taker","maker"]), default="taker")
@click.option("--fee-taker-bps", type=float, default=10.0)
@click.option("--fee-maker-bps", type=float, default=2.0)
@click.option("--slippage-bps", type=float, default=0.0)
@click.option("--base-size", type=float, default=1.0)
@click.option("--si-max-legs", type=int, default=3)
@click.option("--partial", is_flag=True, default=True, help="Allow partial exits")
@click.option("--partial-pct", type=float, default=0.5)
@click.option("--force-close", is_flag=True, default=True)
@click.option("--events-csv", type=str, default=None, help="Optional path to write events CSV")
@click.option("--summary-json", type=str, default=None, help="Optional path to write summary JSON")
def main(csv_path, fee_mode, fee_taker_bps, fee_maker_bps, slippage_bps, base_size, si_max_legs, partial, partial_pct, force_close, events_csv, summary_json):
    df = pd.read_csv(csv_path)
    need = {"timestamp","open","high","low","close","volume"}
    if not need.issubset(df.columns):
        raise click.ClickException(f"Missing columns. Need {need}")
    candles = [Candle(int(r.timestamp), float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume)) for r in df.itertuples(index=False)]
    params = Params(
        fee_mode=fee_mode,
        fee_taker_bps=fee_taker_bps,
        fee_maker_bps=fee_maker_bps,
        slippage_bps=slippage_bps,
        base_size_units=base_size,
        si_max_legs=si_max_legs,
        partial_allow=partial,
        partial_pct=partial_pct,
        force_close_at_end=force_close,
    )
    eng = Engine(params)
    out = eng.run(candles)

    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))

    if events_csv:
        pd.DataFrame(out["events"]).to_csv(events_csv, index=False)
        print(f"events -> {events_csv}")
    if summary_json:
        with open(summary_json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"full summary -> {summary_json}")

if __name__ == "__main__":
    main()