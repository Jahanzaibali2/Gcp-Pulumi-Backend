"""
Validation module for GCP infrastructure IR.
Validates payloads before deployment to catch common issues.
"""

from __future__ import annotations
from typing import Dict, Any, List, Tuple
import re

# Valid Firestore location IDs
VALID_FIRESTORE_LOCATIONS = [
    "nam5",  # us-central (multi-region)
    "eur3",  # europe-west (multi-region)
    "asia-northeast1",  # Tokyo
    "asia-southeast1",  # Singapore
    "europe-west1",  # Belgium
    "europe-west2",  # London
    "us-central1",  # Iowa
    "us-east1",  # South Carolina
    "us-east4",  # Northern Virginia
    "us-west1",  # Oregon
    "us-west2",  # Los Angeles
    "us-west3",  # Salt Lake City
    "us-west4",  # Las Vegas
]

# Valid GCP regions
VALID_GCP_REGIONS = [
    "us-central1", "us-east1", "us-east4", "us-west1", "us-west2", "us-west3", "us-west4",
    "europe-west1", "europe-west2", "europe-west3", "europe-west4", "europe-west6",
    "asia-east1", "asia-east2", "asia-northeast1", "asia-northeast2", "asia-northeast3",
    "asia-south1", "asia-southeast1", "asia-southeast2",
    "australia-southeast1", "australia-southeast2",
    "southamerica-east1", "northamerica-northeast1",
]

# Valid Cloud Functions runtimes
VALID_CLOUD_FUNCTIONS_RUNTIMES = [
    "python311", "python312", "python313",
    "nodejs18", "nodejs20",
    "go121", "go122",
    "java17", "java21",
    "dotnet6", "dotnet8",
]

class ValidationError(Exception):
    """Custom exception for validation errors"""
    pass

class IRValidator:
    """Validates GCP infrastructure IR for common issues"""
    
    def __init__(self, ir: Dict[str, Any], project_id: str, region: str):
        self.ir = ir
        self.project_id = project_id
        self.region = region
        self.errors: List[str] = []
        self.warnings: List[str] = []
    
    def validate(self) -> Tuple[List[str], List[str]]:
        """
        Run all validations and return (errors, warnings).
        
        Returns:
            Tuple of (errors, warnings) lists
        """
        nodes = self.ir.get("nodes", [])
        edges = self.ir.get("edges", [])
        
        # Validate region
        self._validate_region()
        
        # Validate each node
        for node in nodes:
            kind = node.get("kind")
            if kind == "gcp.firestore":
                self._validate_firestore(node)
            elif kind == "gcp.cloudfunctions":
                self._validate_cloud_function(node)
            elif kind == "gcp.storage":
                self._validate_storage(node)
            elif kind == "gcp.pubsub":
                self._validate_pubsub(node)
            elif kind == "gcp.run":
                self._validate_cloud_run(node)
            elif kind == "gcp.secretmanager":
                self._validate_secret_manager(node)
        
        # Validate edges
        self._validate_edges(nodes, edges)
        
        # Validate resource name uniqueness
        self._validate_name_uniqueness(nodes)
        
        return (self.errors, self.warnings)
    
    def _validate_region(self):
        """Validate GCP region"""
        if self.region and self.region not in VALID_GCP_REGIONS:
            self.warnings.append(
                f"Region '{self.region}' may not be valid. "
                f"Valid regions include: {', '.join(VALID_GCP_REGIONS[:5])}..."
            )
    
    def _validate_firestore(self, node: Dict[str, Any]):
        """Validate Firestore database configuration"""
        props = node.get("props", {})
        location_id = props.get("locationId", "nam5")
        
        if location_id not in VALID_FIRESTORE_LOCATIONS:
            self.errors.append(
                f"Firestore node '{node.get('id')}': Invalid locationId '{location_id}'. "
                f"Valid locations: {', '.join(VALID_FIRESTORE_LOCATIONS[:5])}... "
                f"(Use 'nam5' for us-central multi-region, or a specific region like 'us-central1')"
            )
    
    def _validate_cloud_function(self, node: Dict[str, Any]):
        """Validate Cloud Function configuration"""
        props = node.get("props", {})
        
        # Check required fields
        if not props.get("sourceArchiveBucket"):
            self.errors.append(
                f"Cloud Function node '{node.get('id')}': Missing required prop 'sourceArchiveBucket'. "
                "Cloud Functions require a source archive bucket."
            )
        
        if not props.get("sourceArchiveObject"):
            self.errors.append(
                f"Cloud Function node '{node.get('id')}': Missing required prop 'sourceArchiveObject'. "
                "Cloud Functions require a source archive object (zip file)."
            )
        
        # Validate runtime
        runtime = props.get("runtime", "python311")
        if runtime not in VALID_CLOUD_FUNCTIONS_RUNTIMES:
            self.warnings.append(
                f"Cloud Function node '{node.get('id')}': Runtime '{runtime}' may not be supported. "
                f"Valid runtimes: {', '.join(VALID_CLOUD_FUNCTIONS_RUNTIMES)}"
            )
        
        # Validate memory
        memory_mb = props.get("availableMemoryMb", 256)
        if memory_mb < 128 or memory_mb > 8192:
            self.warnings.append(
                f"Cloud Function node '{node.get('id')}': Memory {memory_mb}MB is outside typical range (128-8192MB)."
            )
        
        # Validate timeout
        timeout = props.get("timeout", 60)
        if timeout < 1 or timeout > 540:
            self.errors.append(
                f"Cloud Function node '{node.get('id')}': Timeout {timeout}s is invalid. "
                "Must be between 1 and 540 seconds."
            )
    
    def _validate_storage(self, node: Dict[str, Any]):
        """Validate Storage bucket configuration"""
        name = node.get("name") or node.get("id", "")
        
        # GCS bucket names must be globally unique
        if len(name) < 3 or len(name) > 63:
            self.errors.append(
                f"Storage node '{node.get('id')}': Bucket name '{name}' is invalid. "
                "Must be 3-63 characters long."
            )
        
        # Check for invalid characters
        if not re.match(r'^[a-z0-9][a-z0-9\-_]*[a-z0-9]$', name.lower()):
            self.warnings.append(
                f"Storage node '{node.get('id')}': Bucket name '{name}' contains invalid characters. "
                "Should only contain lowercase letters, numbers, hyphens, and underscores."
            )
    
    def _validate_pubsub(self, node: Dict[str, Any]):
        """Validate Pub/Sub topic configuration"""
        props = node.get("props", {})
        topic_name = props.get("topicName") or node.get("name") or node.get("id", "")
        
        # Topic names must be valid
        if len(topic_name) < 3 or len(topic_name) > 255:
            self.warnings.append(
                f"Pub/Sub node '{node.get('id')}': Topic name '{topic_name}' may be invalid. "
                "Should be 3-255 characters."
            )
    
    def _validate_cloud_run(self, node: Dict[str, Any]):
        """Validate Cloud Run service configuration"""
        props = node.get("props", {})
        
        if not props.get("image"):
            self.warnings.append(
                f"Cloud Run node '{node.get('id')}': No image specified. "
                "Using default 'gcr.io/cloudrun/hello'."
            )
        
        # Validate CPU and memory format
        cpu = props.get("cpu", "1000m")
        if not re.match(r'^\d+m$', str(cpu)) and cpu not in ["1", "2", "4", "6", "8"]:
            self.warnings.append(
                f"Cloud Run node '{node.get('id')}': CPU '{cpu}' format may be invalid. "
                "Use format like '1000m' or '1', '2', etc."
            )
        
        memory = props.get("memory", "512Mi")
        if not re.match(r'^\d+[KMGT]?i?$', str(memory)):
            self.warnings.append(
                f"Cloud Run node '{node.get('id')}': Memory '{memory}' format may be invalid. "
                "Use format like '512Mi', '1Gi', etc."
            )
    
    def _validate_secret_manager(self, node: Dict[str, Any]):
        """Validate Secret Manager configuration"""
        name = node.get("name") or node.get("id", "")
        
        # Secret names must be valid
        if len(name) < 1 or len(name) > 255:
            self.errors.append(
                f"Secret Manager node '{node.get('id')}': Secret name '{name}' is invalid. "
                "Must be 1-255 characters long."
            )
        
        # Check for invalid characters
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            self.errors.append(
                f"Secret Manager node '{node.get('id')}': Secret name '{name}' contains invalid characters. "
                "Should only contain letters, numbers, hyphens, and underscores."
            )
    
    def _validate_edges(self, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]):
        """Validate edge connections"""
        node_ids = {node.get("id") for node in nodes}
        
        for edge in edges:
            from_id = edge.get("from") or edge.get("from_")
            to_id = edge.get("to")
            
            if not from_id or not to_id:
                self.errors.append(
                    f"Edge missing 'from' or 'to' field: {edge}"
                )
                continue
            
            if from_id not in node_ids:
                self.errors.append(
                    f"Edge references unknown source node '{from_id}'. "
                    f"Available nodes: {', '.join(sorted(node_ids))}"
                )
            
            if to_id not in node_ids:
                self.errors.append(
                    f"Edge references unknown destination node '{to_id}'. "
                    f"Available nodes: {', '.join(sorted(node_ids))}"
                )
            
            # Validate intent
            intent = edge.get("intent", "notify")
            valid_intents = ["notify", "access", "write"]
            if intent not in valid_intents:
                self.warnings.append(
                    f"Edge from '{from_id}' to '{to_id}': Intent '{intent}' may not be supported. "
                    f"Valid intents: {', '.join(valid_intents)}"
                )
    
    def _validate_name_uniqueness(self, nodes: List[Dict[str, Any]]):
        """Check for duplicate resource names within the same kind"""
        name_by_kind: Dict[str, Dict[str, str]] = {}
        
        for node in nodes:
            kind = node.get("kind")
            name = node.get("name") or node.get("id", "")
            node_id = node.get("id")
            
            if kind not in name_by_kind:
                name_by_kind[kind] = {}
            
            if name in name_by_kind[kind] and name_by_kind[kind][name] != node_id:
                self.errors.append(
                    f"Duplicate resource name '{name}' for kind '{kind}'. "
                    f"Nodes '{name_by_kind[kind][name]}' and '{node_id}' both use this name. "
                    "Resource names must be unique within the same service kind."
                )
            else:
                name_by_kind[kind][name] = node_id

