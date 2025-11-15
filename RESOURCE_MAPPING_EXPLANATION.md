# Resource Mapping: IR Nodes → GCP Resources

## The Concept

When you define **nodes** (the main resources) and **edges** (connections between them) in your IR, the system creates **additional GCP resources** to implement those connections. This is the same pattern used in Azure.

## Your Payload Breakdown

### Your IR Has:
- **3 Nodes:**
  1. `gcs-main` (gcp.storage)
  2. `ps-events` (gcp.pubsub)
  3. `run-worker` (gcp.run)

- **2 Edges:**
  1. `gcs-main` → `ps-events` (intent: "notify")
  2. `ps-events` → `run-worker` (intent: "notify")

### But Pulumi Creates 7 Resources:

#### 1. **Storage Bucket** (from node 1)
   - **Source:** `gcs-main` node
   - **GCP Resource:** `gcp:storage:Bucket`
   - **Name:** `bucket-cleanstorage2025`

#### 2. **Pub/Sub Topic** (from node 2)
   - **Source:** `ps-events` node
   - **GCP Resource:** `gcp:pubsub:Topic`
   - **Name:** `topic-clean-event-bus`

#### 3. **Cloud Run Service** (from node 3)
   - **Source:** `run-worker` node
   - **GCP Resource:** `gcp:cloudrunv2:Service`
   - **Name:** `run-clean-worker`

#### 4. **Storage Notification** (from edge 1)
   - **Source:** Edge `gcs-main` → `ps-events`
   - **GCP Resource:** `gcp:storage:Notification`
   - **Purpose:** Publishes bucket events (OBJECT_FINALIZE) to Pub/Sub topic
   - **Name:** `notif-gcs-main-to-ps-events`
   - **Why it exists:** GCS buckets don't directly connect to Pub/Sub. You need a Notification resource to bridge them.

#### 5. **Pub/Sub Subscription** (from edge 2)
   - **Source:** Edge `ps-events` → `run-worker`
   - **GCP Resource:** `gcp:pubsub:Subscription`
   - **Purpose:** Pushes messages from topic to Cloud Run service URL
   - **Name:** `sub-ps-events-to-run-worker`
   - **Why it exists:** Topics don't directly push to Cloud Run. You need a Subscription with push configuration.

#### 6. **Cloud Run IAM Member** (from node 3 property)
   - **Source:** `allowUnauthenticated: true` in `run-worker` props
   - **GCP Resource:** `gcp:cloudrunv2:ServiceIamMember`
   - **Purpose:** Grants `allUsers` permission to invoke the Cloud Run service
   - **Name:** `clean-worker-invoker-allUsers`
   - **Why it exists:** Making a Cloud Run service public requires an IAM binding.

#### 7. **Pulumi Stack** (always created)
   - **Source:** Pulumi infrastructure
   - **GCP Resource:** `pulumi:pulumi:Stack`
   - **Purpose:** Tracks all resources in this deployment
   - **Name:** `clean-architecture-clean-architecture-dev`
   - **Why it exists:** Pulumi always creates a Stack to manage state.

---

## The General Pattern

### Nodes → Direct Resources
Each node in your IR creates **one primary GCP resource**:
- `gcp.storage` → `gcp:storage:Bucket`
- `gcp.pubsub` → `gcp:pubsub:Topic`
- `gcp.run` → `gcp:cloudrunv2:Service`

### Edges → Connection Resources
Each edge creates **additional GCP resources** to implement the connection:
- `storage → pubsub` → `gcp:storage:Notification`
- `pubsub → run` → `gcp:pubsub:Subscription`

### Properties → Supporting Resources
Some properties create **supporting resources**:
- `allowUnauthenticated: true` → `gcp:cloudrunv2:ServiceIamMember`

---

## Azure Comparison

This is **exactly the same** as Azure:

### Azure Example:
```json
{
  "nodes": [
    {"id": "storage", "kind": "azure.storage"},
    {"id": "function", "kind": "azure.functions"}
  ],
  "edges": [
    {"from": "storage", "to": "function", "intent": "notify"}
  ]
}
```

**Creates:**
1. Storage Account (from node)
2. Function App (from node)
3. **Event Grid Subscription** (from edge) ← Additional resource!
4. **Storage Queue** (if needed) ← Additional resource!

---

## Why This Design?

### 1. **Separation of Concerns**
   - Nodes = "What resources do I need?"
   - Edges = "How do they connect?"
   - The system figures out the implementation details.

### 2. **Cloud Provider Reality**
   - GCP doesn't have "direct connections" between services
   - You need intermediate resources (Notifications, Subscriptions, IAM bindings)
   - The IR abstracts this complexity away.

### 3. **Consistency**
   - Same pattern works for Azure, AWS, etc.
   - You think in terms of "connect A to B"
   - The system creates the necessary plumbing.

---

## Summary

**Your 3 nodes + 2 edges = 7 GCP resources**

- 3 from nodes (bucket, topic, cloud run)
- 2 from edges (notification, subscription)
- 1 from properties (IAM member)
- 1 from Pulumi (stack)

This is **normal and expected**. The IR is a high-level abstraction; the actual cloud infrastructure requires more resources to implement the connections.

