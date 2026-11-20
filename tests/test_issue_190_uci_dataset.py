import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_uci_format_dataset import make_rows


def test_issue_190_dataset_is_600_legal_deduplicated_rows():
    rows = make_rows()
    assert len(rows) == 600
    assert {split: sum(row["split"] == split for row in rows) for split in ("train", "validation", "test")} == {"train": 500, "validation": 50, "test": 50}
    assert len({row["fen"] for row in rows}) == len(rows)
    assert all(row["best_move"] in row["legal_moves"] for row in rows)
    assert all(len(row["best_move"]) in (4, 5) for row in rows)
