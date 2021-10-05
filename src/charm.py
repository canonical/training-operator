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
