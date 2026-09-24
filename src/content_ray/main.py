from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import yaml
from cache_ratelimit import CachedProvider, RateLimiter, RealClock, ThrottledProvider
from llm_client import CompletionRequest, CompletionResult, LlmClient, ReplayProvider, Span
from llm_client.providers.opencode_cli import OpenCodeCLI
from parser_io import ParseError, ParsedDocument, iter_documents, parse
from schema_validate import SchemaRegistry
from secure_base import sanitize_for_prompt
from test_kit import EvalCase, EvalDataset, run

from .models import ContentSummary

DEFAULT_MODEL = "opencode/big-pickle"
SCHEMA_ID = "content-summary-v1"
PROMPT_ID = "content-scan-generator"
ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = ROOT / "prompts"
DEFAULT_PROMPT = PROMPT_DIR / "content-scan-generator.md"
DEFAULT_DATASET = ROOT / "evals" / "content-scan.jsonl"
TTL_PER_FILE = 86400.0


def load_prompt(path: Path) -> tuple[str, str, str, Path]:
    text = path.read_text()
    if not text.startswith("---"):
        raise SystemExit(f"{path}: falta frontmatter")
    _, frontmatter, body = text.split("---", 2)
    data = yaml.safe_load(frontmatter)
    eval_path = ROOT / data["eval"] if not Path(data["eval"]).is_absolute() else Path(data["eval"])
    return data["id"], data["version"], body.strip(), eval_path


def render_content_ray(prompt_id: str, prompt_version: str, variables: dict) -> list[dict]:
    """Templado para `content-scan-generator`; el contenido entra sanitizado
    (`secure-base` lo redacta antes de que cruce al proveedor)."""
    if prompt_id == PROMPT_ID:
        _, _, body, _ = load_prompt(DEFAULT_PROMPT)
        system_part = body.split("## Sistema\n", 1)[1].split("## Usuario\n", 1)[0].strip()
        user_template = body.split("## Usuario\n", 1)[1].strip()
        user_raw = user_template.format(**variables)
        return [
            {"role": "system", "content": system_part},
            {"role": "user", "content": sanitize_for_prompt(user_raw).text},
        ]
    payload = {"prompt_id": prompt_id, "prompt_version": prompt_version, "variables": variables}
    return [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def build_emitter(span_file: Path | None):
    def emit(span: Span, _result) -> None:
        if span_file is not None:
            with span_file.open("a") as handle:
                handle.write(span.as_jsonl() + "\n")
        else:
            import sys

            sys.stderr.write(span.as_jsonl() + "\n")

    return emit


def build_client(inner_provider, *, clock=None, span_file=None) -> dict:
    clock = RealClock() if clock is None else clock
    cached = CachedProvider(inner_provider, ttl_seconds=TTL_PER_FILE, clock=clock)
    limiter = RateLimiter(rpm=120, burst=20, clock=clock)
    provider = ThrottledProvider(cached, limiter)
    registry = SchemaRegistry()
    registry.register(SCHEMA_ID, ContentSummary)
    client = LlmClient(
        provider,
        consumer_repo="content-ray",
        model_aliases={"fast": DEFAULT_MODEL},
        renderer=render_content_ray,
        validator=registry.make_validator(SCHEMA_ID),
        emitter=build_emitter(span_file),
    )
    return {"client": client, "cached": cached, "limiter": limiter}


def panel_for(doc: ParsedDocument) -> str:
    """Índice del documento: secciones con anchor/range/tokens + resumen de tablas."""
    lines = [f"- {doc.source} [{doc.format}] {doc.approx_tokens} tokens"]
    for section in doc.sections:
        lines.append(f"    {section.anchor} (L{section.line_start}-L{section.line_end}) ~{section.body_tokens} tok")
    if doc.tables:
        lines.append(f"    → {len(doc.tables)} tablas ({sum(len(t.rows) for t in doc.tables)} filas)")
    return "\n".join(lines)


def scan_corpus(corpus: Path, *, client: LlmClient) -> tuple[ParsedDocument, ...]:
    """Necesita los documentos parseados y el índice que se le pasa al prompt."""
    documents: list[ParsedDocument] = []
    for path in iter_documents(corpus):
        try:
            documents.append(parse(path))
        except ParseError as exc:
            print(f"salteado {exc}")  # el corpus se tolera parcialmente; no se corta en silencio
    return tuple(documents)


def summarize(documents: tuple[ParsedDocument, ...], *, client: LlmClient, corpus: Path) -> ContentSummary:
    total_tokens = sum(d.approx_tokens for d in documents)
    sections = sum(len(d.sections) for d in documents)
    panel = "\n".join(panel_for(doc) for doc in documents) or "(corpus vacío)"
    result = client.complete(
        CompletionRequest(
            prompt_id=PROMPT_ID,
            prompt_version="0.1.0",
            variables={
                "date": date.today().isoformat(),
                "corpus": str(corpus),
                "files": str(len(documents)),
                "sections": str(sections),
                "est_tokens": str(total_tokens),
                "indice": panel,
            },
            model_alias="fast",
            response_schema=SCHEMA_ID,
            tags=["content-ray", "week-10"],
        )
    )
    if not result.validation.ok:
        raise SystemExit("; ".join(result.validation.errors))
    return result.parsed


def judge_for(client: LlmClient) -> Callable[[EvalCase], CompletionResult]:
    def judge(case: EvalCase) -> CompletionResult:
        return client.complete(
            CompletionRequest(
                prompt_id=case.prompt_id,
                prompt_version=case.prompt_version,
                variables=case.input,
                model_alias="fast",
                response_schema=SCHEMA_ID,
                tags=["content-ray", "week-10"],
            )
        )

    return judge


def run_replay_check() -> int:
    dataset = EvalDataset.from_jsonl(DEFAULT_DATASET)
    provider = ReplayProvider(ROOT / "cassettes", record=False)
    client = build_client(provider)["client"]
    report = run(dataset, judge_for(client), mode="full", threshold=1.0)
    print(f"replay: pass={report.passed}/{report.total} threshold_ok={report.threshold_ok}")
    return 0 if report.threshold_ok else 1


def run_record() -> int:
    dataset = EvalDataset.from_jsonl(DEFAULT_DATASET)
    provider = ReplayProvider(ROOT / "cassettes", record=True, inner=OpenCodeCLI(DEFAULT_MODEL))
    client = build_client(provider)["client"]
    report = run(dataset, judge_for(client), mode="full", threshold=1.0)
    print(f"grabadas {len(dataset.cases)} respuestas; pass={report.passed}/{report.total}")
    return 0 if report.threshold_ok else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="content-ray: escáner de corpus (semana 10).")
    parser.add_argument("--dir", type=Path, help="corpus a escanear (repo/carpeta de archivos)")
    parser.add_argument("--sample", help="'archivo:línea' → imprime la sección que contiene esa línea")
    parser.add_argument(
        "command",
        nargs="?",
        default="scan",
        choices=["scan", "sample", "replay-check", "record"],
        help="scan resume el corpus; sample imprime una ventana; replay-check/record usan la cinta (D4).",
    )
    args = parser.parse_args(argv)

    if args.command in ("replay-check", "record"):
        raise SystemExit(run_record() if args.command == "record" else run_replay_check())

    if args.sample:
        source, _, lineno = args.sample.partition(":")
        doc = parse(Path(source))
        wanted = int(lineno)
        hit = next((s for s in doc.sections if s.line_start <= wanted <= s.line_end), None)
        if hit is None:
            raise SystemExit(f"{source}:{lineno}: sin sección en esa línea")
        print(f"{hit.title} (L{hit.line_start}-{hit.line_end})")
        print(hit.body)
        return

    if not args.dir:
        parser.error("scan necesita --dir")
    client = build_client(OpenCodeCLI(DEFAULT_MODEL))["client"]
    documents = scan_corpus(args.dir, client=client)
    summary = summarize(documents, client=client, corpus=args.dir)
    print(f"{summary.corpus}: {summary.files} archivos · {summary.sections} secciones · "
          f"{summary.total_tokens} tokens aproximados")
    for highlight in summary.highlights:
        print(f"- {highlight}")


if __name__ == "__main__":
    main()