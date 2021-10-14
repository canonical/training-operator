#!/usr/bin/env python3

import os
import logging
from pathlib import Path

from ops.main import main
from ops.pebble import Layer
from ops.charm import CharmBase
from ops.model import ActiveStatus, WaitingStatus, MaintenanceStatus

from lightkube import ApiError, Client, codecs
from lightkube.types import PatchType

logger = logging.getLogger(__name__)


class TrainingOperatorCharm(CharmBase):
    """A Juju Charm for Training Operator"""

    def __init__(self, *args):
        super().__init__(*args)

        self.logger = logging.getLogger(__name__)

        self._name = self.model.app.name
        self._namespace = self.model.name
        self._manager_service = "manager"
        self._src_dir = Path(__file__).parent

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.on.training_operator_pebble_ready,
            self._on_training_operator_pebble_ready,
        )

    @property
    def _training_operator_layer(self) -> Layer:
        """Returns a pre-configured Pebble layer."""

        layer_config = {
            "summary": "training-operator layer",
            "description": "pebble config layer for training-operator",
            "services": {
                self._manager_service: {
                    "override": "replace",
                    "summary": "entrypoint of the training-operator image",
                    # /manager is the entrypoint on Kubeflow's training-operator image
                    "command": "/manager",
                    "startup": "enabled",
                    "environment": {
                        "MY_POD_NAMESPACE": self._namespace,
                        "MY_POD_NAME": self._name,
                    },
                }
            },
        }
        return Layer(layer_config)

    def _on_training_operator_pebble_ready(self, _):
        """Placeholder for pebble ready event"""
        pass

    def _on_config_changed(self, _):
        """Event handler for config-changed events.

        On a config-changed event, a new Pebble configuration layer with the changes
        will be created and compared to the existing one; if they differ, the Pebble
        plan is updated and the manager service restarted.

        """
        container = self.unit.get_container(self._name)
        # Get current config
        current_layer = container.get_plan().services
        # Create a new config layer
        new_layer = self._training_operator_layer
        if container.can_connect():
            # Check if there are any changes
            if current_layer != new_layer.services:
                container.add_layer(self._manager_service, new_layer, combine=True)
                logging.info("Pebble plan updated with new configuration")
                container.restart(self._manager_service)
                logging.info("Restart training-operator")
            self.unit.status = ActiveStatus()
        else:
            self.unit.status = WaitingStatus("Waiting for Pebble in workload container")

    def _patch_auth_resources(self) -> None:
        """Create the Kubernetes auth resources created by Juju.

        Raises:
            ApiError: if creating any of the auth resources fails.
        """
        self.unit.status = MaintenanceStatus("Setting auth configuration")
        client = Client()
        context = {"namespace": self._namespace, "app_name": self._name}

        with open(Path(self._src_dir) / "auth_manifests.yaml") as f:
            for obj in codecs.load_all_yaml(f, context=context):
                try:
                    client.create(obj)
                except ApiError as e:
                    logging.error(
                        f"Creating {obj.metadata.name} failed: {e.status.reason}"
                    )
                    raise
        logging.info("Auth resources successfully created.")

    def _on_install(self, _):
        """Event handler for on-install events."""
        auth = self._patch_auth_resources()

        # Check resources were applied correctly
        if auth:
            self.unit.status = ActiveStatus()


if __name__ == "__main__":
    main(TrainingOperatorCharm)
