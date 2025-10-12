from prometheus_client import Counter

# Counter for auto-seed events (startup or lazy prediction path)
# Labels:
#   source: "startup" | "lazy_predict"
#   result: "success" | "error"
SEED_AUTO_SEED_TOTAL = Counter(
    "inference_seed_auto_seed_total",
    "Total auto-seed baseline insertion attempts (startup vs lazy predict)",
    ["source", "result"],
)

__all__ = ["SEED_AUTO_SEED_TOTAL"]
