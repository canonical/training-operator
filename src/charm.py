#!/usr/bin/env python3

import logging

from ops.main import main
from ops.pebble import Layer
from ops.charm import CharmBase
from ops.model import ActiveStatus, WaitingStatus, MaintenanceStatus

logger = logging.getLogger(__name__)


class TrainingOperatorCharm(CharmBase):
    """A Juju Charm for Training Operator"""

    def __init__(self, *args):
        super().__init__(*args)

        self.logger = logging.getLogger(__name__)

        self._name = self.model.app.name
        self._namespace = self.model.name
        self._manager_service = "manager"

        self.framework.observe(self.on.install, self._on_config_changed)
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


    def _on_install(self, _):
        """Placeholder for install event"""
        pass


if __name__ == "__main__":
    main(TrainingOperatorCharm)
