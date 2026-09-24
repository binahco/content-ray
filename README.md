# content-ray

Escáner de corpus (semana 10): normaliza archivos reales con `parser-io` y produce
un **digest LLM validado** del contenido. El LLM nunca recibe las secciones crudas:
recibe el **índice** (anclas, rangos de línea, presupuesto de tokens) sanitizado con
`secure-base`, y devuelve un resumen con highlights (`content-summary-v1`).

## Demo

```bash
uv sync
uv run content-ray scan --dir ../llm-dev-core/docs/
# ../llm-dev-core/docs: 9 archivos · 40 secciones · 42100 tokens aproximados
# - 9 decisiones de arquitectura cubiertas, una por semana (ADR-0..ADR-9)
# - parser-io documenta su propio contrato y su criterio de aceptación
uv run content-ray sample --sample ../llm-dev-core/docs/decisions/0009-parser-io-contract.md:2
# Contrato mínimo — parser-io  (L1-L6)
# - **Fecha:** 2026-10-01
# ...
```

`sample archivo:línea` imprime la **ventana** (la sección que contiene esa línea,
con su cuerpo) — la pieza reutilizable para ventanas de prompt sobre el
`ParsedDocument`.

## Cómo funciona

```
content-ray scan --dir CORPUS
  ├─ iter_documents(CORPUS)         # sorted, extensions conocidas
  ├─ parse(archivo)                 # parser-io: markdown/csv/json → ParsedDocument
  ├─ panel_for(doc)                 # índice: anchor (Lx-Ly) ~tokens, tablas
  └─ summarize(…)                   # LLM digest sobre el índice SANITIZADO
      └─ content-summary-v1          # schema-validate: salida inválida = no entra
```

Un archivo que no parsea se **salta y se avisa** (nada se corta en silencio §5.1).
El proveedor va por `ThrottledProvider(CachedProvider)` con TTL diario por archivo:
el mismo corpus del mismo día no re-relee ni re-paga.

## Recicla de

| Módulo | Uso |
|---|---|
| `parser-io` | la capa de lectura: `parse`/`iter_documents` → `ParsedDocument` con líneas y tokens |
| `llm-client` | la llamada del digest (retry + reparación + span de 20 campos) |
| `schema-validate` | la salida del digest valida contra `content-summary-v1` o falla |
| `test-kit` | el prompt nace evaluado: dataset congelado + replay determinista (D4) |
| `secure-base` | el contenido externo se redacta antes de que cruce al proveedor |
| `cache-ratelimit` | caché TTL + token bucket por delante del proveedor |
| `ci-pack` | lints del audit y workflow `eval-smoke.yml` |

## Limitaciones

- `parser-io` v1 lee `markdown`/`csv`/`json`: un HTML o PDF del corpus se saltea
  hasta el lector de `scraper` (sem. 28).
- El digest ve el **índice**, no las secciones completas: si un corpus necesita
  lectura real del cuerpo, `vector-core` (sem. 16) define ventanas/chunks sobre
  `ParsedDocument`.
- `approx_tokens` es heurístico (~1/4 char): el conteo exacto es de `vector-core`.

## Roadmap

- [x] `scan` indexa y resume un corpus con digest LLM validado (sem. 10)
- [x] `sample archivo:línea` → ventana por sección sobre `ParsedDocument`
- [x] Contenido externo sanitizado con `secure-base` antes del proveedor
- [ ] Digest alimentado por secciones reales (chunks) cuando llegue `vector-core`