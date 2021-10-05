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

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.on.training_operator_pebble_ready,
            self._on_training_operator_pebble_ready,
        )

    @property
    def _training_operator_layer(self) -> dict:
        """Property which returns a pre-configured Pebble layer.

        The Pbble layer contains information about how to start the manager service,
        and sets the relevant environment variables for the process to start successfully.

        Returns:
            dict: a dictionary representation of a Pebble layer

        """
        return {
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
                        # containerPort: 8080 is accessed by the monitoring service
                        # on port 8080. Leaving this for documentation purposes only.
                        "CONTAINER_PORT": int(self.model.config["container-port"]),
                        "MY_POD_NAMESPACE": self._namespace,
                        "MY_POD_NAME": self._name,
                    },
                }
            },
        }

    def _on_training_operator_pebble_ready(self, event):
        """
        Placeholder for pebble ready event
        """
        pass

    def _on_config_changed(self, event):
        """
        Placeholder for config changed event
        """
        pass

    def _on_install(self, event):
        """
        Placeholder for install event
        """
        pass


if __name__ == "__main__":
    main(TrainingOperatorCharm)
