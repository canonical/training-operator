#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
#

import logging

from charmed_kubeflow_chisme.exceptions import ErrorWithStatus, GenericCharmRuntimeError
from charmed_kubeflow_chisme.kubernetes import KubernetesResourceHandler
from charmed_kubeflow_chisme.lightkube.batch import delete_many
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from lightkube import ApiError
from lightkube.generic_resource import load_in_cluster_generic_resources
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, MaintenanceStatus, WaitingStatus

K8S_RESOURCE_FILES = [
    "src/templates/deployment.yaml.j2",
    "src/templates/rbac_manifests.yaml.j2",
    "src/templates/secret.yaml.j2",
]
CRD_RESOURCE_FILES = [
    "src/templates/crds_manifests.yaml.j2",
]
METRICS_PATH = "/metrics"
METRICS_PORT = "8080"

logger = logging.getLogger(__name__)


class TrainingOperatorCharm(CharmBase):
    """A Juju Charm for Training Operator"""

    def __init__(self, *args):
        super().__init__(*args)

        self.logger = logging.getLogger(__name__)
        self._image = self.config["training-operator-image"]
        self._name = self.model.app.name
        self._namespace = self.model.name
        self._lightkube_field_manager = "lightkube"
        self._context = {
            "namespace": self._namespace,
            "app_name": self._name,
            "training_operator_image": self._image,
            "metrics_port": METRICS_PORT,
        }

        self._k8s_resource_handler = None
        self._crd_resource_handler = None

        self.dashboard_provider = GrafanaDashboardProvider(self)

        self.framework.observe(self.on.upgrade_charm, self._on_upgrade)
        self.framework.observe(self.on.config_changed, self._on_event)
        self.framework.observe(self.on.leader_elected, self._on_event)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.remove, self._on_remove)

        # The target is the Service (applied with service.yamlj2) and the name has the following
        # format: app-name-workload.namespace.svc:metrics_port
        self.prometheus_provider = MetricsEndpointProvider(
            charm=self,
            relation_name="metrics-endpoint",
            jobs=[
                {
                    "metrics_path": METRICS_PATH,
                    "static_configs": [
                        {"targets": [f"{self._name}.{self._namespace}.svc:{METRICS_PORT}"]}
                    ],
                }
            ],
        )

    @property
    def k8s_resource_handler(self):
        """Update K8S with K8S resources."""
        if not self._k8s_resource_handler:
            self._k8s_resource_handler = KubernetesResourceHandler(
                field_manager=self._lightkube_field_manager,
                template_files=K8S_RESOURCE_FILES,
                context=self._context,
                logger=self.logger,
            )
        load_in_cluster_generic_resources(self._k8s_resource_handler.lightkube_client)
        return self._k8s_resource_handler

    @k8s_resource_handler.setter
    def k8s_resource_handler(self, handler: KubernetesResourceHandler):
        self._k8s_resource_handler = handler

    @property
    def crd_resource_handler(self):
        """Update K8S with CRD resources."""
        if not self._crd_resource_handler:
            self._crd_resource_handler = KubernetesResourceHandler(
                field_manager=self._lightkube_field_manager,
                template_files=CRD_RESOURCE_FILES,
                context=self._context,
                logger=self.logger,
            )
        load_in_cluster_generic_resources(self._crd_resource_handler.lightkube_client)
        return self._crd_resource_handler

    @crd_resource_handler.setter
    def crd_resource_handler(self, handler: KubernetesResourceHandler):
        self._crd_resource_handler = handler

    def _check_leader(self):
        """Check if this unit is a leader."""
        if not self.unit.is_leader():
            self.logger.info("Not a leader, skipping setup")
            raise ErrorWithStatus("Waiting for leadership", WaitingStatus)

    def _check_and_report_k8s_conflict(self, error):
        """Returns True if error status code is 409 (conflict), False otherwise."""
        if error.status.code == 409:
            self.logger.warning(f"Encountered a conflict: {str(error)}")
            return True
        return False

    def _apply_k8s_resources(self, force_conflicts: bool = False) -> None:
        """Applies K8S resources.

        Args:
            force_conflicts (bool): *(optional)* Will "force" apply requests causing conflicting
                                    fields to change ownership to the field manager used in this
                                    charm.
                                    NOTE: This will only be used if initial regular apply() fails.
        """
        self.unit.status = MaintenanceStatus("Creating K8S resources")
        try:
            self.k8s_resource_handler.apply()
        except ApiError as error:
            if self._check_and_report_k8s_conflict(error) and force_conflicts:
                # conflict detected when applying K8S resources
                # re-apply K8S resources with forced conflict resolution
                self.unit.status = MaintenanceStatus("Force applying K8S resources")
                self.logger.warning("Applying K8S resources with conflict resolution")
                self.k8s_resource_handler.apply(force=force_conflicts)
            else:
                raise GenericCharmRuntimeError("K8S resources creation failed") from error
        try:
            self.crd_resource_handler.apply()
        except ApiError as error:
            if self._check_and_report_k8s_conflict(error) and force_conflicts:
                # conflict detected when applying CRD resources
                # re-apply CRD resources with forced conflict resolution
                self.unit.status = MaintenanceStatus("Force applying CRD resources")
                self.logger.warning("Applying CRD resources with conflict resolution")
                self.crd_resource_handler.apply(force=force_conflicts)
            else:
                raise GenericCharmRuntimeError("CRD resources creation failed") from error
        self.model.unit.status = MaintenanceStatus("K8S resources created")

    # TODO: force_conflicts=True due to
    #  https://github.com/canonical/training-operator/issues/104
    #  Remove this if [this pr](https://github.com/canonical/charmed-kubeflow-chisme/pull/65)
    #  merges.
    def _on_event(self, _, force_conflicts: bool = True) -> None:
        """Perform all required actions the Charm.

        Args:
            force_conflicts (bool): Should only be used when need to resolved conflicts on K8S
                                    resources.
        """
        try:
            self._check_leader()
            self._apply_k8s_resources(force_conflicts=force_conflicts)
        except ErrorWithStatus as error:
            self.model.unit.status = error.status
            return

        self.model.unit.status = ActiveStatus()

    def _on_install(self, _):
        """Perform installation only actions."""
        # apply K8S resources to speed up deployment
        # TODO: force_conflicts=True due to
        #  https://github.com/canonical/training-operator/issues/104
        #  Remove this if [this pr](https://github.com/canonical/charmed-kubeflow-chisme/pull/65)
        #  merges.
        self._apply_k8s_resources(force_conflicts=True)

    def _on_upgrade(self, _):
        """Perform upgrade steps."""
        # force conflict resolution in K8S resources update
        #  TODO: Remove force_conflicts if
        #   [this pr](https://github.com/canonical/charmed-kubeflow-chisme/pull/65) merges.
        self._on_event(_, force_conflicts=True)

    def _on_remove(self, _):
        """Remove all resources."""
        self.unit.status = MaintenanceStatus("Removing K8S resources")
        k8s_resources_manifests = self.k8s_resource_handler.render_manifests()
        crd_resources_manifests = self.crd_resource_handler.render_manifests()
        try:
            delete_many(self.crd_resource_handler.lightkube_client, crd_resources_manifests)
            delete_many(self.k8s_resource_handler.lightkube_client, k8s_resources_manifests)
        except ApiError as error:
            # do not log/report when resources were not found
            if error.status.code != 404:
                self.logger.error(f"Failed to delete K8S resources, with error: {error}")
                raise error
        self.unit.status = MaintenanceStatus("K8S resources removed")


if __name__ == "__main__":
    main(TrainingOperatorCharm)
