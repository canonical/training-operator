#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import traceback
from pathlib import Path

from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from lightkube import ApiError, Client, codecs
from lightkube.types import PatchType
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus
from ops.pebble import Layer

METRICS_PATH = "/metrics"
METRICS_PORT = "8080"

logger = logging.getLogger(__name__)


class TrainingOperatorCharm(CharmBase):
    """A Juju Charm for Training Operator"""

    def __init__(self, *args):
        super().__init__(*args)

        self.logger = logging.getLogger(__name__)

        self.prometheus_provider = MetricsEndpointProvider(
            charm=self,
            relation_name="metrics-endpoint",
            jobs=[
                {
                    "metrics_path": METRICS_PATH,
                    "static_configs": [{"targets": ["*:{}".format(METRICS_PORT)]}],
                }
            ],
        )

        self._name = self.model.app.name
        self._namespace = self.model.name
        self._manager_service = "manager"
        self._src_dir = Path(__file__).parent
        self._container = self.unit.get_container(self._name)
        self._resource_files = {
            "auth": "auth_manifests.yaml",
            "crds": "crds_manifests.yaml",
        }
        self._context = {"namespace": self._namespace, "app_name": self._name}

        self.dashboard_provider = GrafanaDashboardProvider(self)

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

    def _update_layer(self) -> None:
        """Updates the Pebble configuration layer if changed."""
        if not self._container.can_connect():
            self.unit.status = MaintenanceStatus("Waiting for pod startup to complete")
            return

        # Get current config
        current_layer = self._container.get_plan()
        # Create a new config layer
        new_layer = self._training_operator_layer
        if current_layer.services != new_layer.services:
            self._container.add_layer(self._manager_service, new_layer, combine=True)
            logging.info("Pebble plan updated with new configuration")
        self._container.restart(self._manager_service)

    def _create_resource(self, resource_type: str, context: dict = None) -> None:
        """Helper method to create Kubernetes resources."""
        client = Client()
        with open(Path(self._src_dir) / self._resource_files[resource_type]) as f:
            for obj in codecs.load_all_yaml(f, context=context):
                client.create(obj)

    def _patch_resource(self, resource_type: str, context: dict = None) -> None:
        """Helper method to patch Kubernetes resources."""
        client = Client()
        with open(Path(self._src_dir) / self._resource_files[resource_type]) as f:
            for obj in codecs.load_all_yaml(f, context=context):
                client.patch(type(obj), obj.metadata.name, obj, patch_type=PatchType.MERGE)

    def _create_crds(self) -> None:
        """Creates training-jobs CRDs.

        Raises:
            ApiError: if creating any of the CRDs fails.
        """
        try:
            self._create_resource(resource_type="crds")
        except ApiError as e:
            if e.status.reason == "AlreadyExists":
                logging.info(
                    f"{e.status.details.name} CRD already present. It will be used by the operator."
                )
            else:
                raise

    def _create_auth_resources(self) -> None:
        """Creates auth resources.

        Raises:
            ApiError: if creating any of the resources fails.
        """
        try:
            self._create_resource(resource_type="auth", context=self._context)
        except ApiError as e:
            if e.status.reason == "AlreadyExists":
                logging.info(
                    f"{e.status.details.name} auth resource already present. It will be reused."
                )
            else:
                raise

    def _on_install(self, event):
        """Event handler for InstallEvent."""

        # Update Pebble configuration layer if it has changed
        self._update_layer()

        # Patch/create Kubernetes resources
        try:
            self.unit.status = MaintenanceStatus("Creating auth resources")
            self._create_auth_resources()
            self.unit.status = MaintenanceStatus("Creating CRDs")
            self._create_crds()
        except ApiError as e:
            logging.error(traceback.format_exc())
            self.unit.status = BlockedStatus(
                f"Creating/patching resources failed with code {str(e.status.code)}."
            )
            if e.status.code == 403:
                logging.error(
                    "Received Forbidden (403) error when creating auth resources."
                    "This may be due to the charm lacking permissions to create"
                    "cluster-scoped resources."
                    "Charm must be deployed with --trust"
                )
                event.defer()
                return
        else:
            self.unit.status = ActiveStatus()

    def _on_config_changed(self, _):
        """Event handler for ConfigChangedEvent"""

        # Update Pebble configuration layer if it has changed
        self._update_layer()

        # Patch/create Kubernetes resources
        try:
            self.unit.status = MaintenanceStatus("Patching auth resources")
            self._patch_resource(resource_type="auth", context=self._context)
            self.unit.status = MaintenanceStatus("Patching CRDs")
            self._patch_resource(resource_type="crds")
        except ApiError as e:
            logging.error(traceback.format_exc())
            self.unit.status = BlockedStatus(
                f"Patching resources failed with code {str(e.status.code)}."
            )
        else:
            self.unit.status = ActiveStatus()

    def _on_training_operator_pebble_ready(self, _):
        """Event handler for on PebbleReadyEvent"""
        self._update_layer()


if __name__ == "__main__":
    main(TrainingOperatorCharm)
