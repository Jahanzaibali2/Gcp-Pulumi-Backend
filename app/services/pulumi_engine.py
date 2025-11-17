
from __future__ import annotations
import os, shutil
from pathlib import Path
from typing import Dict, Any
from pulumi import automation as auto
from .program_builder import build_pulumi_program
from .validator import IRValidator

DEFAULT_REGION = os.getenv("GCP_REGION", "us-central1")

def _ensure_pulumi_env() -> dict:
    """
    Ensure Pulumi CLI is discoverable and configure a persistent local backend.
    """
    env = os.environ.copy()

    # 1) pulumi.exe PATH hint (Windows) - Set PATH early and update os.environ immediately
    pulumi_dir = None
    if not shutil.which("pulumi"):
        # Check for custom Pulumi CLI path from environment
        custom_pulumi_path = os.getenv("PULUMI_CLI_PATH")
        if custom_pulumi_path:
            pulumi_dir = str(Path(custom_pulumi_path))
            new_path = pulumi_dir + os.pathsep + env.get("PATH", "")
            env["PATH"] = new_path
            os.environ["PATH"] = new_path
        else:
            # Try default Windows location
            default_dir = Path(r"C:\Program Files (x86)\Pulumi")
            if (default_dir / "pulumi.exe").exists():
                pulumi_dir = str(default_dir)
                # Update both the env dict AND os.environ immediately
                new_path = pulumi_dir + os.pathsep + env.get("PATH", "")
                env["PATH"] = new_path
                os.environ["PATH"] = new_path
            else:
                raise RuntimeError("pulumi.exe not found on PATH. Install Pulumi, add it to PATH, or set PULUMI_CLI_PATH in .env")
    else:
        # Pulumi found, but ensure it's in PATH for subprocesses
        pulumi_path = shutil.which("pulumi")
        if pulumi_path:
            pulumi_dir = str(Path(pulumi_path).parent)
            if pulumi_dir not in env.get("PATH", ""):
                new_path = pulumi_dir + os.pathsep + env.get("PATH", "")
                env["PATH"] = new_path
                os.environ["PATH"] = new_path

    # 2) Local backend (no Pulumi Cloud token needed)
    # Get paths from environment variables or use defaults
    base_dir = Path.cwd().resolve()
    state_dir_name = os.getenv("PULUMI_STATE_DIR", "pulumi-state")
    state_dir = Path(state_dir_name) if Path(state_dir_name).is_absolute() else base_dir / state_dir_name
    state_dir.mkdir(parents=True, exist_ok=True)
    
    # For local file backend, try using just the directory path
    # Some Pulumi versions accept directory paths directly
    # If that doesn't work, we'll use file:// URL format
    abs_path_str = str(state_dir.resolve())
    
    # Try using file:// with 2 slashes instead of 3 (file://c:/path)
    # Convert to forward slashes and lowercase drive
    normalized = abs_path_str.replace('\\', '/')
    if len(normalized) > 1 and normalized[1] == ':':
        normalized = normalized[0].lower() + normalized[1:]
    # Use file:// with 2 slashes: file://c:/path (not file:///c:/path)
    env["PULUMI_BACKEND_URL"] = f"file://{normalized}"

    # Optional: keep plugins/cache tidy
    pulumi_home_name = os.getenv("PULUMI_HOME_DIR", ".pulumi-home")
    pulumi_home = Path(pulumi_home_name) if Path(pulumi_home_name).is_absolute() else base_dir / pulumi_home_name
    pulumi_home.mkdir(parents=True, exist_ok=True)
    env["PULUMI_HOME"] = str(pulumi_home)
    
    # Set passphrase for secrets manager (required for local backend)
    # Use a default passphrase if not set (for local development)
    if "PULUMI_CONFIG_PASSPHRASE" not in env:
        env["PULUMI_CONFIG_PASSPHRASE"] = os.getenv("PULUMI_CONFIG_PASSPHRASE", "dev-passphrase")

    return env

# Create work directory with absolute path
# Get from environment variable or use default
base_dir = Path.cwd().resolve()
work_dir_name = os.getenv("PULUMI_WORK_DIR", "pulumi-work")
WORK_DIR = Path(work_dir_name) if Path(work_dir_name).is_absolute() else base_dir / work_dir_name
WORK_DIR.mkdir(parents=True, exist_ok=True)

class PulumiEngine:
    @staticmethod
    def _set_gcp_config(stack, ir: Dict[str, Any]):
        # Project & region are taken from env exported by main.py
        project = os.environ.get("GOOGLE_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        region = ir.get("location") or ir.get("region") or os.environ.get("GOOGLE_REGION") or DEFAULT_REGION
        if not project:
            raise RuntimeError("GOOGLE_PROJECT not set. Export GCP creds in the request.")
        # Set configs early to avoid region validation warnings
        stack.set_config("gcp:project", auto.ConfigValue(value=project))
        stack.set_config("gcp:region", auto.ConfigValue(value=region))
        # Suppress Compute Engine API warning - we don't need it for Storage/PubSub/CloudRun
        # This warning is harmless and doesn't affect functionality

    @staticmethod
    def preview(ir: Dict[str, Any]):
        project = ir.get("project", "canvas")
        env_name = ir.get("env", "dev")
        
        # Run validation before preview
        project_id = os.environ.get("GOOGLE_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        region = ir.get("location") or ir.get("region") or os.environ.get("GOOGLE_REGION") or DEFAULT_REGION
        
        validator = IRValidator(ir, project_id or "unknown", region)
        validation_errors, validation_warnings = validator.validate()
        
        # If there are validation errors, return them immediately
        if validation_errors:
            return {
                "preview": False,
                "validation_failed": True,
                "errors": validation_errors,
                "warnings": validation_warnings,
                "message": "Validation failed. Please fix the errors before deploying."
            }
        
        program = build_pulumi_program(ir)

        pulumi_env = _ensure_pulumi_env()
        # Update environment BEFORE creating stack so Pulumi Automation API can find pulumi CLI
        os.environ.update(pulumi_env)
        # Also ensure PATH is set in current process (critical for Windows)
        if "PATH" in pulumi_env:
            current_path = os.environ.get("PATH", "")
            if pulumi_env["PATH"] not in current_path:
                os.environ["PATH"] = pulumi_env["PATH"] + os.pathsep + current_path

        stack = auto.create_or_select_stack(
            stack_name=f"{project}-{env_name}",
            project_name=project,
            program=program,
            work_dir=str(WORK_DIR),
        )
        PulumiEngine._set_gcp_config(stack, ir)

        res = stack.preview(on_output=print)
        
        # Include validation warnings in the response even if preview succeeds
        return {
            "preview": True,
            "changeSummary": res.change_summary,
            "validation_warnings": validation_warnings if validation_warnings else None
        }

    @staticmethod
    def up(ir: Dict[str, Any]):
        project = ir.get("project", "canvas")
        env_name = ir.get("env", "dev")
        program = build_pulumi_program(ir)

        pulumi_env = _ensure_pulumi_env()
        # Update environment BEFORE creating stack so Pulumi Automation API can find pulumi CLI
        os.environ.update(pulumi_env)
        # Also ensure PATH is set in current process (critical for Windows)
        if "PATH" in pulumi_env:
            current_path = os.environ.get("PATH", "")
            if pulumi_env["PATH"] not in current_path:
                os.environ["PATH"] = pulumi_env["PATH"] + os.pathsep + current_path

        stack = auto.create_or_select_stack(
            stack_name=f"{project}-{env_name}",
            project_name=project,
            program=program,
            work_dir=str(WORK_DIR),
        )
        PulumiEngine._set_gcp_config(stack, ir)

        up_res = stack.up(on_output=print)
        return {
            "preview": False,
            "outputs": {k: v.value for k, v in (up_res.outputs or {}).items()},
            "summary": {
                "resources": (up_res.summary.resource_changes if up_res.summary else None),
                "duration_sec": (up_res.summary.duration_seconds if up_res.summary and hasattr(up_res.summary, 'duration_seconds') else None),
            },
        }

    @staticmethod
    def destroy(project: str, env_name: str):
        def program(): pass
        pulumi_env = _ensure_pulumi_env()
        # Update environment BEFORE creating stack so Pulumi Automation API can find pulumi CLI
        os.environ.update(pulumi_env)
        # Also ensure PATH is set in current process (critical for Windows)
        if "PATH" in pulumi_env:
            current_path = os.environ.get("PATH", "")
            if pulumi_env["PATH"] not in current_path:
                os.environ["PATH"] = pulumi_env["PATH"] + os.pathsep + current_path

        stack = auto.create_or_select_stack(
            stack_name=f"{project}-{env_name}",
            project_name=project,
            program=program,
            work_dir=str(WORK_DIR),
        )
        res = stack.destroy(on_output=print)
        stack.workspace.remove_stack(stack.name)
        return {"destroyed": True}
