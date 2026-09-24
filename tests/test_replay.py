from __future__ import annotations

from pathlib import Path

import pytest
from content_ray.main import DEFAULT_DATASET, DEFAULT_PROMPT, build_client, judge_for, load_prompt
from llm_client import ReplayProvider
from test_kit import EvalDataset, run

ROOT = Path(__file__).resolve().parents[1]
CASSETTES = ROOT / "cassettes"


@pytest.mark.skipif(
    not CASSETTES.is_dir() or not list(CASSETTES.glob("complete-*.jsonl")),
    reason="tape real no grabada (content-ray record)",
)
def test_replay_dataset_en_verde() -> None:
    dataset = EvalDataset.from_jsonl(DEFAULT_DATASET)
    prompt_id, prompt_version, _, _ = load_prompt(DEFAULT_PROMPT)
    assert dataset.prompt_id == prompt_id and dataset.prompt_version == prompt_version

    provider = ReplayProvider(CASSETTES, record=False)
    client = build_client(provider)["client"]
    report = run(dataset, judge_for(client), mode="full", threshold=1.0)
    assert report.threshold_ok, report.model_dump()
    assert report.total == 3