
# GCP Infra Composer (FastAPI + Pulumi Automation)

Generalized backend that composes GCP infra from a simple IR (nodes + edges). No gcloud CLI required — uses Service Account JSON and Pulumi Automation API.
Designed to scale: adapters & connectors registry for adding services/edge-types later.

## Setup

### 1. Environment Variables

Create a `.env` file in the project root (copy from `.env.example`):

```bash
# Copy the example file
cp .env.example .env

# Edit .env and add your values:
# - GCP_SERVICE_ACCOUNT_JSON: Your complete service account JSON key (as a single-line JSON string)
# - GCP_PROJECT_ID: Your GCP project ID (optional, can be overridden in API payload)
# - GCP_REGION: Default region (optional, defaults to us-central1)
# - GCP_ORG_ID: Organization ID (optional, needed for creating new projects)
# - PULUMI_STATE_DIR: Path to Pulumi state directory (default: pulumi-state)
# - PULUMI_HOME_DIR: Path to Pulumi home directory (default: .pulumi-home)
# - PULUMI_WORK_DIR: Path to Pulumi work directory (default: pulumi-work)
# - PULUMI_CONFIG_PASSPHRASE: Passphrase for Pulumi secrets (default: dev-passphrase)
# - PULUMI_CLI_PATH: Custom Pulumi CLI path (optional, auto-detected if in PATH)
```

**Important**: The `.env` file is already in `.gitignore` and will not be committed to version control.

### 2. Install Dependencies

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Run the Server

```bash
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
        "props": { "image": "gcr.io/cloudrun/hello", "env": { "LOG_LEVEL": "info" }, "allowUnauthenticated": true } },
      { "id": "sm-1",  "kind": "gcp.secretmanager", "name": "app-secrets",
        "props": { "secretValue": "my-secret-value" } },
      { "id": "cf-1",  "kind": "gcp.cloudfunctions", "name": "processor",
        "props": { "runtime": "python311", "entryPoint": "main", "sourceArchiveBucket": "my-bucket", "sourceArchiveObject": "function.zip" } },
      { "id": "fs-1",  "kind": "gcp.firestore", "name": "app-db",
        "props": { "locationId": "us-central" } }
    ],
    "edges": [
      { "from": "gcs-1", "to": "ps-1",  "intent": "notify" },
      { "from": "ps-1",  "to": "run-1", "intent": "notify" },
      { "from": "run-1", "to": "sm-1",  "intent": "access" },
      { "from": "cf-1",  "to": "fs-1",  "intent": "write" }
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

### Supported Services (6)

1. **gcp.storage** - Cloud Storage Bucket
   - Props: `uniformAccess` (bool), `forceDestroy` (bool), `labels` (dict)

2. **gcp.pubsub** - Pub/Sub Topic
   - Props: `topicName` (string), `labels` (dict)

3. **gcp.run** - Cloud Run Service (v2)
   - Props: `image` (string), `env` (dict), `allowUnauthenticated` (bool), `cpu` (string), `memory` (string)

4. **gcp.secretmanager** - Secret Manager Secret
   - Props: `secretValue` (string, optional) - Initial secret value

5. **gcp.cloudfunctions** - Cloud Functions (Gen 2)
   - Props: `runtime` (string, default: "python311"), `entryPoint` (string, default: "main"), `sourceArchiveBucket` (string, required), `sourceArchiveObject` (string, required), `availableMemoryMb` (int, default: 256), `timeout` (int, default: 60), `environmentVariables` (dict)

6. **gcp.firestore** - Firestore Database (Native mode)
   - Props: `locationId` (string, default: "us-central")

### Supported Edge Types

- **notify** - Event-driven notifications (Storage → Pub/Sub, Pub/Sub → Cloud Run)
- **access** - IAM-based access (Cloud Run → Secret Manager)
- **write** - Write permissions (Cloud Functions → Firestore)

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

### Free Tier Services

All 6 services are eligible for GCP free tier:
- **Cloud Storage**: 5GB storage, 5K Class A operations/month
- **Pub/Sub**: 10GB message storage, 10M operations/month
- **Cloud Run**: 2 million requests/month, 360K GB-seconds
- **Cloud Functions Gen 2**: 2 million invocations/month
- **Firestore**: 1GB storage, 50K reads/day, 20K writes/day
- **Secret Manager**: 6 secrets, 10K access operations/month

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
