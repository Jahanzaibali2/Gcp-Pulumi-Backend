
from __future__ import annotations
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

# ---------- IR schema ----------
class Node(BaseModel):
    id: str
    kind: str
    name: Optional[str] = None
    props: Dict[str, Any] = Field(default_factory=dict)

class Edge(BaseModel):
    from_: str = Field(alias="from")
    to: str
    intent: str = "notify"

class IR(BaseModel):
    project: str
    env: str
    location: Optional[str] = None   # synonym for region
    region: Optional[str] = None
    nodes: List[Node] = Field(default_factory=list)
    edges: List[Edge] = Field(default_factory=list)

# ---------- GCP creds ----------
class GcpCreds(BaseModel):
    projectId: Optional[str] = None  # Optional: if not provided, project will be created from IR
    region: str
    serviceAccountJson: str  # full JSON string
    orgId: Optional[str] = None  # Optional: required only if creating new projects

# ---------- Requests ----------
class PreviewRequest(BaseModel):
    ir: IR
    creds: Optional[GcpCreds] = None

class UpRequest(BaseModel):
    ir: IR
    creds: Optional[GcpCreds] = None

class DestroyRequest(BaseModel):
    project: str
    env: str
    creds: Optional[GcpCreds] = None
