# Coordinator real-model evaluation (2026-07-29)

## Evaluation setup

- Input: `tests/fixtures/sample_project/input`
- Coordinator job: `workspace/jobs/coordinator-demo-20260729-01`
- Baseline job: `workspace/jobs/coordinator-baseline-20260729-01`
- Real-model profile: `coordinator:deepseek`
- Baseline: the same workers executed linearly without an LLM
- Visual generation: requested in full mode; the external GPTImage2 endpoint failed its TLS handshake, so both jobs used the production template fallback
- Both PPTX files were opened by the renderer, rendered slide-by-slide, and compared as contact sheets

## Results

| Metric | Deterministic baseline | Coordinator + real model |
|---|---:|---:|
| Slides produced | 8 | 12 |
| Replaced text zones | 94 | 155 |
| LLM-authored text-zone ratio | 0% | 89.68% |
| Mapping diagnostic issues | 18 | 9 |
| Visual audit aggregate | 77.4 | 79.1 |
| Manual-review items | 7 | 3 |
| Final PPTX produced | yes | yes |

The Coordinator run improved narrative coverage and halved mapping diagnostics. It also survived the unavailable image service and produced a usable PPTX through the configured fallback path. The durable state converged to eight completed phases with no stale running phase.

## Quality-gate result

The Coordinator job is intentionally recorded as `completed_with_warnings`, not as a clean pass. Fresh-eyes validation scored 70 and correctly flagged residual template content and off-domain visuals on later slides. The selected template had low retrieval confidence, and the image replacement service was unavailable; those are output-quality limitations, not orchestration failures.

This evaluation demonstrates that Coordinator mode improves mapping and production resilience, while also showing that template-confidence gating and image-service reliability remain the next quality bottlenecks.
