
from __future__ import annotations
import pulumi
import os
import re
from typing import Dict, Any
import pulumi_gcp as gcp
from .gcp_fabric import GcpFabric

def _generate_project_id(project_name: str, env: str) -> str:
    """
    Generate a GCP project ID from project name and environment.
    GCP project IDs must be globally unique, lowercase, alphanumeric + hyphens.
    """
    # Combine project name and env, make lowercase, replace invalid chars
    combined = f"{project_name}-{env}"
    # Remove invalid characters, keep only alphanumeric and hyphens
    project_id = re.sub(r'[^a-z0-9-]', '-', combined.lower())
    # Remove consecutive hyphens
    project_id = re.sub(r'-+', '-', project_id)
    # Remove leading/trailing hyphens
    project_id = project_id.strip('-')
    # Limit to 30 chars (GCP project ID max length)
    project_id = project_id[:30]
    return project_id

def build_pulumi_program(ir: Dict[str, Any]):
    """
    Build a Pulumi program function that creates GCP resources from IR.
    Creates a GCP project (like Azure resource group) if it doesn't exist.
    
    Args:
        ir: Intermediate Representation dictionary with nodes and edges
        
    Returns:
        Callable: Pulumi program function
    """
    project_name = ir.get("project", "canvas")
    env = ir.get("env", "dev")
    region = ir.get("location") or ir.get("region") or "us-central1"

    def program():
        # Set region early to avoid Compute Engine API validation warning
        # This warning is harmless - we don't need Compute Engine API for Storage/PubSub/CloudRun
        gcp.config.region = region
        
        # Check if we should use existing project from creds or create new one
        existing_project_id = os.getenv("GOOGLE_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
        
        # If project ID is provided in creds, use it (existing project)
        # Otherwise, create/use project based on IR project name
        if existing_project_id:
            # Use existing project from credentials
            project_id = existing_project_id
            pulumi.log.info(f"Using existing GCP project: {project_id}")
        else:
            # Generate project ID from IR project name and env (like Azure resource group)
            project_id = _generate_project_id(project_name, env)
            
            # Try to get organization ID from environment (optional)
            org_id = os.getenv("GOOGLE_ORG_ID")
            
            # Create GCP project (similar to Azure resource group creation)
            # Note: This requires organization-level permissions
            if org_id:
                gcp_project = gcp.organizations.Project(
                    f"project-{project_name}-{env}",
                    project_id=project_id,
                    name=f"{project_name}-{env}",
                    org_id=org_id,
                    labels={
                        "environment": env,
                        "managed-by": "gcp-infra-composer"
                    }
                )
                # Use the created project ID
                project_id = gcp_project.project_id
                pulumi.log.info(f"Created new GCP project: {project_id}")
            else:
                # If no org_id, assume project exists or will be created manually
                # Use the generated project ID
                pulumi.log.warn(
                    f"GOOGLE_ORG_ID not set. Assuming project '{project_id}' exists. "
                    "If it doesn't exist, create it manually or set GOOGLE_ORG_ID."
                )
        
        # Enable required APIs for the resources we're creating
        # This ensures APIs are enabled before we try to create resources
        # Note: Cloud Resource Manager API must be enabled first to enable other APIs
        required_apis = [
            "cloudresourcemanager.googleapis.com",  # Must be first - needed to enable other APIs
            "storage.googleapis.com",                # For Storage Buckets
            "pubsub.googleapis.com",                 # For Pub/Sub Topics
            "run.googleapis.com",                    # For Cloud Run Services
        ]
        
        # Enable APIs (this is idempotent - safe to call multiple times)
        # Cloud Resource Manager will be enabled first, then others can be enabled
        api_services = []
        for api in required_apis:
            api_service = gcp.projects.Service(
                f"enable-{api.replace('.', '-')}",
                project=project_id,
                service=api,
                disable_on_destroy=False,
            )
            api_services.append(api_service)
        
        pulumi.log.info(f"Enabling required APIs for project {project_id}")
        
        # Get project number for Storage service account (needed for Storage → Pub/Sub IAM)
        # This is done here so it's available to the fabric
        # get_project returns a list of projects - we need the first one
        project_data = gcp.projects.get_project(filter=f"projectId:{project_id}")
        # Access the first project from the projects list, then get its number attribute
        # Handle both Output and direct list access
        if isinstance(project_data.projects, list):
            # Already a list, access directly
            proj_num = project_data.projects[0].get('number') if project_data.projects and len(project_data.projects) > 0 else None
            project_number = pulumi.Output.from_input(proj_num) if proj_num else None
        else:
            # It's an Output, use apply
            project_number = project_data.projects.apply(
                lambda projs: projs[0].get('number') if projs and len(projs) > 0 and isinstance(projs[0], dict)
                else (projs[0].number if projs and len(projs) > 0 else None)
            )
        
        # Create fabric and apply IR (all resources will belong to this project)
        # Pass project_number to fabric so it can construct Storage service account email
        fabric = GcpFabric(project_id=project_id, region=region, project_number=project_number)
        fabric.apply_ir(ir)
        
        # Export outputs
        pulumi.export("projectId", project_id)
        pulumi.export("projectName", project_name)
        pulumi.export("region", region)
        pulumi.export("fabricOutputs", fabric.outputs())

    return program
