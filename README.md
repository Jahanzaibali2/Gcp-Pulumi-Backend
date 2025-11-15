
# GCP Infra Composer (FastAPI + Pulumi Automation)

Generalized backend that composes GCP infra from a simple IR (nodes + edges). No gcloud CLI required — uses Service Account JSON and Pulumi Automation API.
Designed to scale: adapters & connectors registry for adding services/edge-types later.

## Run
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Example /up payload

**Note**: The `project` field in IR creates a GCP project (like Azure resource groups). If `projectId` is not provided in creds, a project will be created from the IR project name.

```json
{
  "ir": {
    "project": "clean-architecture",
    "env": "prod",
    "location": "us-central1",
    "nodes": [
      { "id": "gcs-1", "kind": "gcp.storage", "name": "ingress-bucket", "props": { "uniformAccess": true } },
      { "id": "ps-1",  "kind": "gcp.pubsub",  "name": "ingress-topic", "props": { "topicName": "ingress-topic" } },
      { "id": "run-1", "kind": "gcp.run",     "name": "worker",
        "props": { "image": "gcr.io/cloudrun/hello", "env": { "LOG_LEVEL": "info" }, "allowUnauthenticated": true } }
    ],
    "edges": [
      { "from": "gcs-1", "to": "ps-1",  "intent": "notify" },
      { "from": "ps-1",  "to": "run-1", "intent": "notify" }
    ]
  },
  "creds": {
    "projectId": null,
    "region": "us-central1",
    "serviceAccountJson": "{ ... full service account JSON ... }",
    "orgId": "123456789012"
  }
}
```

### Project Creation Behavior

- **If `projectId` is provided in creds**: Uses that existing project (like Azure subscription)
- **If `projectId` is null/omitted**: Creates a new GCP project from IR `project` field + `env` (like Azure resource group)
  - Project ID format: `{project}-{env}` (e.g., `clean-architecture-prod`)
  - Requires `orgId` in creds to create new projects
  - If `orgId` is not provided, assumes project already exists

## Architecture

The system uses a **scalable registry pattern** that makes it easy to add new services:

- **ServiceRegistry** (`app/services/service_registry.py`): Maps service kinds to creation methods
- **GcpFabric** (`app/services/gcp_fabric.py`): Creates resources and handles edge connections
- **Adapter Pattern**: Each service has a `_create_<service>` method
- **Connector Pattern**: Edge connections are handled via `_wire_<src>_to_<dst>` methods

## Extending

### Adding a New Service

1. **Create the service method** in `GcpFabric`:
   ```python
   def _create_cloud_function(self, node: Dict[str, Any]):
       # Implementation here
       pass
   ```

2. **Register it** in `ServiceRegistry` (`app/services/service_registry.py`):
   ```python
   self._service_registry: Dict[str, Callable] = {
       "gcp.storage": self.fabric._create_storage,
       "gcp.pubsub": self.fabric._create_pubsub,
       "gcp.run": self.fabric._create_cloud_run,
       "gcp.cloudfunctions": self.fabric._create_cloud_function,  # Add this
   }
   ```

3. **Add edge connections** (if needed) in `GcpFabric._connectors`:
   ```python
   self._connectors: Dict[Tuple[str, str, str], ConnectFn] = {
       # ... existing connectors
       ("gcp.cloudfunctions", "gcp.run", "notify"): self._wire_function_to_run,
   }
   ```

That's it! The service is now available. The IR format stays stable; only the registry grows.
