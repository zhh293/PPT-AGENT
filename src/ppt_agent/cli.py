from __future__ import annotations

import argparse
from pathlib import Path

from ppt_agent.coordinator.phase_state import create_job
from ppt_agent.coordinator.workflow import run_workflow
from ppt_agent.models.artifacts import JobWorkspace, atomic_write_json, read_json
from ppt_agent.models.schema_loader import load_schema, validate_required
from ppt_agent.models.slide_contents import approve_slide_contents


def cmd_create_job(args: argparse.Namespace) -> None:
    workspace = create_job(Path(args.input), Path(args.output) if args.output else None)
    print(str(workspace.root))


def cmd_run(args: argparse.Namespace) -> None:
    outputs = run_workflow(
        Path(args.job),
        until=args.until,
        start_from=args.start_from,
        force=args.force,
        model_profile=args.model_profile,
    )
    for path in outputs:
        print(path)


def cmd_approve(args: argparse.Namespace) -> None:
    workspace = JobWorkspace(Path(args.job))
    slide_path = Path(args.slide_contents) if args.slide_contents else workspace.artifact_path("slide_contents")
    payload = approve_slide_contents(read_json(slide_path))
    atomic_write_json(slide_path, payload)
    print(slide_path)


def cmd_preview(args: argparse.Namespace) -> None:
    """Generate an HTML preview of slide_contents for visual review."""
    from ppt_agent.assembly.html_preview import write_preview
    workspace = JobWorkspace(Path(args.job))
    output_path = write_preview(workspace.root)
    print(str(output_path))


def cmd_validate_artifacts(args: argparse.Namespace) -> None:
    workspace = JobWorkspace(Path(args.job))
    contracts = Path(args.contracts)
    mapping = {
        "source_summary": "source-summary.schema.json",
        "outline": "outline.schema.json",
        "selected_template": "selected-template.schema.json",
        "template_meta": "template-meta.schema.json",
        "slide_design_plan": "slide-design-plan.schema.json",
        "slide_contents": "slide-contents.schema.json",
        "image_generation_config": "image-generation-config.schema.json",
        "image_generation_report": "image-generation-report.schema.json",
        "validation_report": "validation-report.schema.json",
    }
    checked = []
    for artifact, schema_name in mapping.items():
        path = workspace.artifact_path(artifact)
        if not path.exists():
            continue
        validate_required(load_schema(schema_name, contracts), read_json(path), path.name)
        checked.append(path.name)
    print("validated: " + ", ".join(checked))


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the web dashboard server."""
    from ppt_agent.server.app import start_server
    print(f"Starting PPT Agent Dashboard at http://localhost:{args.port}")
    start_server(host=args.host, port=args.port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ppt-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-job")
    create.add_argument("--input", required=True)
    create.add_argument("--output")
    create.set_defaults(func=cmd_create_job)

    run = sub.add_parser("run")
    run.add_argument("--job", required=True)
    run.add_argument("--until")
    run.add_argument("--from", dest="start_from")
    run.add_argument("--force", action="store_true")
    run.add_argument("--model-profile", dest="model_profile", default=None,
                     help="LLM provider profile from config/models.yml (e.g., anthropic, openai, fake)")
    run.set_defaults(func=cmd_run)

    approve = sub.add_parser("approve")
    approve.add_argument("--job", required=True)
    approve.add_argument("--slide-contents")
    approve.set_defaults(func=cmd_approve)

    preview = sub.add_parser("preview")
    preview.add_argument("--job", required=True,
                         help="Path to job workspace. Generates preview.html from slide_contents.json.")
    preview.set_defaults(func=cmd_preview)

    validate = sub.add_parser("validate-artifacts")
    validate.add_argument("--job", required=True)
    validate.add_argument("--contracts", default="specs/001-ppt-generation-agent/contracts")
    validate.set_defaults(func=cmd_validate_artifacts)

    serve = sub.add_parser("serve", help="Start the web dashboard server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
