
from __future__ import annotations
from typing import Dict, Any, Callable, Tuple
import pulumi
import pulumi_gcp as gcp
from .naming import safe_name
from .service_registry import ServiceRegistry

CreateFn = Callable[[Dict[str, Any]], Dict[str, Any]]
ConnectFn = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], None]

class GcpFabric:
    """
    Generalized, adapter-based fabric for GCP.
    v0 adapters:
      - gcp.storage  -> Cloud Storage Bucket
      - gcp.pubsub   -> Pub/Sub Topic
      - gcp.run      -> Cloud Run (v2) Service
    v0 edges (intent='notify'):
      - storage -> pubsub : Bucket Notification (OBJECT_FINALIZE) -> Topic
      - pubsub  -> run    : Push Subscription -> Cloud Run URL
    The adapter/edge registries make it easy to add more services later.
    """
    def __init__(self, project_id: str, region: str, project_number: pulumi.Output[int] = None):
        self.project_id = project_id
        self.region = region
        self.project_number = project_number  # For constructing service account emails
        self.node_index: Dict[str, Dict[str, Any]] = {}
        self._outputs: Dict[str, pulumi.Output[Any]] = {}
        
        # Use ServiceRegistry for better separation of concerns
        self._registry = ServiceRegistry(self)

        # --- Edge registry ((src_kind, dst_kind, intent) -> connect_fn) ---
        self._connectors: Dict[Tuple[str, str, str], ConnectFn] = {
            ("gcp.storage", "gcp.pubsub", "notify"): self._wire_bucket_to_pubsub,
            ("gcp.pubsub", "gcp.run", "notify"): self._wire_pubsub_to_run,
        }

    # -------- Public API --------
    def outputs(self) -> Dict[str, pulumi.Output[Any]]:
        return self._outputs

    def apply_ir(self, ir: Dict[str, Any]):
        """
        Apply the IR (Intermediate Representation) to create resources and connections.
        
        Args:
            ir: IR dictionary with nodes and edges
        """
        nodes = ir.get("nodes", [])
        edges = ir.get("edges", [])
        
        # 1) Build nodes using registry
        for node in nodes:
            kind = node.get("kind")
            if not kind:
                pulumi.log.warn(f"Node missing 'kind': {node}; skipping")
                continue
            try:
                creator = self._registry.get_creator(kind)
                creator(node)
            except ValueError as e:
                raise ValueError(str(e))

        # 2) Connect edges
        for edge in edges:
            from_id = edge.get("from") or edge.get("from_")
            to_id = edge.get("to")
            if not from_id or not to_id:
                pulumi.log.warn(f"Edge missing endpoints: {edge}; skipping")
                continue
            
            src = self.node_index.get(from_id)
            dst = self.node_index.get(to_id)
            if not src or not dst:
                pulumi.log.warn(f"Edge references unknown node: {edge}; skipping")
                continue
            
            intent = edge.get("intent", "notify")
            key = (src["kind"], dst["kind"], intent)
            fn = self._connectors.get(key)
            if not fn:
                pulumi.log.warn(f"No connector for {key}; skipping")
                continue
            fn(src, dst, edge)

    # -------- Adapters (node creators) --------
    def _create_storage(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Create GCS Bucket"""
        name = safe_name(node.get("name") or node["id"])
        props = node.get("props", {})
        uniform = bool(props.get("uniformAccess", True))

        bucket = gcp.storage.Bucket(
            f"bucket-{name}",
            location=self.region,
            uniform_bucket_level_access=uniform,
            force_destroy=props.get("forceDestroy", False),
            labels=props.get("labels")
        )

        # Store node metadata separately from Pulumi Output objects
        self.node_index[node["id"]] = {
            "kind": "gcp.storage",
            "bucket": bucket,
            "name": bucket.name,  # This is an Output, but we'll use node["name"] for string operations
            "node_name": node.get("name") or node["id"],  # Store original name for string operations
        }
        
        self._outputs[f"storage-{name}-bucketName"] = bucket.name
        self._outputs[f"storage-{name}-url"] = bucket.url
        return self.node_index[node["id"]]

    def _create_pubsub(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Create Pub/Sub Topic"""
        name = safe_name(node.get("name") or node["id"])
        props = node.get("props", {})
        topic_name = props.get("topicName") or name

        topic = gcp.pubsub.Topic(
            f"topic-{name}",
            name=topic_name,
            labels=props.get("labels")
        )

        # Store node metadata separately from Pulumi Output objects
        self.node_index[node["id"]] = {
            "kind": "gcp.pubsub",
            "topic": topic,
            "name": topic.name,  # This is an Output, but we'll use node["name"] for string operations
            "node_name": node.get("name") or node["id"],  # Store original name for string operations
        }
        
        self._outputs[f"pubsub-{name}-topicName"] = topic.name
        self._outputs[f"pubsub-{name}-topicId"] = topic.id
        return self.node_index[node["id"]]

    def _create_cloud_run(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Create Cloud Run Service"""
        name = safe_name(node.get("name") or node["id"])
        props = node.get("props", {})
        image = props.get("image", "gcr.io/cloudrun/hello")
        allow_unauth = bool(props.get("allowUnauthenticated", True))
        cpu = props.get("cpu", "1000m")
        memory = props.get("memory", "512Mi")
        env_dict = props.get("env", {})

        # Build environment variables - use correct class name for Cloud Run v2
        env_vars = []
        for k, v in env_dict.items():
            env_vars.append(gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                name=k,
                value=str(v)
            ))

        service = gcp.cloudrunv2.Service(
            f"run-{name}",
            location=self.region,
            template=gcp.cloudrunv2.ServiceTemplateArgs(
                containers=[
                    gcp.cloudrunv2.ServiceTemplateContainerArgs(
                        image=image,
                        envs=env_vars,  # Use 'envs' not 'env'
                        resources=gcp.cloudrunv2.ServiceTemplateContainerResourcesArgs(
                            limits={"cpu": str(cpu), "memory": str(memory)}
                        )
                    )
                ],
            ),
            ingress="INGRESS_TRAFFIC_ALL"
        )

        # Public access (optional)
        if allow_unauth:
            gcp.cloudrunv2.ServiceIamMember(
                f"{name}-invoker-allUsers",
                name=service.name,
                location=self.region,
                role="roles/run.invoker",
                member="allUsers"
            )

        # Store node metadata separately from Pulumi Output objects
        self.node_index[node["id"]] = {
            "kind": "gcp.run",
            "service": service,
            "name": service.name,  # This is an Output, but we'll use node["name"] for string operations
            "node_name": node.get("name") or node["id"],  # Store original name for string operations
        }
        
        self._outputs[f"cloudrun-{name}-url"] = service.uri
        self._outputs[f"cloudrun-{name}-name"] = service.name
        return self.node_index[node["id"]]

    # -------- Connectors (edge handlers) --------
    def _wire_bucket_to_pubsub(self, src: Dict[str, Any], dst: Dict[str, Any], edge: Dict[str, Any]):
        """
        Create a GCS Notification to publish to Pub/Sub topic on OBJECT_FINALIZE.
        Storage → Pub/Sub connection.
        """
        bucket = src["bucket"]
        topic = dst["topic"]
        event_types = edge.get("eventTypes") or ["OBJECT_FINALIZE"]
        
        # Use node_name (original IR name) for resource naming, not Pulumi Output objects
        src_id = safe_name(src.get("node_name", src.get("id", "storage")))
        dst_id = safe_name(dst.get("node_name", dst.get("id", "pubsub")))
        
        # Get project number to construct Storage service account email
        # Format: service-{PROJECT_NUMBER}@gs-project-accounts.iam.gserviceaccount.com
        # Use project_number passed from program_builder (already fetched there)
        if self.project_number:
            # project_number might be a string or an Output - handle both cases
            if isinstance(self.project_number, str):
                # If it's already a string, wrap it in Output
                storage_sa_email = pulumi.Output.from_input(
                    f"serviceAccount:service-{self.project_number}@gs-project-accounts.iam.gserviceaccount.com"
                )
            else:
                # If it's an Output, use apply
                storage_sa_email = self.project_number.apply(
                    lambda num: f"serviceAccount:service-{num}@gs-project-accounts.iam.gserviceaccount.com"
                )
        else:
            # Fallback: try to get it here if not provided
            project_data = gcp.projects.get_project(filter=f"projectId:{self.project_id}")
            storage_sa_email = project_data.projects[0].number.apply(
                lambda num: f"serviceAccount:service-{num}@gs-project-accounts.iam.gserviceaccount.com"
            )
        
        # Grant Storage service account permission to publish to Pub/Sub topic
        # This IAM binding must be created before the notification
        topic_publisher = gcp.pubsub.TopicIAMMember(
            f"topic-publisher-{src_id}-{dst_id}",
            topic=topic.name,
            role="roles/pubsub.publisher",
            member=storage_sa_email,
        )
        
        notification = gcp.storage.Notification(
            f"notif-{src_id}-to-{dst_id}",
            bucket=bucket.name,  # Pulumi Output - OK for resource properties
            topic=topic.id,      # Pulumi Output - OK for resource properties
            event_types=event_types,
            payload_format="JSON_API_V1",
            opts=pulumi.ResourceOptions(depends_on=[topic_publisher]),  # Ensure IAM is set before notification
        )
        
        self._outputs[f"bind-{dst.get('node_name', 'pubsub')}-topic"] = topic.name
        # Use node_name for logging (string, not Output)
        src_name = src.get('node_name', 'storage')
        dst_name = dst.get('node_name', 'pubsub')
        pulumi.log.info(f"Connected Storage '{src_name}' → Pub/Sub '{dst_name}' via notification")

    def _wire_pubsub_to_run(self, src: Dict[str, Any], dst: Dict[str, Any], edge: Dict[str, Any]):
        """
        Create a push subscription from Pub/Sub topic to Cloud Run URL.
        Pub/Sub → Cloud Run connection.
        """
        topic = src["topic"]
        service = dst["service"]
        
        # Use node_name (original IR name) for subscription naming, not Pulumi Output objects
        src_id = safe_name(src.get("node_name", src.get("id", "pubsub")))
        dst_id = safe_name(dst.get("node_name", dst.get("id", "run")))
        sub_name = edge.get("subscriptionName") or f"sub-{src_id}-to-{dst_id}"

        # Create push subscription
        subscription = gcp.pubsub.Subscription(
            sub_name,
            name=sub_name,
            topic=topic.name,  # Pulumi Output - OK for resource properties
            push_config=gcp.pubsub.SubscriptionPushConfigArgs(
                push_endpoint=service.uri,  # Pulumi Output - OK for resource properties
            )
        )
        
        # Note: For authenticated push, we would need to grant Pub/Sub service account
        # permission to invoke Cloud Run. This requires the project number.
        # For now, this works when Cloud Run is public (allowUnauthenticated=true)
        
        self._outputs[f"bind-{dst.get('node_name', 'run')}-subscription"] = subscription.name
        # Use node_name for logging (string, not Output)
        src_name = src.get('node_name', 'pubsub')
        dst_name = dst.get('node_name', 'run')
        pulumi.log.info(f"Connected Pub/Sub '{src_name}' → Cloud Run '{dst_name}' via push subscription")
