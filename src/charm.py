#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
#

import logging
from pathlib import Path

import lightkube
import tenacity
import yaml
from charmed_kubeflow_chisme.exceptions import ErrorWithStatus, GenericCharmRuntimeError
from charmed_kubeflow_chisme.kubernetes import KubernetesResourceHandler
from charmed_kubeflow_chisme.lightkube.batch import delete_many
from charmed_service_mesh_helpers.models import (
    Action,
    AuthorizationPolicySpec,
    Rule,
    WorkloadSelector,
)
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.istio_beacon_k8s.v0.service_mesh import PolicyResourceManager, ServiceMeshConsumer
from charms.kubeflow_dashboard.v0.kubeflow_dashboard_links import (
    DashboardLink,
    KubeflowDashboardLinksRequirer,
)
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from lightkube import ApiError, Client
from lightkube.generic_resource import GenericNamespacedResource, load_in_cluster_generic_resources
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from lightkube_extensions.types import AuthorizationPolicy
from ops import main
from ops.charm import CharmBase
from ops.model import ActiveStatus, MaintenanceStatus, WaitingStatus

K8S_RESOURCE_FILES = [
    "src/templates/trainer-configmap_manifests.yaml.j2",
    "src/templates/trainer-role_bindings_manifests.yaml.j2",
    "src/templates/trainer-roles_manifests.yaml.j2",
    "src/templates/trainer-serviceaccount_manifests.yaml.j2",
    "src/templates/trainer-secret_manifests.yaml.j2",
    "src/templates/trainer-deployment_manifests.yaml.j2",
    "src/templates/trainer-validatingwebhookconfiguration_manifests.yaml.j2",
    "src/templates/trainer-service_manifests.yaml.j2",
    "src/templates/jobset-rbac_manifests.yaml.j2",
    "src/templates/jobset-secret_manifests.yaml.j2",
    "src/templates/jobset-configmap_manifests.yaml.j2",
    "src/templates/jobset-deployment_manifests.yaml.j2",
    "src/templates/jobset-validatingwebhookconfiguration_manifests.yaml.j2",
    "src/templates/jobset-mutatingwebhookconfiguration_manifests.yaml.j2",
    "src/templates/jobset-service_manifests.yaml.j2",
    "src/templates/lws-rbac_manifests.yaml.j2",
    "src/templates/lws-secret_manifests.yaml.j2",
    "src/templates/lws-configmap_manifests.yaml.j2",
    "src/templates/lws-deployment_manifests.yaml.j2",
    "src/templates/lws-validatingwebhookconfiguration_manifests.yaml.j2",
    "src/templates/lws-mutatingwebhookconfiguration_manifests.yaml.j2",
    "src/templates/lws-service_manifests.yaml.j2",
    "src/templates/lws-serviceaccount_manifests.yaml.j2",
]
CRD_RUNTIMES_RESOURCE_FILES = [
    "src/templates/trainer-crds_runtimes_manifests.yaml",
]
CRD_JOBSET_RESOURCE_FILES = [
    "src/templates/jobset-crds_manifests.yaml.j2",
]
CRD_LWS_RESOURCE_FILES = [
    "src/templates/lws-crds_manifests.yaml.j2",
]
CRD_TRAINJOB_RESOURCE_FILES = [
    "src/templates/trainer-crds_trainjob_manifests.yaml",
]
TRAINING_RUNTIMES_FILES = [
    "src/training_runtimes/deepspeed_distributed.yaml",
    "src/training_runtimes/mlx_distributed.yaml",
    "src/training_runtimes/mpi_distributed.yaml",
    "src/training_runtimes/torch_distributed.yaml",
]
METRICS_PATH = "/metrics"
METRICS_PORT = "8080"
WEBHOOK_PORT = "443"
WEBHOOK_TARGET_PORT = "9443"

SERVICE_MESH_RELATION_NAME = "service-mesh"

logger = logging.getLogger(__name__)


# For errors when a TrainJob exists while it shouldn't
class ObjectStillExistsError(Exception):
    """Exception for when a K8s object exists, while it should have been removed."""

    pass


class TrainingOperatorCharm(CharmBase):
    """A Juju Charm for Training Operator"""

    def __init__(self, *args):
        super().__init__(*args)

        self.logger = logging.getLogger(__name__)
        self._kf_trainer_image = self.config["kubeflow-trainer-image"]
        self._jobset_image = self.config["jobset-image"]
        self._lws_image = self.config["lws-image"]
        self._name = self.model.app.name
        self._namespace = self.model.name
        self._lightkube_field_manager = "lightkube"
        self._context = {
            "namespace": self._namespace,
            "app_name": self._name,
            "kubeflow_trainer_image": self._kf_trainer_image,
            "jobset_image": self._jobset_image,
            "lws_image": self._lws_image,
            "metrics_port": METRICS_PORT,
            "webhook_port": WEBHOOK_PORT,
            "webhook_target_port": WEBHOOK_TARGET_PORT,
        }

        self._k8s_resource_handler = None
        self._crd_resource_handler = None
        self._training_runtimes_resource_handler = None
        self._trainjob_resource_handler = None
        self._policy_resource_manager_client = None
        self._policy_resource_manager_instance = None

        self.dashboard_provider = GrafanaDashboardProvider(self)

        self.framework.observe(self.on.upgrade_charm, self._on_upgrade)
        self.framework.observe(self.on.config_changed, self._on_event)
        self.framework.observe(self.on.leader_elected, self._on_event)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.update_status, self._on_event)
        self.framework.observe(self.on.remove, self._on_remove)

        self.framework.observe(
            self.on[SERVICE_MESH_RELATION_NAME].relation_changed, self._on_event
        )
        self.framework.observe(
            self.on[SERVICE_MESH_RELATION_NAME].relation_broken,
            self._remove_authorization_policies,
        )

        # Add documentation link to the dashboard
        self.kubeflow_dashboard_sidebar = KubeflowDashboardLinksRequirer(
            charm=self,
            relation_name="dashboard-links",
            dashboard_links=[
                DashboardLink(
                    text="Kubeflow Trainer Documentation",
                    link="https://www.kubeflow.org/docs/components/trainer/",
                    desc="Documentation for Kubeflow Trainer",
                    location="documentation",
                ),
            ],
        )

        # The target is the Service (applied with service.yaml.j2) and the name has the following
        # format: app-name-controller-manager.namespace.svc:metrics_port
        self.prometheus_provider = MetricsEndpointProvider(
            charm=self,
            relation_name="metrics-endpoint",
            jobs=[
                {
                    "metrics_path": METRICS_PATH,
                    "static_configs": [
                        {
                            "targets": [
                                f"{self._name}-controller-manager.{self._namespace}.svc:{METRICS_PORT}"  # noqa E501
                            ]
                        }
                    ],
                }
            ],
        )

        self._mesh = ServiceMeshConsumer(
            self,
        )

        # Allow all policies needed to allow the K8s API to talk to the webhooks
        self._allow_all_policies = self.generate_allow_all_authorization_policies(
            app_name=self.app.name,
            namespace=self.model.name,
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
                template_files=CRD_RUNTIMES_RESOURCE_FILES
                + CRD_JOBSET_RESOURCE_FILES
                + CRD_LWS_RESOURCE_FILES,
                context=self._context,
                logger=self.logger,
            )
        load_in_cluster_generic_resources(self._crd_resource_handler.lightkube_client)
        return self._crd_resource_handler

    @crd_resource_handler.setter
    def crd_resource_handler(self, handler: KubernetesResourceHandler):
        self._crd_resource_handler = handler

    @property
    def training_runtimes_resource_handler(self):
        """Update K8S with TrainingRuntime resources."""
        if not self._training_runtimes_resource_handler:
            self._training_runtimes_resource_handler = KubernetesResourceHandler(
                field_manager=self._lightkube_field_manager,
                template_files=TRAINING_RUNTIMES_FILES,
                context=self._context,
                logger=self.logger,
            )
        load_in_cluster_generic_resources(
            self._training_runtimes_resource_handler.lightkube_client
        )
        return self._training_runtimes_resource_handler

    @training_runtimes_resource_handler.setter
    def training_runtimes_resource_handler(self, handler: KubernetesResourceHandler):
        self._training_runtimes_resource_handler = handler

    @property
    def trainjob_resource_handler(self):
        """Update K8S with TrainJob resources."""
        if not self._trainjob_resource_handler:
            self._trainjob_resource_handler = KubernetesResourceHandler(
                field_manager=self._lightkube_field_manager,
                template_files=CRD_TRAINJOB_RESOURCE_FILES,
                context=self._context,
                logger=self.logger,
            )
        load_in_cluster_generic_resources(self._trainjob_resource_handler.lightkube_client)
        return self._trainjob_resource_handler

    @trainjob_resource_handler.setter
    def trainjob_resource_handler(self, handler: KubernetesResourceHandler):
        self._trainjob_resource_handler = handler

    @property
    def policy_resource_manager_client(self) -> Client:
        """Return lightkube client for PolicyResourceManager."""
        if not self._policy_resource_manager_client:
            self._policy_resource_manager_client = Client(
                field_manager=f"{self.app.name}-{self.model.name}"
            )
        return self._policy_resource_manager_client

    @policy_resource_manager_client.setter
    def policy_resource_manager_client(self, client: Client):
        self._policy_resource_manager_client = client

    @property
    def _policy_resource_manager(self) -> PolicyResourceManager:
        """Create and return PolicyResourceManager, used to manage authorization policies."""
        if not self._policy_resource_manager_instance:
            self._policy_resource_manager_instance = PolicyResourceManager(
                charm=self,
                lightkube_client=self.policy_resource_manager_client,
                labels={
                    "app.kubernetes.io/instance": f"{self.app.name}-{self.model.name}",
                    "kubernetes-resource-handler-scope": f"{self.app.name}-allow-all",
                },
                logger=self.logger,
            )
        return self._policy_resource_manager_instance

    def generate_allow_all_authorization_policies(
        self, app_name: str, namespace: str
    ) -> list[GenericNamespacedResource]:
        """Return AuthorizationPolicy list for workload deployments.

        Args:
            app_name: name of the app to allow traffic to
            namespace: namespace of the app to allow traffic to

        Returns:
            List of three AuthorizationPolicy resources, one for each deployment
            (controller-manager, lws, jobset) since they all have WebhookConfiguration.
        """
        policies = []
        for component in ["manager", "lws", "jobset"]:
            policy = AuthorizationPolicy(
                metadata=ObjectMeta(
                    name=f"{app_name}-{component}-allow-all",
                    namespace=namespace,
                ),
                spec=AuthorizationPolicySpec(
                    selector=WorkloadSelector(
                        matchLabels={
                            "app.kubernetes.io/name": f"{app_name}-{component}",
                        },
                    ),
                    action=Action.allow,
                    rules=[Rule()],
                ).model_dump(by_alias=True, exclude_unset=True, exclude_none=True),
            )
            policies.append(policy)
        return policies

    def _reconcile_policy_resource_manager(self):
        """Reconcile authorization policies via PolicyResourceManager."""
        if self._mesh._relation:
            self._policy_resource_manager.reconcile(
                policies=[], mesh_type=self._mesh.mesh_type, raw_policies=self._allow_all_policies
            )

    def _remove_authorization_policies(self, _):
        """Remove authorization policies via PolicyResourceManager."""
        if self.unit.is_leader():
            self._policy_resource_manager.delete()

    def _check_leader(self):
        """Check if this unit is a leader."""
        if not self.unit.is_leader():
            self.logger.info("Not a leader, skipping setup")
            raise ErrorWithStatus("Waiting for leadership", WaitingStatus)

    def _apply_k8s_resources(self) -> None:
        """Applies K8S resources."""
        self.unit.status = MaintenanceStatus("Creating K8S resources")
        try:
            self.crd_resource_handler.apply()
            self.trainjob_resource_handler.apply()
        except ApiError as error:
            self.logger.warning("Unexpected ApiError happened: %s", error)
            raise GenericCharmRuntimeError(
                f"CRD resources creation failed: {error.status.message}",
            )
        try:
            self.k8s_resource_handler.apply()
        except ApiError as error:
            self.logger.warning("Unexpected ApiError happened: %s", error)
            raise GenericCharmRuntimeError(
                f"K8s resources creation failed: {error.status.message}",
            )
        try:
            self.training_runtimes_resource_handler.apply()
        except ApiError as error:
            if error.status.code == 500 and "connect: " in error.status.message:
                self.logger.warning("Failed to create TrainingRuntimes: %s", error.status.message)
                msg = "Charm Pod is not ready yet. Will apply TrainingRuntimes later."
                self.logger.info(msg)
                self.model.unit.status = MaintenanceStatus(msg)
                raise ErrorWithStatus(msg, MaintenanceStatus)
                # If the Endpoint for the webhook server Service is not yet created
                # then K8s will drop request to svc with message "no endpoints available".
                # The Endpoint gets created automatically by the control plane shortly
                # after the Service is created. Drop the traffic and set the status to
                # `MaintenanceStatus` expecting the error to be resolved in the future hooks.
            elif "no endpoints available" in error.status.message:
                self.logger.warning("Failed to create TrainingRuntimes: %s", error.status.message)
                msg = "Webhook Server Service endpoints not ready. Will apply ClusterServingRuntimes later."  # noqa E501
                self.logger.info(msg)
                self.model.unit.status = MaintenanceStatus(msg)
                raise ErrorWithStatus(msg, MaintenanceStatus)
            else:
                self.logger.warning("Unexpected ApiError happened: %s", error)
                raise GenericCharmRuntimeError(
                    f"TrainingRuntime resources creation failed: {error.status.message}",
                )

        self.model.unit.status = MaintenanceStatus("K8S resources created")

    def _on_event(self, _) -> None:
        """Perform all required actions the Charm."""

        try:
            self._check_leader()
            self._reconcile_policy_resource_manager()
            self._apply_k8s_resources()
        except ErrorWithStatus as error:
            self.model.unit.status = error.status
            return

        self.model.unit.status = ActiveStatus()

    def _on_install(self, _):
        """Perform installation only actions."""
        # apply K8S resources to speed up deployment
        try:
            self._apply_k8s_resources()
        except ErrorWithStatus as error:
            self.model.unit.status = error.status
            return

    def _on_upgrade(self, _):
        """Perform upgrade steps."""
        self._on_event(_)

    def _on_remove(self, _):
        """Remove all resources."""
        self.unit.status = MaintenanceStatus("Removing K8S resources")
        trainjob_resources_manifests = self.trainjob_resource_handler.render_manifests()
        k8s_resources_manifests = self.k8s_resource_handler.render_manifests()
        crd_resources_manifests = self.crd_resource_handler.render_manifests()
        self._remove_authorization_policies(_)
        try:
            delete_many(
                self.trainjob_resource_handler.lightkube_client, trainjob_resources_manifests
            )
            for trainjob_crd in _extract_crds_names(CRD_TRAINJOB_RESOURCE_FILES):
                self.ensure_crd_is_deleted(
                    self.trainjob_resource_handler.lightkube_client, trainjob_crd
                )
            delete_many(self.crd_resource_handler.lightkube_client, crd_resources_manifests)
            for runtime_crd in _extract_crds_names(CRD_RUNTIMES_RESOURCE_FILES):
                self.ensure_crd_is_deleted(self.crd_resource_handler.lightkube_client, runtime_crd)
            delete_many(self.k8s_resource_handler.lightkube_client, k8s_resources_manifests)
        except ApiError as error:
            # do not log/report when resources were not found
            if error.status.code != 404:
                self.logger.error(f"Failed to delete K8S resources, with error: {error}")
                raise error
        self.unit.status = MaintenanceStatus("K8S resources removed")

    @tenacity.retry(stop=tenacity.stop_after_delay(300), wait=tenacity.wait_fixed(5), reraise=True)
    def ensure_crd_is_deleted(self, client: lightkube.Client, crd_name: str):
        """Check if the CRD doesn't exist with retries.

        The function will keep retrying until the CRD is deleted, and handle the
        404 error once it gets deleted.

        Args:
            crd_name: The CRD to be checked if it is deleted.
            client: The lightkube client to use for talking to K8s.

        Raises:
            ApiError: From lightkube, if there was an error aside from 404.
            ObjectStillExistsError: If the Profile's namespace was not deleted after retries.
        """
        self.logger.info("Checking if CRD exists: %s", crd_name)
        try:
            client.get(CustomResourceDefinition, name=crd_name)
            self.logger.info('CRD "%s" exists, retrying...', crd_name)
            raise ObjectStillExistsError("CRD %s is not deleted.", crd_name)
        except ApiError as e:
            if e.status.code == 404:
                self.logger.info('CRD "%s" does not exist!', crd_name)
                return
            else:
                # Raise any other error
                raise


def _extract_crds_names(manifest_files: list[str]):
    manifests = "".join([Path(manifest).read_text() for manifest in manifest_files])
    crds_yaml = yaml.safe_load_all(manifests)
    crds_names = []
    for crd in crds_yaml:
        crds_names.append(crd.get("metadata").get("name"))
    return crds_names


if __name__ == "__main__":
    main(TrainingOperatorCharm)
