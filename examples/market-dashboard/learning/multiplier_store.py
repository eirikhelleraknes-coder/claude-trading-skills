# learning/multiplier_store.py
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

LEARNING_DIR = Path(__file__).resolve().parent
DEFAULT_LEARNED_FILE = LEARNING_DIR / "learned_multipliers.json"
DEFAULT_SEED_FILE = LEARNING_DIR / "seed_multipliers.json"

MIN_SAMPLE_COUNT = 5
_MAX_VALID_RR = 20.0


def _p75(values: list[float]) -> float:
    """75th percentile using nearest-rank method. Spec test: [2,2.5,3,3.5,4] → 3.5."""
    s = sorted(values)
    idx = math.ceil(0.75 * len(s)) - 1
    return round(s[max(0, idx)], 3)


class MultiplierStore:
    """Reads/writes learned take-profit multipliers per bucket key.

    Bucket key format: "{screener}+{confidence_tag}+{regime}"

    Fallback chain:
      1. learned p75 (>=5 real trades)
      2. weighted blend of seed prior + observed p75 (1-4 real trades)
      3. seed prior (0 real trades, bucket in seed)
      4. 2.0 hardcoded default (unknown bucket)
    """

    def __init__(
        self,
        learned_file: Path = DEFAULT_LEARNED_FILE,
        seed_file: Path = DEFAULT_SEED_FILE,
    ):
        self._learned_file = learned_file
        self._seed_file = seed_file

    def _load_learned(self) -> dict:
        if not self._learned_file.exists():
            return {}
        try:
            return json.loads(self._learned_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _load_seed(self) -> dict:
        if not self._seed_file.exists():
            return {}
        try:
            return json.loads(self._seed_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def get(self, bucket_key: str) -> float:
        """Return the multiplier to use for a bracket order. Never raises."""
        try:
            learned_all = self._load_learned()
            seed_all = self._load_seed()
            bucket = learned_all.get(bucket_key, {})
            seed = seed_all.get(bucket_key)
            observed = bucket.get("observed_rr", [])
            n_real = len(observed)

            if n_real >= MIN_SAMPLE_COUNT:
                return bucket["p75"]
            elif n_real > 0:
                observed_p75 = _p75(observed)
                if seed is None:
                    return observed_p75
                seed_weight = seed["sample_count"]
                return (seed_weight * seed["multiplier"] + n_real * observed_p75) / (
                    seed_weight + n_real
                )
            else:
                return seed["multiplier"] if seed is not None else 2.0
        except Exception:
            return 2.0

    def update(self, bucket_key: str, achieved_rr: float) -> None:
        """Append a real trade's achieved R:R and recompute p75. Discards bad values."""
        if achieved_rr <= 0 or achieved_rr > _MAX_VALID_RR:
            return
        data = self._load_learned()
        bucket = data.get(bucket_key, {"observed_rr": []})
        bucket["observed_rr"].append(achieved_rr)
        n = len(bucket["observed_rr"])
        bucket["p75"] = _p75(bucket["observed_rr"])
        bucket["sample_count"] = n
        bucket["last_updated"] = date.today().isoformat()
        data[bucket_key] = bucket
        self._learned_file.parent.mkdir(parents=True, exist_ok=True)
        self._learned_file.write_text(json.dumps(data, indent=2))
