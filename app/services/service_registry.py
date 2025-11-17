
from __future__ import annotations
from typing import Dict, Callable

class ServiceRegistry:
    """
    Registry pattern for mapping service kinds to creation methods.
    Makes it easy to add new services without modifying core fabric logic.
    """
    def __init__(self, fabric_instance):
        self.fabric = fabric_instance
        # 6 services - easily extensible
        self._service_registry: Dict[str, Callable] = {
            "gcp.storage": self.fabric._create_storage,
            "gcp.pubsub": self.fabric._create_pubsub,
            "gcp.run": self.fabric._create_cloud_run,
            "gcp.cloudfunctions": self.fabric._create_cloud_function,
            "gcp.firestore": self.fabric._create_firestore,
            "gcp.secretmanager": self.fabric._create_secret_manager,
        }
    
    def get_creator(self, kind: str) -> Callable:
        """
        Get the creation function for a given service kind.
        
        Args:
            kind: Service kind (e.g., "gcp.storage", "gcp.pubsub")
            
        Returns:
            Callable: The creation function for the service
            
        Raises:
            ValueError: If the kind is not supported
        """
        creator = self._service_registry.get(kind)
        if not creator:
            supported = ", ".join(self._service_registry.keys())
            raise ValueError(f"Unsupported kind: {kind}. Supported: {supported}")
        return creator
    
    def get_supported_kinds(self) -> list:
        """Get list of all supported service kinds."""
        return list(self._service_registry.keys())
    
    def register(self, kind: str, creator: Callable):
        """
        Register a new service kind and its creator function.
        Useful for dynamic registration of services.
        
        Args:
            kind: Service kind identifier
            creator: Function that creates the service
        """
        self._service_registry[kind] = creator

