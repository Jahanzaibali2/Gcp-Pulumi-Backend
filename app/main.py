
from __future__ import annotations

import os, shutil
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from app.models import PreviewRequest, UpRequest, DestroyRequest, GcpCreds
from app.services.utils import get_allowed_origins
from app.services.pulumi_engine import PulumiEngine

load_dotenv()

app = FastAPI(title="GCP IR → Pulumi Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _export_gcp_creds(creds: Optional[GcpCreds]):
    """
    Export GCP credentials to environment variables.
    Writes service account JSON to a temporary file and sets GOOGLE_APPLICATION_CREDENTIALS.
    """
    if not creds:
        return
    
    # Parse and validate JSON
    import json
    import tempfile
    
    try:
        sa_data = json.loads(creds.serviceAccountJson)
    except json.JSONDecodeError:
        raise ValueError("Invalid serviceAccountJson: must be valid JSON")
    
    # Write to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(sa_data, f)
        creds_path = f.name
    
    # Set environment variables
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
    os.environ["GOOGLE_REGION"] = creds.region
    
    # Only set project ID if provided (optional - project can be created from IR)
    if creds.projectId:
        os.environ["GOOGLE_PROJECT"] = creds.projectId
        os.environ["GOOGLE_CLOUD_PROJECT"] = creds.projectId
    
    # Set organization ID if provided (needed for project creation)
    if creds.orgId:
        os.environ["GOOGLE_ORG_ID"] = creds.orgId

@app.get("/health")
def health():
    return {
        "status": "ok",
        "pulumiOnPath": bool(shutil.which("pulumi")),
        "project": os.environ.get("GOOGLE_PROJECT"),
        "region": os.environ.get("GOOGLE_REGION"),
    }

@app.post("/preview")
def preview(req: PreviewRequest):
    try:
        _export_gcp_creds(req.creds)
        return PulumiEngine.preview(req.ir.model_dump())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/up")
def up(req: UpRequest):
    try:
        _export_gcp_creds(req.creds)
        return PulumiEngine.up(req.ir.model_dump())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/destroy")
def destroy(req: DestroyRequest):
    try:
        _export_gcp_creds(req.creds)
        return PulumiEngine.destroy(req.project, req.env)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
