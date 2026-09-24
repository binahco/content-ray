from __future__ import annotations

from datetime import datetime
from pathlib import Path

from content_ray.main import (
    DEFAULT_DATASET,
    build_client,
    judge_for,
    panel_for,
    render_content_ray,
    scan_corpus,
    summarize,
)
from llm_client.provider import ProviderRequest, ProviderResponse
from parser_io import parse
from test_kit import EvalDataset, run as run_evalset

STUB_TEXT = (
    '{"corpus": "docs", "files": 2, "sections": 3, "total_tokens": 900, "highlights": ["a", "b"]}'
)


class StubProvider:
    name = "stub"

    def __init__(self, text: str | None = None) -> None:
        self.text = text or STUB_TEXT
        self.calls = 0

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        self.last_request = request
        return ProviderResponse(text=self.text, model=request.model)

    def stream(self, request: ProviderRequest) -> list[str]:
        self.calls += 1
        return [self.text]


class FakeClock:
    def __init__(self, tick: float = 0.0) -> None:
        self._tick = tick

    def monotonic(self) -> float:
        return self._tick

    def sleep(self, seconds: float) -> None:
        self._tick += seconds

    def now(self) -> datetime:
        return datetime(2026, 10, 1, 8, 0, 0)


def test_panel_cubre_titulos_y_rangos(tmp_path: Path) -> None:
    path = tmp_path / "m.md"
    path.write_text("# T\n\n## Sección A\n\ncuerpo A.\n")
    panel = panel_for(parse(path))
    assert "m.md [markdown]" in panel
    assert "sección-a (L3-L5)" in panel
    assert "~2 tok" in panel


def test_scan_y_resumen_con_stub(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# Uno\n\n## 1.1\n\ntexto.\n")
    (tmp_path / "b.csv").write_text("x,y\n1,2\n")
    stub = StubProvider()
    client = build_client(stub, clock=FakeClock())["client"]
    docs = scan_corpus(tmp_path, client=client)
    assert [d.format for d in docs] == ["markdown", "csv"]
    summary = summarize(docs, client=client, corpus=tmp_path)
    assert summary.files == 2
    assert summary.highlights[:2] == ["a", "b"]
    assert stub.calls == 1
    user = [m["content"] for m in stub.last_request.messages if m["role"] == "user"][0]
    assert "Archivos: 2" in user and "Secciones: 1" in user


def test_scan_salteta_archivo_invalido_y_sigue(tmp_path: Path) -> None:
    (tmp_path / "ok.md").write_text("# Fine\n\ntexto.\n")
    (tmp_path / "roto.json").write_text("{no-json")
    stub = StubProvider()
    client = build_client(stub, clock=FakeClock())["client"]
    docs = scan_corpus(tmp_path, client=client)
    assert [d.source.rsplit("/", 1)[-1] for d in docs] == ["ok.md"]


def test_el_renderer_redacta_contenido_externo() -> None:
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    messages = render_content_ray(
        "content-scan-generator",
        "0.1.0",
        {
            "date": "2026-10-01",
            "corpus": "docs",
            "files": "9",
            "sections": "40",
            "est_tokens": "42000",
            "indice": secret,
        },
    )
    user = messages[-1]["content"]
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in user
    assert "[REDACTED:" in user


def test_el_judge_evoluciona_el_dataset_completo() -> None:
    dataset = EvalDataset.from_jsonl(DEFAULT_DATASET)
    stub = StubProvider()
    client = build_client(stub, clock=FakeClock())["client"]
    out = run_evalset(dataset, judge_for(client), mode="full", threshold=1.0)
    assert out.total == 3
    assert out.passed == 3