"""visual_generation tools — run_shell_command, check_file_exists, list_directory."""

import os
import subprocess
from pathlib import Path

from ppt_agent.runtime.agent_loop import ToolResult
from ppt_agent.tools.registry import WORKER_AND_ABOVE, ToolDescriptor


def register_tools(registry, workspace, capability, llm_client=None):
    def run_shell(args: dict) -> ToolResult:
        cmd = args.get("command", "")
        timeout = int(args.get("timeout", 120))
        cwd = args.get("cwd") or str(workspace.root)
        if not cmd.strip():
            return ToolResult(call_id="", output=None, success=False, error="Empty command")

        # Sandbox: block dangerous patterns
        blocked = ("rm -rf / ", "rm -rf /* ", "rm -r -f / ", "mkfs", "mke2fs", "dd if=", ":(){ :|:& };:")
        for p in blocked:
            if p in cmd.lower():
                return ToolResult(call_id="", output=None, success=False, error=f"Blocked: {p}")

        work_dir = Path(cwd).resolve()
        if workspace.root.resolve() not in work_dir.parents and work_dir != workspace.root.resolve():
            return ToolResult(call_id="", output=None, success=False, error=f"CWD outside workspace: {work_dir}")

        api_keys = {k: v for k, v in os.environ.items() if k.endswith("_API_KEY") or k.endswith("_API_TOKEN")}
        safe_env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
                    "LANG": "en_US.UTF-8", "PYTHONUNBUFFERED": "1",
                    "WORKSPACE_ROOT": str(workspace.root), "TMPDIR": os.environ.get("TMPDIR", ""),
                    "PYTHONPATH": os.environ.get("PYTHONPATH", ""), **api_keys}
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=str(work_dir), env=safe_env)
            return ToolResult(call_id="", output={"exit_code": r.returncode, "stdout": r.stdout[-3000:], "stderr": r.stderr[-1000:]},
                              success=r.returncode == 0, error=f"Exit {r.returncode}" if r.returncode else None)
        except subprocess.TimeoutExpired:
            return ToolResult(call_id="", output=None, success=False, error=f"Timeout after {timeout}s")
        except Exception as e:
            return ToolResult(call_id="", output=None, success=False, error=str(e))

    registry.register_tool(ToolDescriptor("run_shell_command", "execution", WORKER_AND_ABOVE,
                                          "Run a shell command within the workspace sandbox.",
                                          {"command": {"type": "string"}, "timeout": {"type": "integer", "default": 120}}), run_shell)

    def check_exists(args: dict) -> ToolResult:
        p = Path(args.get("path", ""))
        if not p.is_absolute(): p = workspace.root / p
        return ToolResult(call_id="", output={"exists": p.exists(), "path": str(p)}, success=True)

    registry.register_tool(ToolDescriptor("check_file_exists", "filesystem", WORKER_AND_ABOVE,
                                          "Check if a file exists.", {"path": {"type": "string"}}), check_exists)

    def list_dir(args: dict) -> ToolResult:
        p = Path(args.get("path", "."))
        if not p.is_absolute(): p = workspace.root / p
        try:
            items = [{"name": x.name, "is_dir": x.is_dir()} for x in sorted(p.iterdir())]
            return ToolResult(call_id="", output={"items": items}, success=True)
        except Exception as e:
            return ToolResult(call_id="", output=None, success=False, error=str(e))

    registry.register_tool(ToolDescriptor("list_directory", "filesystem", WORKER_AND_ABOVE,
                                          "List files in a directory.", {"path": {"type": "string", "default": "."}}), list_dir)
