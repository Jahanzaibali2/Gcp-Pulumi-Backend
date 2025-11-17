
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
    Supported services:
      - gcp.storage         -> Cloud Storage Bucket
      - gcp.pubsub          -> Pub/Sub Topic
      - gcp.run             -> Cloud Run (v2) Service
      - gcp.firestore       -> Firestore Database (Native mode)
      - gcp.secretmanager   -> Secret Manager Secret
    Supported edges:
      - storage -> pubsub (notify): Bucket Notification -> Topic
      - pubsub  -> run (notify): Push Subscription -> Cloud Run URL
      - run -> secretmanager (access): IAM binding for secret access
    The adapter/edge registries make it easy to add more services later.
    """
    def __init__(self, project_id: str, region: str, project_number: pulumi.Output[int] = None, api_services: list = None):
        self.project_id = project_id
        self.region = region
        self.project_number = project_number  # For constructing service account emails
        self.api_services = api_services or []  # API services to depend on
        self.node_index: Dict[str, Dict[str, Any]] = {}
        self._outputs: Dict[str, pulumi.Output[Any]] = {}

        # Use ServiceRegistry for better separation of concerns
        self._registry = ServiceRegistry(self)

        # --- Edge registry ((src_kind, dst_kind, intent) -> connect_fn) ---
        self._connectors: Dict[Tuple[str, str, str], ConnectFn] = {
            ("gcp.storage", "gcp.pubsub", "notify"): self._wire_bucket_to_pubsub,
            ("gcp.pubsub", "gcp.run", "notify"): self._wire_pubsub_to_run,
            ("gcp.run", "gcp.secretmanager", "access"): self._wire_run_to_secretmanager,
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

    def _create_firestore(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Create Firestore Database (Native mode)
        
        Note: GCP projects can only have one default database (or databases with unique names).
        If a database with the same name already exists, Pulumi will import it instead of creating.
        """
        name = safe_name(node.get("name") or node["id"])
        props = node.get("props", {})
        # Firestore locationId must be a valid region like "us-central1" or "nam5" (multi-region)
        # Default to "nam5" (us-central multi-region) for free tier
        location_id = props.get("locationId", "nam5")

        # Find Firestore API service to depend on
        firestore_api = None
        for api_service in self.api_services:
            try:
                # Match by resource name to avoid __str__ on Output
                resource_name = str(api_service) if hasattr(api_service, '__name__') else ''
                if "firestore" in resource_name.lower() or "enable-firestore" in resource_name.lower():
                    firestore_api = api_service
                    break
            except:
                # Fallback: check resource name
                resource_name = str(api_service) if hasattr(api_service, '__name__') else ''
                if "firestore" in resource_name.lower():
                    firestore_api = api_service
                    break
        
        # Firestore database creation
        # Note: GCP projects can only have one default database per location.
        # If a database with the same name already exists, you'll get a 409 error.
        # In that case, you should either:
        # 1. Use a different database name
        # 2. Delete the existing database first (if safe to do so)
        # 3. Use the existing database by not creating a new one
        database = gcp.firestore.Database(
            f"firestore-{name}",
            name=name,
            location_id=location_id,
            type="FIRESTORE_NATIVE",  # Native mode for free tier
            project=self.project_id,
            opts=pulumi.ResourceOptions(
                depends_on=[firestore_api] if firestore_api else [],
                retain_on_delete=True,  # Don't delete database on destroy (safer - databases are critical)
            )
        )

        # Store node metadata separately from Pulumi Output objects
        self.node_index[node["id"]] = {
            "kind": "gcp.firestore",
            "database": database,
            "name": database.name,
            "node_name": node.get("name") or node["id"],
        }
        
        self._outputs[f"firestore-{name}-databaseId"] = database.name
        self._outputs[f"firestore-{name}-locationId"] = database.location_id
        return self.node_index[node["id"]]

    def _create_secret_manager(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Create Secret Manager Secret"""
        name = safe_name(node.get("name") or node["id"])
        props = node.get("props", {})
        secret_value = props.get("secretValue")  # Optional initial secret value

        # Find Secret Manager API service to depend on
        secretmanager_api = None
        for api_service in self.api_services:
            try:
                # Match by resource name to avoid __str__ on Output
                resource_name = str(api_service) if hasattr(api_service, '__name__') else ''
                if "secretmanager" in resource_name.lower() or "enable-secretmanager" in resource_name.lower():
                    secretmanager_api = api_service
                    break
            except:
                # Fallback: check resource name
                resource_name = str(api_service) if hasattr(api_service, '__name__') else ''
                if "secretmanager" in resource_name.lower():
                    secretmanager_api = api_service
                    break
        
        # Create secret with automatic replication (free tier)
        # Replication is required - use SecretReplicationArgs with auto property
        # Note: The class is SecretReplicationAutoArgs (not SecretReplicationAutomaticArgs)
        secret = gcp.secretmanager.Secret(
            f"secret-{name}",
            secret_id=name,
            replication=gcp.secretmanager.SecretReplicationArgs(
                auto=gcp.secretmanager.SecretReplicationAutoArgs()
            ),
            project=self.project_id,
            opts=pulumi.ResourceOptions(depends_on=[secretmanager_api] if secretmanager_api else [])
        )

        # If initial secret value provided, create a version
        secret_version = None
        if secret_value:
            secret_version = gcp.secretmanager.SecretVersion(
                f"secret-version-{name}",
                secret=secret.id,
                secret_data=secret_value,
                opts=pulumi.ResourceOptions(depends_on=[secret])
            )

        # Store node metadata separately from Pulumi Output objects
        self.node_index[node["id"]] = {
            "kind": "gcp.secretmanager",
            "secret": secret,
            "secret_version": secret_version,
            "name": secret.secret_id,
            "node_name": node.get("name") or node["id"],
        }
        
        self._outputs[f"secret-{name}-secretId"] = secret.secret_id
        self._outputs[f"secret-{name}-name"] = secret.name
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
        # Note: For authenticated push, we would need to grant Pub/Sub service account
        # permission to invoke Cloud Run. For now, this works when Cloud Run is public (allowUnauthenticated=true)
        subscription = gcp.pubsub.Subscription(
            sub_name,
            name=sub_name,
            topic=topic.name,  # Pulumi Output - OK for resource properties
            push_config=gcp.pubsub.SubscriptionPushConfigArgs(
                push_endpoint=service.uri,  # Pulumi Output - OK for resource properties
            ),
            # Ensure subscription is created after both topic and service exist
            opts=pulumi.ResourceOptions(depends_on=[topic, service])
        )
        
        self._outputs[f"bind-{dst.get('node_name', 'run')}-subscription"] = subscription.name
        # Use node_name for logging (string, not Output)
        src_name = src.get('node_name', 'pubsub')
        dst_name = dst.get('node_name', 'run')
        pulumi.log.info(f"Connected Pub/Sub '{src_name}' → Cloud Run '{dst_name}' via push subscription")

    def _wire_run_to_secretmanager(self, src: Dict[str, Any], dst: Dict[str, Any], edge: Dict[str, Any]):
        """
        Grant Cloud Run service account access to Secret Manager secret.
        Cloud Run → Secret Manager connection.
        """
        service = src["service"]
        secret = dst["secret"]
        
        # Use node_name (original IR name) for resource naming
        src_id = safe_name(src.get("node_name", src.get("id", "run")))
        dst_id = safe_name(dst.get("node_name", dst.get("id", "secretmanager")))
        
        # Get project number to construct Cloud Run service account email
        # Format: {PROJECT_NUMBER}-compute@developer.gserviceaccount.com
        if self.project_number:
            if isinstance(self.project_number, str):
                run_sa_email = pulumi.Output.from_input(
                    f"serviceAccount:{self.project_number}-compute@developer.gserviceaccount.com"
                )
            else:
                run_sa_email = self.project_number.apply(
                    lambda num: f"serviceAccount:{num}-compute@developer.gserviceaccount.com"
                )
        else:
            # Fallback: try to get it here if not provided
            project_data = gcp.projects.get_project(filter=f"projectId:{self.project_id}")
            if isinstance(project_data.projects, list):
                proj_num = project_data.projects[0].get('number') if project_data.projects and len(project_data.projects) > 0 else None
                run_sa_email = pulumi.Output.from_input(
                    f"serviceAccount:{proj_num}-compute@developer.gserviceaccount.com"
                ) if proj_num else None
            else:
                run_sa_email = project_data.projects.apply(
                    lambda projs: f"serviceAccount:{projs[0].get('number')}-compute@developer.gserviceaccount.com"
                    if projs and len(projs) > 0 else None
                )
        
        if run_sa_email:
            # Grant Cloud Run service account permission to access secret
            iam_member = gcp.secretmanager.SecretIamMember(
                f"secret-accessor-{src_id}-{dst_id}",
                secret_id=secret.secret_id,
                role="roles/secretmanager.secretAccessor",
                member=run_sa_email,
            )
            
            self._outputs[f"bind-{dst.get('node_name', 'secretmanager')}-iam"] = iam_member.member
            src_name = src.get('node_name', 'run')
            dst_name = dst.get('node_name', 'secretmanager')
            pulumi.log.info(f"Connected Cloud Run '{src_name}' → Secret Manager '{dst_name}' via IAM binding")
        else:
            pulumi.log.warn(f"Could not determine project number for Cloud Run service account")
