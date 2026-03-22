# tests/test_multiplier_store.py
import sys, json, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def write_seed(tmp: Path, data: dict):
    (tmp / "seed_multipliers.json").write_text(json.dumps(data))


def write_learned(tmp: Path, data: dict):
    (tmp / "learned_multipliers.json").write_text(json.dumps(data))


def make_store(tmp: Path):
    from learning.multiplier_store import MultiplierStore
    return MultiplierStore(
        learned_file=tmp / "learned_multipliers.json",
        seed_file=tmp / "seed_multipliers.json",
    )


def test_get_returns_seed_when_no_real_trades():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        write_seed(tmp, {"vcp+CLEAR+bull": {"multiplier": 3.0, "sample_count": 50}})
        store = make_store(tmp)
        assert store.get("vcp+CLEAR+bull") == 3.0


def test_get_returns_2_0_for_unknown_bucket_no_seed():
    with tempfile.TemporaryDirectory() as d:
        store = make_store(Path(d))
        assert store.get("canslim+UNCERTAIN+bear") == 2.0


def test_get_returns_p75_when_5_or_more_real_trades():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # p75 of [2.0, 2.5, 3.0, 3.5, 4.0] = 3.5 (nearest rank: ceil(0.75*5)-1 = index 3)
        write_learned(tmp, {
            "vcp+CLEAR+bull": {
                "observed_rr": [2.0, 2.5, 3.0, 3.5, 4.0],
                "p75": 3.5,
                "sample_count": 5,
            }
        })
        store = make_store(tmp)
        assert store.get("vcp+CLEAR+bull") == 3.5


def test_get_returns_weighted_blend_with_3_real_trades():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # seed: 50 samples @ 3.0; real: [3.0, 3.0, 3.0] → p75=3.0
        # blend = (50*3.0 + 3*3.0) / (50+3) = 3.0
        write_seed(tmp, {"vcp+CLEAR+bull": {"multiplier": 3.0, "sample_count": 50}})
        write_learned(tmp, {
            "vcp+CLEAR+bull": {"observed_rr": [3.0, 3.0, 3.0], "p75": 3.0, "sample_count": 3}
        })
        store = make_store(tmp)
        assert abs(store.get("vcp+CLEAR+bull") - 3.0) < 0.01


def test_get_returns_2_0_when_file_unreadable():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "seed_multipliers.json").write_text("not valid json")
        store = make_store(tmp)
        assert store.get("vcp+CLEAR+bull") == 2.0


def test_update_appends_and_rewrites():
    with tempfile.TemporaryDirectory() as d:
        store = make_store(Path(d))
        store.update("vcp+CLEAR+bull", 2.8)
        store.update("vcp+CLEAR+bull", 3.2)
        data = json.loads((Path(d) / "learned_multipliers.json").read_text())
        assert data["vcp+CLEAR+bull"]["observed_rr"] == [2.8, 3.2]
        assert data["vcp+CLEAR+bull"]["sample_count"] == 2


def test_update_discards_invalid_rr():
    with tempfile.TemporaryDirectory() as d:
        store = make_store(Path(d))
        store.update("vcp+CLEAR+bull", 0.0)   # <= 0: discard
        store.update("vcp+CLEAR+bull", -1.0)  # <= 0: discard
        store.update("vcp+CLEAR+bull", 21.0)  # > 20: discard
        store.update("vcp+CLEAR+bull", 2.5)   # valid
        data = json.loads((Path(d) / "learned_multipliers.json").read_text())
        assert data["vcp+CLEAR+bull"]["observed_rr"] == [2.5]


def test_update_computes_correct_p75():
    with tempfile.TemporaryDirectory() as d:
        store = make_store(Path(d))
        for v in [2.0, 2.5, 3.0, 3.5, 4.0]:
            store.update("vcp+CLEAR+bull", v)
        data = json.loads((Path(d) / "learned_multipliers.json").read_text())
        assert data["vcp+CLEAR+bull"]["p75"] == 3.5
