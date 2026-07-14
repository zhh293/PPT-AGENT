"""visual_generation tools — run_shell_command, check_file_exists, list_directory, generate_images."""

import os
import subprocess
from pathlib import Path

from ppt_agent.runtime.agent_loop import ToolResult
from ppt_agent.tools.registry import WORKER_AND_ABOVE, ToolDescriptor


def register_tools(registry, workspace, capability, llm_client=None):
    def generate_images(call_id: str, arguments: dict) -> ToolResult:
        """Run image generation — deterministic background quality check + GPTImage2 launch."""
        try:
            from ppt_agent.workers.image_generator import run as gen_run
            mode = arguments.get("mode", "all")
            force = arguments.get("force", False)
            output = gen_run(workspace, force=force, mode=mode)  # No llm_client
            return ToolResult(call_id=call_id, output={"path": str(output), "mode": mode}, success=True)
        except Exception as e:
            return ToolResult(call_id=call_id, output=None, success=False, error=str(e))

    registry.register_tool(
        ToolDescriptor("generate_images", "generation", WORKER_AND_ABOVE,
                       "Run visual generation (background quality check + GPTImage2 skill). Mode: all/key/cover-only/none.",
                       {"mode": {"type": "string", "default": "all"}, "force": {"type": "boolean", "default": False}}),
        generate_images)

    def run_shell(call_id: str, arguments: dict) -> ToolResult:
        cmd = arguments.get("command", "")
        timeout = int(arguments.get("timeout", 120))
        cwd = arguments.get("cwd") or str(workspace.root)
        if not cmd.strip():
            return ToolResult(call_id=call_id, output=None, success=False, error="Empty command")

        # Sandbox: block dangerous patterns (keep in sync with worker_agent._BLOCKED_PATTERNS)
        blocked = (
            "rm -rf / ", "rm -rf /* ", "rm -r -f / ", "rm -r -f /* ",
            "mkfs", "mke2fs", "dd if=", ":(){ :|:& };:",
        )
        for p in blocked:
            if p in cmd.lower():
                return ToolResult(call_id=call_id, output=None, success=False, error=f"Blocked: {p}")

        work_dir = Path(cwd).resolve()
        if workspace.root.resolve() not in work_dir.parents and work_dir != workspace.root.resolve():
            return ToolResult(call_id=call_id, output=None, success=False, error=f"CWD outside workspace: {work_dir}")

        api_keys = {k: v for k, v in os.environ.items() if k.endswith("_API_KEY") or k.endswith("_API_TOKEN")}
        safe_env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
                    "LANG": "en_US.UTF-8", "PYTHONUNBUFFERED": "1",
                    "WORKSPACE_ROOT": str(workspace.root), "TMPDIR": os.environ.get("TMPDIR", ""),
                    "PYTHONPATH": os.environ.get("PYTHONPATH", ""), **api_keys}
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=str(work_dir), env=safe_env)
            return ToolResult(call_id=call_id, output={"exit_code": r.returncode, "stdout": r.stdout[-3000:], "stderr": r.stderr[-1000:]},
                              success=r.returncode == 0, error=f"Exit {r.returncode}" if r.returncode else None)
        except subprocess.TimeoutExpired:
            return ToolResult(call_id=call_id, output=None, success=False, error=f"Timeout after {timeout}s")
        except Exception as e:
            return ToolResult(call_id=call_id, output=None, success=False, error=str(e))

    registry.register_tool(ToolDescriptor("run_shell_command", "execution", WORKER_AND_ABOVE,
                                          "Run a shell command within the workspace sandbox.",
                                          {"command": {"type": "string"}, "timeout": {"type": "integer", "default": 120}}), run_shell)

    def check_exists(call_id: str, arguments: dict) -> ToolResult:
        p = Path(arguments.get("path", ""))
        if not p.is_absolute(): p = workspace.root / p
        return ToolResult(call_id=call_id, output={"exists": p.exists(), "path": str(p)}, success=True)

    registry.register_tool(ToolDescriptor("check_file_exists", "filesystem", WORKER_AND_ABOVE,
                                          "Check if a file exists.", {"path": {"type": "string"}}), check_exists)

    def list_dir(call_id: str, arguments: dict) -> ToolResult:
        p = Path(arguments.get("path", "."))
        if not p.is_absolute(): p = workspace.root / p
        try:
            items = [{"name": x.name, "is_dir": x.is_dir()} for x in sorted(p.iterdir())]
            return ToolResult(call_id=call_id, output={"items": items}, success=True)
        except Exception as e:
            return ToolResult(call_id=call_id, output=None, success=False, error=str(e))

    registry.register_tool(ToolDescriptor("list_directory", "filesystem", WORKER_AND_ABOVE,
                                          "List files in a directory.", {"path": {"type": "string", "default": "."}}), list_dir)
