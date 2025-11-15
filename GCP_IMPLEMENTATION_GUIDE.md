# GCP Infrastructure Composer - Implementation Guide

This document provides a complete technical guide for implementing a GCP version of the Azure Infrastructure Composer system. This guide focuses on **3 core services** (Storage, Pub/Sub, Cloud Run) with a scalable architecture that makes it easy to add more services in the future.

---

## 📋 Table of Contents

1. [System Overview](#system-overview)
2. [Supported Services (3 Core)](#supported-services-3-core)
3. [Architecture](#architecture)
4. [Key Design Patterns](#key-design-patterns)
5. [Component Breakdown](#component-breakdown)
6. [IR Format Specification](#ir-format-specification)
7. [Service Registry Pattern](#service-registry-pattern)
8. [Edge Connections Implementation](#edge-connections-implementation)
9. [Complete Code Implementation](#complete-code-implementation)
10. [API Endpoints](#api-endpoints)
11. [Testing Strategy](#testing-strategy)
12. [Adding More Services (Scalability)](#adding-more-services-scalability)

---

## 🎯 System Overview

### Supported Services (3 Core)

The initial implementation focuses on **3 core GCP services**:

1. **`gcp.storage`** - Google Cloud Storage (GCS Bucket)
2. **`gcp.pubsub`** - Cloud Pub/Sub (Topic)
3. **`gcp.run`** - Cloud Run (Serverless Container Service)

These services form a complete event-driven pipeline:
- **Storage** → **Pub/Sub** → **Cloud Run**

### Example Payload

```json
{
  "ir": {
    "project": "canvas-project",
    "env": "dev",
    "location": "us-central1",
    "nodes": [
      { 
        "id": "gcs-1", 
        "kind": "gcp.storage", 
        "name": "ingress-bucket", 
        "props": { "uniformAccess": true } 
      },
      { 
        "id": "ps-1", 
        "kind": "gcp.pubsub", 
        "name": "ingress-topic", 
        "props": { "topicName": "ingress-topic" } 
      },
      { 
        "id": "run-1", 
        "kind": "gcp.run", 
        "name": "worker",
        "props": { 
          "image": "gcr.io/cloudrun/hello", 
          "env": { "LOG_LEVEL": "info" }, 
          "allowUnauthenticated": true 
        } 
      }
    ],
    "edges": [
      { "from": "gcs-1", "to": "ps-1", "intent": "notify" },
      { "from": "ps-1", "to": "run-1", "intent": "notify" }
    ]
  },
  "creds": {
    "projectId": "YOUR_PROJECT",
    "region": "us-central1",
    "serviceAccountJson": "{ ... full service account JSON ... }"
  }
}
```

### Scalability

The architecture is designed to be **easily extensible**. To add more services:

1. Create `_create_<service>` method in `GCPFabric`
2. Add entry to `ServiceRegistry`
3. Implement edge connections if needed
4. That's it! (See [Adding More Services](#adding-more-services-scalability) section)

---

## 🎯 System Overview

### What This System Does

The Infrastructure Composer is a **graph-based infrastructure provisioning system** that:

1. **Accepts a graph definition** (nodes = resources, edges = connections) via REST API
2. **Converts it to cloud resources** using Pulumi
3. **Automatically configures connections** between resources
4. **Provides preview, deploy, and destroy** operations

### Core Concepts

- **Intermediate Representation (IR)**: A JSON format that defines infrastructure as a graph
- **Nodes**: Represent cloud resources (e.g., Storage, Compute, Databases)
- **Edges**: Represent connections/relationships between resources
- **Registry Pattern**: Maps service kinds to creation methods for extensibility
- **Edge Connections**: Automatically configures integrations between services

---

## 🏗️ Architecture

### High-Level Flow

```
┌─────────────┐
│   Client    │
│  (Frontend) │
└──────┬──────┘
       │ HTTP POST /up
       ▼
┌─────────────────┐
│  FastAPI Server │
│   (main.py)     │
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│  PulumiEngine   │
│ (pulumi_engine) │
│  - preview()    │
│  - up()         │
│  - destroy()    │
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│ Program Builder │
│(program_builder) │
│  - build_program()│
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│  GCP Fabric     │
│ (gcp_fabric.py) │
│  - apply_ir()   │
│  - _create_*() │
│  - _connect()   │
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│ Pulumi GCP      │
│     Native      │
└─────────────────┘
       │
       ▼
┌─────────────────┐
│  Google Cloud   │
└─────────────────┘
```

### Key Components

1. **FastAPI Server** (`app/main.py`)
   - Handles HTTP requests
   - Exports cloud credentials to environment
   - Routes to PulumiEngine

2. **PulumiEngine** (`app/services/pulumi_engine.py`)
   - Manages Pulumi stack lifecycle
   - Handles preview, up, destroy operations
   - Falls back to direct cloud API for destroy if needed

3. **Program Builder** (`app/services/program_builder.py`)
   - Constructs Pulumi program function
   - Creates resource group/project
   - Instantiates Fabric and applies IR

4. **GCP Fabric** (`app/services/gcp_fabric.py`)
   - Creates GCP resources
   - Implements edge connections
   - Manages outputs/exports

5. **Service Registry** (`app/services/service_registry.py`)
   - Maps service kinds to creation methods
   - Provides extensibility

6. **Naming Service** (`app/services/naming.py`)
   - Sanitizes resource names for cloud requirements

---

## 🔑 Key Design Patterns

### 1. Registry Pattern

**Purpose**: Map service kinds (e.g., `gcp.storage`) to creation methods dynamically.

**Implementation**:
```python
# app/services/service_registry.py
class ServiceRegistry:
    def __init__(self, fabric_instance):
        self.fabric = fabric_instance
        self._service_registry: Dict[str, Callable] = {
            "gcp.storage": self.fabric._create_storage,
            "gcp.cloudfunctions": self.fabric._create_cloud_function,
            "gcp.cloudrun": self.fabric._create_cloud_run,
            # ... more services
        }
    
    def get_creator(self, kind: str) -> Callable:
        creator = self._service_registry.get(kind)
        if not creator:
            supported = ", ".join(self._service_registry.keys())
            raise ValueError(f"Unsupported kind: {kind}. Supported: {supported}")
        return creator
```

**Usage in Fabric**:
```python
# app/services/gcp_fabric.py
class GCPFabric:
    def __init__(self, project_id: str, region: str):
        self._registry = ServiceRegistry(self)
        self.node_index: Dict[str, Dict] = {}
        self._outputs: Dict[str, pulumi.Output] = {}
    
    def apply_ir(self, ir: Dict[str, Any]):
        nodes = ir.get("nodes", [])
        edges = ir.get("edges", [])
        
        # Use registry instead of if/elif chain
        for n in nodes:
            kind = n.get("kind")
            creator = self._registry.get_creator(kind)
            creator(n)  # Calls _create_storage, _create_cloud_function, etc.
        
        # Process edges
        for e in edges:
            self._connect(e)
```

**Benefits**:
- ✅ Clean separation of concerns
- ✅ Easy to add new services (just add to registry)
- ✅ No massive if/elif chains
- ✅ Centralized service management

---

### 2. Node Index Pattern

**Purpose**: Store created resources in a dictionary for edge connections.

**Implementation**:
```python
class GCPFabric:
    def __init__(self, ...):
        self.node_index: Dict[str, Dict[str, Any]] = {}
        # Format: {node_id: {"kind": "gcp.storage", "bucket": <resource>, ...}}
    
    def _create_storage(self, node: Dict[str, Any]):
        bucket = storage.Bucket(...)
        self.node_index[node["id"]] = {
            "kind": "gcp.storage",
            "bucket": bucket,
            "name": bucket.name,
        }
    
    def _connect(self, edge: Dict[str, Any]):
        from_id = edge.get("from")
        to_id = edge.get("to")
        
        src = self.node_index.get(from_id)  # Source resource
        dst = self.node_index.get(to_id)    # Destination resource
        
        # Now you can access src["bucket"], dst["function"], etc.
```

---

### 3. Output Export Pattern

**Purpose**: Export connection strings, endpoints, and IDs for other resources to use.

**Implementation**:
```python
class GCPFabric:
    def __init__(self, ...):
        self._outputs: Dict[str, pulumi.Output] = {}
    
    def _create_storage(self, node: Dict[str, Any]):
        bucket = storage.Bucket(...)
        # Export connection info
        self._outputs[f"storage-{name}-bucketName"] = bucket.name
        self._outputs[f"storage-{name}-url"] = bucket.url
    
    def outputs(self) -> Dict[str, pulumi.Output]:
        return self._outputs
```

**Usage in Program Builder**:
```python
def build_pulumi_program(ir: Dict[str, Any]):
    def program():
        project = gcp.organizations.Project(...)
        fabric = GCPFabric(project_id=project.project_id, region=region)
        fabric.apply_ir(ir)
        pulumi.export("fabricOutputs", fabric.outputs())
    return program
```

---

### 4. Edge Connection Pattern

**Purpose**: Automatically configure integrations between resources.

**Implementation**:
```python
def _connect(self, edge: Dict[str, Any]):
    from_id = edge.get("from")
    to_id = edge.get("to")
    intent = edge.get("intent", "notify")
    
    src = self.node_index.get(from_id)
    dst = self.node_index.get(to_id)
    
    src_kind = src.get("kind")
    dst_kind = dst.get("kind")
    
    # Storage → Cloud Function (Pub/Sub trigger)
    if src_kind == "gcp.storage" and dst_kind == "gcp.cloudfunctions":
        bucket = src["bucket"]
        function = dst["function"]
        
        # Create Pub/Sub topic for storage events
        topic = pubsub.Topic(...)
        
        # Create notification for bucket
        notification = storage.Notification(
            bucket=bucket.name,
            topic=topic.name,
            event_types=["OBJECT_FINALIZE"],
        )
        
        # Export topic name for function to subscribe
        pulumi.export(f"bind-{to_id}-topic", topic.name)
    
    # Cloud Function → Cloud Run (HTTP call)
    elif src_kind == "gcp.cloudfunctions" and dst_kind == "gcp.cloudrun":
        function_url = src["function"].https_trigger_url
        pulumi.export(f"bind-{to_id}-function-url", function_url)
    
    # ... more connection patterns
```

---

## 📦 Component Breakdown

### 1. Models (`app/models.py`)

**Purpose**: Define Pydantic models for API requests and IR format.

```python
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

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
    location: Optional[str] = None  # For GCP, use "region" or "location"
    region: Optional[str] = None
    nodes: List[Node] = Field(default_factory=list)
    edges: List[Edge] = Field(default_factory=list)

class GCPCreds(BaseModel):
    projectId: str
    region: str
    serviceAccountJson: str  # Full service account JSON as string

class PreviewRequest(BaseModel):
    ir: IR
    creds: Optional[GCPCreds] = None

class UpRequest(BaseModel):
    ir: IR
    creds: Optional[GCPCreds] = None

class DestroyRequest(BaseModel):
    project: str
    env: str
    creds: Optional[GCPCreds] = None
```

---

### 2. Main API (`app/main.py`)

**Purpose**: FastAPI endpoints for preview, up, destroy.

```python
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.models import IR, UpRequest, PreviewRequest, DestroyRequest, GCPCreds
from app.services.pulumi_engine import PulumiEngine
import os

app = FastAPI(title="GCP IR → Pulumi Backend", version="0.1.0")

def _export_gcp_creds(creds: Optional[GCPCreds]):
    if not creds:
        return
    # Write service account JSON to temp file
    import tempfile
    import json
    
    # Parse and validate JSON
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
    os.environ["GOOGLE_PROJECT"] = creds.projectId
    os.environ["GOOGLE_REGION"] = creds.region

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
        creds_dict = req.creds.model_dump() if req.creds else None
        return PulumiEngine.destroy(req.project, req.env, creds_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
```

---

### 3. Pulumi Engine (`app/services/pulumi_engine.py`)

**Purpose**: Orchestrate Pulumi stack operations.

```python
from pulumi import automation as auto
from .program_builder import build_pulumi_program
import os

class PulumiEngine:
    @staticmethod
    def _set_config(stack, ir: Dict[str, Any]):
        region = ir.get("region") or ir.get("location") or "us-central1"
        stack.set_config("gcp:region", auto.ConfigValue(value=region))
        stack.set_config("gcp:project", auto.ConfigValue(value=os.getenv("GOOGLE_PROJECT")))
    
    @staticmethod
    def preview(ir: Dict[str, Any]):
        project = ir.get("project", "canvas")
        env_name = ir.get("env", "dev")
        program = build_pulumi_program(ir)
        stack = auto.create_or_select_stack(
            stack_name=f"{project}-{env_name}",
            project_name=project,
            program=program,
            work_dir="pulumi-work",
        )
        PulumiEngine._set_config(stack, ir)
        res = stack.preview(on_output=print)
        return {"preview": True, "changeSummary": res.change_summary}
    
    @staticmethod
    def up(ir: Dict[str, Any]):
        # Similar to preview, but calls stack.up()
        # Returns outputs and summary
        pass
    
    @staticmethod
    def destroy(project: str, env: str, creds: Optional[Dict]):
        # Try Pulumi destroy first
        # Fall back to direct GCP API if needed
        pass
```

---

### 4. Program Builder (`app/services/program_builder.py`)

**Purpose**: Construct the Pulumi program function.

```python
import pulumi
from pulumi_gcp import organizations
from .gcp_fabric import GCPFabric

def build_pulumi_program(ir: Dict[str, Any]):
    project = ir.get("project", "canvas")
    env = ir.get("env", "dev")
    region = ir.get("region") or ir.get("location") or "us-central1"
    
    def program():
        # Get or create GCP project
        # For simplicity, assume project exists or use existing project ID
        project_id = os.getenv("GOOGLE_PROJECT")
        
        # Create fabric and apply IR
        fabric = GCPFabric(project_id=project_id, region=region)
        fabric.apply_ir(ir)
        
        # Export outputs
        pulumi.export("projectId", project_id)
        pulumi.export("region", region)
        pulumi.export("fabricOutputs", fabric.outputs())
    
    return program
```

---

### 5. GCP Fabric (`app/services/gcp_fabric.py`)

**Purpose**: Create GCP resources and handle edge connections.

**Structure**:
```python
from pulumi_gcp import storage, cloudfunctions, cloudrun, sql, pubsub, ...
from .service_registry import ServiceRegistry
from .naming import safe_name

class GCPFabric:
    def __init__(self, project_id: str, region: str):
        self.project_id = project_id
        self.region = region
        self.node_index: Dict[str, Dict] = {}
        self._outputs: Dict[str, pulumi.Output] = {}
        self._registry = ServiceRegistry(self)
    
    def apply_ir(self, ir: Dict[str, Any]):
        # Create nodes
        for n in ir.get("nodes", []):
            creator = self._registry.get_creator(n.get("kind"))
            creator(n)
        
        # Create edges
        for e in ir.get("edges", []):
            self._connect(e)
    
    def outputs(self) -> Dict[str, pulumi.Output]:
        return self._outputs
    
    # Resource creation methods
    def _create_storage(self, node: Dict[str, Any]):
        # Create GCS bucket
        pass
    
    def _create_cloud_function(self, node: Dict[str, Any]):
        # Create Cloud Function
        pass
    
    # ... more _create_* methods
    
    # Edge connections
    def _connect(self, edge: Dict[str, Any]):
        # Implement connection logic
        pass
```

---

## 📝 IR Format Specification

### Complete IR Example

```json
{
  "project": "my-project",
  "env": "dev",
  "region": "us-central1",
  "nodes": [
    {
      "id": "storage-1",
      "kind": "gcp.storage",
      "name": "my-bucket",
      "props": {
        "location": "US",
        "storageClass": "STANDARD"
      }
    },
    {
      "id": "function-1",
      "kind": "gcp.cloudfunctions",
      "name": "my-function",
      "props": {
        "runtime": "python39",
        "entryPoint": "hello_world"
      }
    }
  ],
  "edges": [
    {
      "from": "storage-1",
      "to": "function-1",
      "intent": "notify"
    }
  ]
}
```

### Node Structure

- **id**: Unique identifier within the IR (used in edges)
- **kind**: Service type (e.g., `gcp.storage`, `gcp.cloudfunctions`)
- **name**: Human-readable name (optional, defaults to id)
- **props**: Service-specific properties (varies by service)

### Edge Structure

- **from**: Source node ID
- **to**: Destination node ID
- **intent**: Connection intent (currently only `"notify"`)

---

## 🔧 Service Registry Pattern

### Implementation

**File**: `app/services/service_registry.py`

```python
from typing import Dict, Callable

class ServiceRegistry:
    def __init__(self, fabric_instance):
        self.fabric = fabric_instance
        # Only 3 core services initially - easily extensible
        self._service_registry: Dict[str, Callable] = {
            "gcp.storage": self.fabric._create_storage,
            "gcp.pubsub": self.fabric._create_pubsub,
            "gcp.run": self.fabric._create_cloud_run,
        }
    
    def get_creator(self, kind: str) -> Callable:
        creator = self._service_registry.get(kind)
        if not creator:
            supported = ", ".join(self._service_registry.keys())
            raise ValueError(f"Unsupported kind: {kind}. Supported: {supported}")
        return creator
```

**Adding a New Service** (for future scalability):
1. Create `_create_<service>` method in `GCPFabric`
2. Add entry to `_service_registry` dictionary
3. That's it! The service is now available.

**Example** (for future addition):
```python
# In ServiceRegistry.__init__:
"gcp.cloudfunctions": self.fabric._create_cloud_function,  # Add this line

# In GCPFabric:
def _create_cloud_function(self, node: Dict[str, Any]):
    # Implementation here
    pass
```

---

## 🔗 Edge Connections Implementation

### Supported Edge Connections (3 Services)

For the 3 core services, we implement these edge connections:

1. **Storage → Pub/Sub**: Storage notifications trigger Pub/Sub messages
2. **Pub/Sub → Cloud Run**: Pub/Sub messages trigger Cloud Run service

### Complete Implementation

```python
def _connect(self, edge: Dict[str, Any]):
    from_id = edge.get("from") or edge.get("from_")
    to_id = edge.get("to")
    intent = edge.get("intent", "notify")
    
    if not from_id or not to_id:
        pulumi.log.warn(f"Edge missing endpoints: {edge}; skipping")
        return
    
    src = self.node_index.get(from_id)
    dst = self.node_index.get(to_id)
    
    if not src or not dst:
        pulumi.log.warn(f"Edge references unknown node: {edge}; skipping")
        return
    
    src_kind = src.get("kind")
    dst_kind = dst.get("kind")
    
    # Connection 1: Storage → Pub/Sub
    # When a file is uploaded to Storage, send event to Pub/Sub topic
    if src_kind == "gcp.storage" and dst_kind == "gcp.pubsub":
        bucket = src["bucket"]
        topic = dst["topic"]
        
        # Create storage notification that publishes to Pub/Sub
        notification = storage.Notification(
            f"notif-{safe_name(from_id)}-{safe_name(to_id)}",
            bucket=bucket.name,
            topic=topic.id,
            event_types=["OBJECT_FINALIZE"],  # Trigger on file creation
            payload_format="JSON_API_V1",
        )
        
        # Export topic name for reference
        self._outputs[f"bind-{to_id}-topic"] = topic.name
        pulumi.log.info(f"Connected Storage '{from_id}' → Pub/Sub '{to_id}' via notification")
    
    # Connection 2: Pub/Sub → Cloud Run
    # When message arrives in Pub/Sub, trigger Cloud Run service
    elif src_kind == "gcp.pubsub" and dst_kind == "gcp.run":
        topic = src["topic"]
        service = dst["service"]
        
        # Create Pub/Sub subscription that triggers Cloud Run
        subscription = pubsub.Subscription(
            f"sub-{safe_name(from_id)}-{safe_name(to_id)}",
            name=f"sub-{safe_name(from_id)}-{safe_name(to_id)}",
            topic=topic.name,
            push_config=pubsub.SubscriptionPushConfigArgs(
                push_endpoint=service.statuses[0].url,
            ),
        )
        
        # Grant Pub/Sub permission to invoke Cloud Run
        cloudrun.IamMember(
            f"run-{safe_name(to_id)}-pubsub-invoker",
            service=service.name,
            location=service.location,
            role="roles/run.invoker",
            member=f"serviceAccount:service-{self.project_number}@gcp-sa-pubsub.iam.gserviceaccount.com",
        )
        
        # Export subscription name
        self._outputs[f"bind-{to_id}-subscription"] = subscription.name
        pulumi.log.info(f"Connected Pub/Sub '{from_id}' → Cloud Run '{to_id}' via push subscription")
    
    else:
        pulumi.log.warn(
            f"Unsupported edge connection: {src_kind} → {dst_kind}. "
            f"Supported: gcp.storage → gcp.pubsub, gcp.pubsub → gcp.run"
        )
```

**Note**: For the Pub/Sub → Cloud Run connection, you'll need the project number. Add this to `GCPFabric.__init__`:

```python
def __init__(self, project_id: str, region: str):
    self.project_id = project_id
    self.region = region
    # Get project number (you may need to fetch this)
    self.project_number = self._get_project_number(project_id)
    # ... rest of init
```

---

## 🗺️ GCP Service Implementations (3 Core Services)

### 1. Storage (GCS Bucket) - `gcp.storage`

**Purpose**: Store files/blobs in Google Cloud Storage.

**Props**:
- `uniformAccess` (bool): Enable uniform bucket-level access (default: `true`)

**Implementation**:
```python
def _create_storage(self, node: Dict[str, Any]):
    name = safe_name(node.get("name") or node["id"])
    props = node.get("props", {})
    
    # GCS bucket names must be globally unique
    bucket_name = f"{name}-{self.project_id}"
    
    bucket = storage.Bucket(
        f"bucket-{name}",
        name=bucket_name,
        location=self.region.upper(),  # e.g., "US", "EU", "ASIA"
        storage_class="STANDARD",
        uniform_bucket_level_access=props.get("uniformAccess", True),
    )
    
    self.node_index[node["id"]] = {
        "kind": "gcp.storage",
        "bucket": bucket,
        "name": bucket.name,
    }
    
    self._outputs[f"storage-{name}-bucketName"] = bucket.name
    self._outputs[f"storage-{name}-url"] = bucket.url
```

### 2. Pub/Sub (Topic) - `gcp.pubsub`

**Purpose**: Message queue/topic for event-driven architectures.

**Props**:
- `topicName` (str): Name of the Pub/Sub topic

**Implementation**:
```python
def _create_pubsub(self, node: Dict[str, Any]):
    name = safe_name(node.get("name") or node["id"])
    props = node.get("props", {})
    
    topic_name = props.get("topicName") or name
    
    topic = pubsub.Topic(
        f"topic-{name}",
        name=topic_name,
    )
    
    self.node_index[node["id"]] = {
        "kind": "gcp.pubsub",
        "topic": topic,
        "name": topic.name,
    }
    
    self._outputs[f"pubsub-{name}-topicName"] = topic.name
    self._outputs[f"pubsub-{name}-topicId"] = topic.id
```

### 3. Cloud Run (Service) - `gcp.run`

**Purpose**: Serverless container service.

**Props**:
- `image` (str): Container image (e.g., `"gcr.io/cloudrun/hello"`)
- `env` (dict): Environment variables
- `allowUnauthenticated` (bool): Allow unauthenticated invocations (default: `true`)
- `cpu` (str): CPU allocation (default: `"1000m"`)
- `memory` (str): Memory allocation (default: `"512Mi"`)

**Implementation**:
```python
def _create_cloud_run(self, node: Dict[str, Any]):
    name = safe_name(node.get("name") or node["id"])
    props = node.get("props", {})
    
    # Build environment variables
    env_vars = []
    env_dict = props.get("env", {})
    for key, value in env_dict.items():
        env_vars.append(cloudrun.ServiceTemplateSpecContainerEnvArgs(
            name=key,
            value=str(value),
        ))
    
    service = cloudrun.Service(
        f"run-{name}",
        name=name,
        location=self.region,
        template=cloudrun.ServiceTemplateArgs(
            spec=cloudrun.ServiceTemplateSpecArgs(
                containers=[
                    cloudrun.ServiceTemplateSpecContainerArgs(
                        image=props.get("image", "gcr.io/cloudrun/hello"),
                        envs=env_vars,
                        resources=cloudrun.ServiceTemplateSpecContainerResourcesArgs(
                            limits={
                                "cpu": props.get("cpu", "1000m"),
                                "memory": props.get("memory", "512Mi"),
                            },
                        ),
                    )
                ],
            ),
        ),
    )
    
    # Allow unauthenticated access if specified
    if props.get("allowUnauthenticated", True):
        cloudrun.IamMember(
            f"run-{name}-public",
            service=service.name,
            location=service.location,
            role="roles/run.invoker",
            member="allUsers",
        )
    
    self.node_index[node["id"]] = {
        "kind": "gcp.run",
        "service": service,
        "name": service.name,
    }
    
    self._outputs[f"cloudrun-{name}-url"] = service.statuses[0].url
    self._outputs[f"cloudrun-{name}-name"] = service.name
```

---

## 🚀 Implementation Steps

### Step 1: Project Setup

1. **Create project structure**:
   ```
   gcp_infra_composer/
   ├── app/
   │   ├── __init__.py
   │   ├── main.py
   │   ├── models.py
   │   └── services/
   │       ├── __init__.py
   │       ├── gcp_fabric.py
   │       ├── service_registry.py
   │       ├── program_builder.py
   │       ├── pulumi_engine.py
   │       ├── naming.py
   │       └── utils.py
   ├── pulumi-work/
   ├── venv/
   └── requirements.txt
   ```

2. **Install dependencies**:
   ```bash
   pip install fastapi uvicorn python-dotenv pulumi pulumi-gcp pydantic requests
   ```

### Step 2: Implement Core Components

1. **Models** (`app/models.py`): Copy from Azure version, change `AzureCreds` to `GCPCreds`
2. **Main API** (`app/main.py`): Copy and adapt credential export
3. **Pulumi Engine** (`app/services/pulumi_engine.py`): Copy and change config keys
4. **Program Builder** (`app/services/program_builder.py`): Adapt for GCP project
5. **Naming** (`app/services/naming.py`): Copy as-is (generic)

### Step 3: Implement GCP Fabric

1. **Create `GCPFabric` class** with `__init__`, `apply_ir`, `outputs` methods
2. **Implement Service Registry** with GCP service kinds
3. **Implement `_create_*` methods** for each service (start with 3-4 core services)
4. **Implement `_connect` method** with edge connection logic

### Step 4: Test Gradually

1. **Start with 1 service** (e.g., Storage)
2. **Add 2-3 services** and test
3. **Add edge connections** one by one
4. **Expand to all services**

### Step 5: Implement Edge Connections

1. **Start with simple connections** (Storage → Pub/Sub → Function)
2. **Add HTTP connections** (Function ↔ Cloud Run)
3. **Add connection exports** (connection strings, endpoints)
4. **Test bidirectional connections**

---

## 💻 Complete Code Implementation

### Complete GCP Fabric (`app/services/gcp_fabric.py`)

```python
# app/services/gcp_fabric.py
from typing import Dict, Any
import pulumi
from pulumi_gcp import storage, pubsub, cloudrun
from .service_registry import ServiceRegistry
from .naming import safe_name

class GCPFabric:
    def __init__(self, project_id: str, region: str):
        self.project_id = project_id
        self.region = region
        self.node_index: Dict[str, Dict[str, Any]] = {}
        self._outputs: Dict[str, pulumi.Output[Any]] = {}
        self._registry = ServiceRegistry(self)
    
    def outputs(self) -> Dict[str, pulumi.Output[Any]]:
        return self._outputs
    
    def apply_ir(self, ir: Dict[str, Any]):
        nodes = ir.get("nodes", [])
        edges = ir.get("edges", [])
        
        # Create nodes using registry
        for n in nodes:
            kind = n.get("kind")
            try:
                creator = self._registry.get_creator(kind)
                creator(n)
            except ValueError as e:
                raise ValueError(str(e))
        
        # Create edges
        for e in edges:
            self._connect(e)
    
    def _create_storage(self, node: Dict[str, Any]):
        """Create GCS Bucket"""
        name = safe_name(node.get("name") or node["id"])
        props = node.get("props", {})
        
        bucket_name = f"{name}-{self.project_id}"
        
        bucket = storage.Bucket(
            f"bucket-{name}",
            name=bucket_name,
            location=self.region.upper(),
            storage_class="STANDARD",
            uniform_bucket_level_access=props.get("uniformAccess", True),
        )
        
        self.node_index[node["id"]] = {
            "kind": "gcp.storage",
            "bucket": bucket,
            "name": bucket.name,
        }
        
        self._outputs[f"storage-{name}-bucketName"] = bucket.name
        self._outputs[f"storage-{name}-url"] = bucket.url
    
    def _create_pubsub(self, node: Dict[str, Any]):
        """Create Pub/Sub Topic"""
        name = safe_name(node.get("name") or node["id"])
        props = node.get("props", {})
        
        topic_name = props.get("topicName") or name
        
        topic = pubsub.Topic(
            f"topic-{name}",
            name=topic_name,
        )
        
        self.node_index[node["id"]] = {
            "kind": "gcp.pubsub",
            "topic": topic,
            "name": topic.name,
        }
        
        self._outputs[f"pubsub-{name}-topicName"] = topic.name
        self._outputs[f"pubsub-{name}-topicId"] = topic.id
    
    def _create_cloud_run(self, node: Dict[str, Any]):
        """Create Cloud Run Service"""
        name = safe_name(node.get("name") or node["id"])
        props = node.get("props", {})
        
        # Build environment variables
        env_vars = []
        env_dict = props.get("env", {})
        for key, value in env_dict.items():
            env_vars.append(cloudrun.ServiceTemplateSpecContainerEnvArgs(
                name=key,
                value=str(value),
            ))
        
        service = cloudrun.Service(
            f"run-{name}",
            name=name,
            location=self.region,
            template=cloudrun.ServiceTemplateArgs(
                spec=cloudrun.ServiceTemplateSpecArgs(
                    containers=[
                        cloudrun.ServiceTemplateSpecContainerArgs(
                            image=props.get("image", "gcr.io/cloudrun/hello"),
                            envs=env_vars,
                            resources=cloudrun.ServiceTemplateSpecContainerResourcesArgs(
                                limits={
                                    "cpu": props.get("cpu", "1000m"),
                                    "memory": props.get("memory", "512Mi"),
                                },
                            ),
                        )
                    ],
                ),
            ),
        )
        
        # Allow unauthenticated access if specified
        if props.get("allowUnauthenticated", True):
            cloudrun.IamMember(
                f"run-{name}-public",
                service=service.name,
                location=service.location,
                role="roles/run.invoker",
                member="allUsers",
            )
        
        self.node_index[node["id"]] = {
            "kind": "gcp.run",
            "service": service,
            "name": service.name,
        }
        
        self._outputs[f"cloudrun-{name}-url"] = service.statuses[0].url
        self._outputs[f"cloudrun-{name}-name"] = service.name
    
    def _connect(self, edge: Dict[str, Any]):
        """Connect resources via edges"""
        from_id = edge.get("from") or edge.get("from_")
        to_id = edge.get("to")
        intent = edge.get("intent", "notify")
        
        if not from_id or not to_id:
            pulumi.log.warn(f"Edge missing endpoints: {edge}; skipping")
            return
        
        src = self.node_index.get(from_id)
        dst = self.node_index.get(to_id)
        
        if not src or not dst:
            pulumi.log.warn(f"Edge references unknown node: {edge}; skipping")
            return
        
        src_kind = src.get("kind")
        dst_kind = dst.get("kind")
        
        # Storage → Pub/Sub
        if src_kind == "gcp.storage" and dst_kind == "gcp.pubsub":
            bucket = src["bucket"]
            topic = dst["topic"]
            
            notification = storage.Notification(
                f"notif-{safe_name(from_id)}-{safe_name(to_id)}",
                bucket=bucket.name,
                topic=topic.id,
                event_types=["OBJECT_FINALIZE"],
                payload_format="JSON_API_V1",
            )
            
            self._outputs[f"bind-{to_id}-topic"] = topic.name
            pulumi.log.info(f"Connected Storage '{from_id}' → Pub/Sub '{to_id}'")
        
        # Pub/Sub → Cloud Run
        elif src_kind == "gcp.pubsub" and dst_kind == "gcp.run":
            topic = src["topic"]
            service = dst["service"]
            
            subscription = pubsub.Subscription(
                f"sub-{safe_name(from_id)}-{safe_name(to_id)}",
                name=f"sub-{safe_name(from_id)}-{safe_name(to_id)}",
                topic=topic.name,
                push_config=pubsub.SubscriptionPushConfigArgs(
                    push_endpoint=service.statuses[0].url,
                ),
            )
            
            self._outputs[f"bind-{to_id}-subscription"] = subscription.name
            pulumi.log.info(f"Connected Pub/Sub '{from_id}' → Cloud Run '{to_id}'")
        
        else:
            pulumi.log.warn(
                f"Unsupported edge: {src_kind} → {dst_kind}. "
                f"Supported: gcp.storage → gcp.pubsub, gcp.pubsub → gcp.run"
            )
```

### Service Registry (`app/services/service_registry.py`)

```python
# app/services/service_registry.py
from typing import Dict, Callable

class ServiceRegistry:
    def __init__(self, fabric_instance):
        self.fabric = fabric_instance
        # Only 3 services initially - easily extensible
        self._service_registry: Dict[str, Callable] = {
            "gcp.storage": self.fabric._create_storage,
            "gcp.pubsub": self.fabric._create_pubsub,
            "gcp.run": self.fabric._create_cloud_run,
        }
    
    def get_creator(self, kind: str) -> Callable:
        creator = self._service_registry.get(kind)
        if not creator:
            supported = ", ".join(self._service_registry.keys())
            raise ValueError(f"Unsupported kind: {kind}. Supported: {supported}")
        return creator
    
    def get_supported_kinds(self) -> list:
        return list(self._service_registry.keys())
```

### Program Builder (`app/services/program_builder.py`)

```python
# app/services/program_builder.py
import pulumi
import os
from .gcp_fabric import GCPFabric

def build_pulumi_program(ir: Dict[str, Any]):
    project = ir.get("project", "canvas")
    env = ir.get("env", "dev")
    region = ir.get("region") or ir.get("location") or "us-central1"
    
    def program():
        project_id = os.getenv("GOOGLE_PROJECT")
        if not project_id:
            raise ValueError("GOOGLE_PROJECT environment variable not set")
        
        fabric = GCPFabric(project_id=project_id, region=region)
        fabric.apply_ir(ir)
        
        pulumi.export("projectId", project_id)
        pulumi.export("region", region)
        pulumi.export("fabricOutputs", fabric.outputs())
    
    return program
```

---

## 🔌 API Endpoints

### POST /preview

Preview infrastructure changes without deploying.

**Request**:
```json
{
  "ir": {
    "project": "my-project",
    "env": "dev",
    "region": "us-central1",
    "nodes": [...],
    "edges": [...]
  },
  "creds": {
    "projectId": "my-gcp-project",
    "credentials": "/path/to/credentials.json"
  }
}
```

**Response**:
```json
{
  "preview": true,
  "changeSummary": {
    "create": 5,
    "update": 0,
    "delete": 0
  }
}
```

### POST /up

Deploy infrastructure to GCP.

**Request**: Same as `/preview`

**Response**:
```json
{
  "preview": false,
  "outputs": {
    "projectId": "my-gcp-project",
    "region": "us-central1",
    "fabricOutputs": {
      "storage-bucket1-bucketName": "bucket1-my-project",
      "function-func1-url": "https://..."
    }
  },
  "summary": {
    "resources": {
      "create": 5,
      "update": 0,
      "delete": 0
    },
    "duration_sec": 45
  }
}
```

### POST /destroy

Destroy infrastructure.

**Request**:
```json
{
  "project": "my-project",
  "env": "dev",
  "creds": {
    "projectId": "my-gcp-project",
    "credentials": "/path/to/credentials.json"
  }
}
```

**Response**:
```json
{
  "destroyed": true,
  "resources_deleted": 5,
  "message": "Destroyed 5 resources via Pulumi."
}
```

---

## 🧪 Testing Strategy

### 1. Unit Testing

Test individual components:
- Service Registry: Test `get_creator` with valid/invalid kinds
- Naming: Test `safe_name` with various inputs
- Models: Test Pydantic validation

### 2. Integration Testing

Test full flow:
1. **Simple deployment**: 1 service, no edges
2. **Multiple services**: 3-4 services, no edges
3. **With edges**: 2-3 services with 1-2 edges
4. **Complex**: All services with multiple edges

### 3. API Testing

Use `curl` or Python `requests`:
```python
import requests

response = requests.post("http://localhost:8000/preview", json={
    "ir": {
        "project": "test",
        "env": "dev",
        "region": "us-central1",
        "nodes": [{"id": "s1", "kind": "gcp.storage", "name": "bucket1"}],
        "edges": []
    },
    "creds": {...}
})
```

---

## 📚 Key Differences: Azure vs GCP

### 1. Credentials

**Azure**:
```python
os.environ["ARM_CLIENT_ID"] = creds.clientId
os.environ["ARM_CLIENT_SECRET"] = creds.clientSecret
os.environ["ARM_TENANT_ID"] = creds.tenantId
os.environ["ARM_SUBSCRIPTION_ID"] = creds.subscriptionId
```

**GCP**:
```python
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds.credentials  # Path to JSON
os.environ["GOOGLE_PROJECT"] = creds.projectId
```

### 2. Resource Groups

**Azure**: Creates Resource Group
```python
rg = resources.ResourceGroup(f"rg-{project}-{env}")
```

**GCP**: Uses existing Project (or creates one)
```python
project_id = os.getenv("GOOGLE_PROJECT")
```

### 3. Naming

**Azure**: Many resources need globally unique names
**GCP**: Project-scoped, but some resources (buckets) need global uniqueness

### 4. Edge Connections

**Azure**: Uses Event Grid, connection strings, bindings
**GCP**: Uses Pub/Sub, IAM bindings, environment variables

---

## ✅ Checklist for GCP Implementation

### Phase 1: Core Setup
- [ ] Project structure created
- [ ] Dependencies installed (`pulumi-gcp`, `fastapi`, etc.)
- [ ] Models implemented (`GCPCreds` with `serviceAccountJson`, `IR`, etc.)
- [ ] Main API endpoints implemented (`/preview`, `/up`, `/destroy`)
- [ ] Credential handling (write JSON to temp file, set env vars)

### Phase 2: Core Services (3 Services)
- [ ] Service Registry created with 3 services
- [ ] `_create_storage` implemented (GCS bucket)
- [ ] `_create_pubsub` implemented (Pub/Sub topic)
- [ ] `_create_cloud_run` implemented (Cloud Run service)
- [ ] Naming service implemented

### Phase 3: Edge Connections
- [ ] `_connect` method implemented
- [ ] Storage → Pub/Sub connection (storage notifications)
- [ ] Pub/Sub → Cloud Run connection (push subscription)

### Phase 4: Integration
- [ ] Pulumi Engine adapted for GCP
- [ ] Program Builder adapted for GCP
- [ ] Preview API tested
- [ ] Up API tested
- [ ] Destroy API tested
- [ ] Edge connections tested end-to-end

### Phase 5: Testing
- [ ] Test with 1 service (Storage)
- [ ] Test with 2 services (Storage + Pub/Sub)
- [ ] Test with 3 services (Storage + Pub/Sub + Cloud Run)
- [ ] Test with edges (full pipeline)
- [ ] Test with your exact payload format

---

## 🚀 Adding More Services (Scalability)

The architecture is designed to be **easily extensible**. Here's how to add a new service:

### Step 1: Add Service Creation Method

In `app/services/gcp_fabric.py`, add a new `_create_<service>` method:

```python
def _create_cloud_function(self, node: Dict[str, Any]):
    """Create Cloud Function (example for future addition)"""
    name = safe_name(node.get("name") or node["id"])
    props = node.get("props", {})
    
    function = cloudfunctions.Function(
        f"function-{name}",
        name=name,
        runtime=props.get("runtime", "python39"),
        # ... more config
    )
    
    self.node_index[node["id"]] = {
        "kind": "gcp.cloudfunctions",
        "function": function,
    }
    
    self._outputs[f"function-{name}-url"] = function.https_trigger_url
```

### Step 2: Register in Service Registry

In `app/services/service_registry.py`, add to the registry:

```python
self._service_registry: Dict[str, Callable] = {
    "gcp.storage": self.fabric._create_storage,
    "gcp.pubsub": self.fabric._create_pubsub,
    "gcp.run": self.fabric._create_cloud_run,
    "gcp.cloudfunctions": self.fabric._create_cloud_function,  # NEW
}
```

### Step 3: Add Edge Connections (if needed)

In `_connect` method, add new connection patterns:

```python
# Cloud Function → Cloud Run
elif src_kind == "gcp.cloudfunctions" and dst_kind == "gcp.run":
    function_url = src["function"].https_trigger_url
    self._outputs[f"bind-{to_id}-function-url"] = function_url
```

### Step 4: Update Documentation

- Add service to supported services list
- Document props in IR format section
- Add example payload

**That's it!** The service is now available. No need to modify core architecture.

---

## 🎯 Next Steps

1. **Implement the 3 core services**: Storage, Pub/Sub, Cloud Run
2. **Test the pipeline**: Storage → Pub/Sub → Cloud Run
3. **Verify edge connections**: Upload file to Storage, see Cloud Run triggered
4. **Add more services as needed**: Use the scalability pattern above
5. **Expand edge connections**: Add more connection types between services

---

## 📖 Additional Resources

- [Pulumi GCP Documentation](https://www.pulumi.com/registry/packages/gcp/)
- [GCP REST API Reference](https://cloud.google.com/apis/docs/overview)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Pulumi Automation API](https://www.pulumi.com/docs/guides/automation-api/)

---

---

## 📝 Example Payload Reference

### Complete Working Example

```json
{
  "ir": {
    "project": "canvas-project",
    "env": "dev",
    "location": "us-central1",
    "nodes": [
      { 
        "id": "gcs-1", 
        "kind": "gcp.storage", 
        "name": "ingress-bucket", 
        "props": { 
          "uniformAccess": true 
        } 
      },
      { 
        "id": "ps-1", 
        "kind": "gcp.pubsub", 
        "name": "ingress-topic", 
        "props": { 
          "topicName": "ingress-topic" 
        } 
      },
      { 
        "id": "run-1", 
        "kind": "gcp.run", 
        "name": "worker",
        "props": { 
          "image": "gcr.io/cloudrun/hello", 
          "env": { 
            "LOG_LEVEL": "info" 
          }, 
          "allowUnauthenticated": true 
        } 
      }
    ],
    "edges": [
      { "from": "gcs-1", "to": "ps-1", "intent": "notify" },
      { "from": "ps-1", "to": "run-1", "intent": "notify" }
    ]
  },
  "creds": {
    "projectId": "my-gcp-project-123",
    "region": "us-central1",
    "serviceAccountJson": "{\"type\":\"service_account\",\"project_id\":\"my-project\",...}"
  }
}
```

### Service Props Reference

#### `gcp.storage`
```json
{
  "id": "gcs-1",
  "kind": "gcp.storage",
  "name": "my-bucket",
  "props": {
    "uniformAccess": true  // Enable uniform bucket-level access
  }
}
```

#### `gcp.pubsub`
```json
{
  "id": "ps-1",
  "kind": "gcp.pubsub",
  "name": "my-topic",
  "props": {
    "topicName": "my-topic"  // Optional: explicit topic name
  }
}
```

#### `gcp.run`
```json
{
  "id": "run-1",
  "kind": "gcp.run",
  "name": "my-service",
  "props": {
    "image": "gcr.io/cloudrun/hello",  // Container image
    "env": {                            // Environment variables
      "LOG_LEVEL": "info",
      "API_KEY": "secret"
    },
    "allowUnauthenticated": true,       // Allow public access
    "cpu": "1000m",                     // CPU allocation
    "memory": "512Mi"                   // Memory allocation
  }
}
```

---

**Last Updated**: 2025-01-27  
**Version**: 1.0.0  
**Status**: Implementation Guide (3 Core Services)  
**Focus**: Storage, Pub/Sub, Cloud Run with scalable architecture

