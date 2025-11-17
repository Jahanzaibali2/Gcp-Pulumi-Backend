
from __future__ import annotations
import os, shutil
import json
import logging
from pathlib import Path
from typing import Dict, Any
from pulumi import automation as auto
from .program_builder import build_pulumi_program
from .validator import IRValidator

# Set up logger for lock handling
logger = logging.getLogger(__name__)

DEFAULT_REGION = os.getenv("GCP_REGION", "us-central1")

def _ensure_pulumi_env() -> dict:
    """
    Ensure Pulumi CLI is discoverable and configure a persistent local backend.
    """
    env = os.environ.copy()

    # 1) pulumi.exe PATH hint (Windows) - Set PATH early and update os.environ immediately
    pulumi_dir = None
    pulumi_path = shutil.which("pulumi")
    
    # If not found, try to locate it
    if not pulumi_path:
        # Check for custom Pulumi CLI path from environment
        custom_pulumi_path = os.getenv("PULUMI_CLI_PATH")
        if custom_pulumi_path:
            custom_path = Path(custom_pulumi_path)
            if custom_path.is_file():
                pulumi_path = str(custom_path)
            elif custom_path.is_dir() and (custom_path / "pulumi.exe").exists():
                pulumi_path = str(custom_path / "pulumi.exe")
            pulumi_dir = str(custom_path.parent if custom_path.is_file() else custom_path)
        else:
            # Try default Windows locations
            default_dirs = [
                Path(r"C:\Program Files (x86)\Pulumi"),
                Path(r"C:\Program Files\Pulumi"),
            ]
            for default_dir in default_dirs:
                pulumi_exe = default_dir / "pulumi.exe"
                if pulumi_exe.exists():
                    pulumi_path = str(pulumi_exe)
                    pulumi_dir = str(default_dir)
                    break
        
        if not pulumi_path:
            raise RuntimeError("pulumi.exe not found on PATH. Install Pulumi, add it to PATH, or set PULUMI_CLI_PATH in .env")
    
    # Ensure pulumi directory is in PATH
    if pulumi_path:
        pulumi_dir = str(Path(pulumi_path).parent)
        if pulumi_dir not in env.get("PATH", ""):
            new_path = pulumi_dir + os.pathsep + env.get("PATH", "")
            env["PATH"] = new_path
            os.environ["PATH"] = new_path
    
    # Verify pulumi is accessible (but don't fail if shutil.which doesn't find it yet)
    # The PATH update should make it available to subprocesses
    import subprocess
    try:
        # Try to run pulumi version to verify it works
        test_env = env.copy()
        result = subprocess.run(
            [pulumi_path, "version"],
            capture_output=True,
            text=True,
            timeout=10,
            env=test_env,
            shell=False  # Explicitly disable shell on Windows
        )
        if result.returncode != 0:
            # Log warning but don't fail - might work in subprocess context
            import logging
            logging.warning(f"Pulumi version check returned non-zero: {result.stderr}")
    except FileNotFoundError:
        raise RuntimeError(f"Pulumi executable not found at {pulumi_path}")
    except Exception as e:
        # Log but don't fail - might work in actual subprocess context
        import logging
        logging.warning(f"Pulumi verification warning: {e}")

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
    def _handle_stack_lock(stack, operation_name: str, operation_func):
        """
        Execute a stack operation with automatic lock handling.
        If a lock is detected, cancel it and retry.
        """
        import time
        max_retries = 2
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                return operation_func()
            except Exception as e:
                error_str = str(e)
                # Check if error is due to stack lock
                if "locked" in error_str.lower() or "lock" in error_str.lower():
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Stack is locked, attempting to cancel lock (attempt {attempt + 1}/{max_retries})"
                        )
                        try:
                            # Try to cancel the lock using stack.cancel()
                            if hasattr(stack, 'cancel'):
                                stack.cancel()
                            else:
                                # Fallback: try to find and delete stale lock files
                                PulumiEngine._cleanup_stale_locks(stack.name)
                            time.sleep(retry_delay)
                            continue
                        except Exception as cancel_error:
                            logger.warning(f"Failed to cancel lock via API: {cancel_error}")
                            # Try manual cleanup as fallback
                            try:
                                PulumiEngine._cleanup_stale_locks(stack.name)
                                time.sleep(retry_delay)
                                continue
                            except Exception as cleanup_error:
                                logger.warning(f"Failed to cleanup locks manually: {cleanup_error}")
                                time.sleep(retry_delay)
                                continue
                    else:
                        raise RuntimeError(
                            f"Stack is locked and could not be unlocked after {max_retries} attempts. "
                            f"Error: {error_str}. "
                            f"You may need to manually cancel the lock with 'pulumi cancel' or delete the lock file."
                        )
                else:
                    # Not a lock error, re-raise
                    raise
    
    @staticmethod
    def _cleanup_stale_locks(stack_name: str):
        """
        Attempt to clean up stale lock files manually.
        This is a fallback if stack.cancel() doesn't work.
        """
        try:
            # Lock files are typically in: .pulumi/locks/organization/{project}/{stack}/
            base_dir = Path.cwd().resolve()
            state_dir_name = os.getenv("PULUMI_STATE_DIR", "pulumi-state")
            state_dir = Path(state_dir_name) if Path(state_dir_name).is_absolute() else base_dir / state_dir_name
            
            # Try to find lock directory
            lock_pattern = state_dir / ".pulumi" / "locks" / "**" / stack_name / "*.json"
            lock_files = list(Path(state_dir).glob(str(lock_pattern.relative_to(state_dir))))
            
            # Also try direct path
            if not lock_files:
                # Try organization/stack pattern
                for org_dir in (state_dir / ".pulumi" / "locks").glob("*/"):
                    for proj_dir in org_dir.glob("*/"):
                        lock_dir = proj_dir / stack_name
                        if lock_dir.exists():
                            lock_files = list(lock_dir.glob("*.json"))
                            break
                    if lock_files:
                        break
            
            # Check if lock files are stale (older than 5 minutes)
            import time
            current_time = time.time()
            stale_threshold = 300  # 5 minutes
            
            for lock_file in lock_files:
                try:
                    # Read lock file to check timestamp
                    with open(lock_file, 'r') as f:
                        lock_data = json.load(f)
                        lock_time_str = lock_data.get('time', '')
                        if lock_time_str:
                            # Parse ISO format timestamp
                            from datetime import datetime
                            lock_time = datetime.fromisoformat(lock_time_str.replace('Z', '+00:00'))
                            lock_timestamp = lock_time.timestamp()
                            
                            # Check if stale
                            if current_time - lock_timestamp > stale_threshold:
                                logger.info(f"Removing stale lock file: {lock_file}")
                                lock_file.unlink()
                            else:
                                logger.warning(f"Lock file is recent, not removing: {lock_file}")
                except Exception as e:
                    logger.warning(f"Could not process lock file {lock_file}: {e}")
        except Exception as e:
            logger.warning(f"Could not cleanup stale locks: {e}")
    
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

        # Use lock handling wrapper for the preview operation
        def _do_preview():
            return stack.preview(on_output=print)
        
        res = PulumiEngine._handle_stack_lock(stack, "preview", _do_preview)
        
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

        # Use lock handling wrapper for the up operation
        def _do_up():
            return stack.up(on_output=print)
        
        try:
            up_res = PulumiEngine._handle_stack_lock(stack, "up", _do_up)
            
            return {
                "preview": False,
                "outputs": {k: v.value for k, v in (up_res.outputs or {}).items()},
                "summary": {
                    "resources": (up_res.summary.resource_changes if up_res.summary else None),
                    "duration_sec": (up_res.summary.duration_seconds if up_res.summary and hasattr(up_res.summary, 'duration_seconds') else None),
                },
            }
        except Exception as e:
            error_msg = str(e)
            
            # Check for 409 conflict errors (resource already exists)
            if "409" in error_msg or "already exists" in error_msg.lower() or "Database already exists" in error_msg:
                # Parse the error to identify which resource
                resource_type = "resource"
                resource_name = "unknown"
                
                if "Database already exists" in error_msg or "firestore" in error_msg.lower():
                    resource_type = "Firestore database"
                    # Try to extract database name from error
                    import re
                    match = re.search(r'database[_\s]+([a-zA-Z0-9_-]+)', error_msg, re.IGNORECASE)
                    if match:
                        resource_name = match.group(1)
                elif "bucket" in error_msg.lower() or "storage" in error_msg.lower():
                    resource_type = "Storage bucket"
                elif "secret" in error_msg.lower():
                    resource_type = "Secret Manager secret"
                
                return {
                    "preview": False,
                    "error": True,
                    "error_type": "resource_conflict",
                    "message": f"{resource_type} '{resource_name}' already exists. "
                               f"GCP resources must have unique names. "
                               f"Please use a different name or delete the existing resource first.",
                    "detailed_error": error_msg,
                    "suggestions": [
                        f"Use a different name for the {resource_type.lower()}",
                        f"Delete the existing {resource_type.lower()} if it's safe to do so",
                        f"Check if you can reuse the existing {resource_type.lower()} instead of creating a new one"
                    ]
                }
            
            # For other errors, return generic error
            return {
                "preview": False,
                "error": True,
                "message": f"Deployment failed: {error_msg}",
                "detailed_error": error_msg
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
        
        # Use lock handling wrapper for the destroy operation
        def _do_destroy():
            return stack.destroy(on_output=print)
        
        res = PulumiEngine._handle_stack_lock(stack, "destroy", _do_destroy)
        stack.workspace.remove_stack(stack.name)
        return {"destroyed": True}
