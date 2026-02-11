# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import MagicMock, call, patch

import pytest
from charmed_kubeflow_chisme.exceptions import GenericCharmRuntimeError
from lightkube.core.exceptions import ApiError
from ops import ErrorStatus
from ops.model import ActiveStatus, MaintenanceStatus, WaitingStatus
from ops.testing import Harness

from charm import TrainingOperatorCharm


class _FakeResponse:
    """Used to fake an httpx response during testing only."""

    def __init__(self, code, message=""):
        self.code = code
        self.name = ""
        self.message = message

    def json(self):
        reason = ""
        if self.code == 409:
            reason = "AlreadyExists"
        return {
            "apiVersion": 1,
            "code": self.code,
            "message": self.message,
            "reason": reason,
        }


class _FakeApiError(ApiError):
    """Used to simulate an ApiError during testing."""

    def __init__(self, code: int = 400, message: str = ""):
        super().__init__(response=_FakeResponse(code, message))


@pytest.fixture(scope="function")
def harness() -> Harness:
    """Create and return Harness for testing."""
    harness = Harness(TrainingOperatorCharm)

    return harness


class TestCharm:
    """Test class for TrainingOperatorCharm."""

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    def test_not_leader(
        self,
        _: MagicMock,  # k8s_resource_handler
        __: MagicMock,  # crd_resource_handler
        ___: MagicMock,  # trainjob_resource_handler
        ____: MagicMock,  # training_runtimes_resource_handler
        harness: Harness,
    ):
        """Test not a leader scenario."""
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == WaitingStatus("Waiting for leadership")

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    def test_no_relation(
        self,
        _: MagicMock,  # k8s_resource_handler
        __: MagicMock,  # crd_resource_handler
        ___: MagicMock,  # trainjob_resource_handler
        ____: MagicMock,  # training_runtimes_resource_handler
        harness: Harness,
    ):
        """Test no relation scenario."""
        harness.set_leader(True)

        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == ActiveStatus("")

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    def test_apply_k8s_resources_success(
        self,
        k8s_resource_handler: MagicMock,
        crd_resource_handler: MagicMock,
        trainjob_resource_handler: MagicMock,
        training_runtimes_resource_handler: MagicMock,
        harness: Harness,
    ):
        """Test if K8S resource handler is executed as expected."""
        harness.begin()
        # passing any event to _apply_k8s_resources works
        harness.charm._apply_k8s_resources()
        crd_resource_handler.apply.assert_called()
        trainjob_resource_handler.apply.assert_called()
        training_runtimes_resource_handler.apply.assert_called()
        k8s_resource_handler.apply.assert_called()
        assert isinstance(harness.charm.model.unit.status, MaintenanceStatus)

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.ApiError", _FakeApiError)
    def test_error_on_apierror_on_k8s_resource_handler(
        self,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        k8s_resource_handler: MagicMock,
        harness: Harness,
    ):
        # Ensure the unit is in BlockedStatus
        # on exception when creating K8s resources
        k8s_resource_handler.apply.side_effect = _FakeApiError(code=400, message="invalid name")

        harness.begin()
        with pytest.raises(GenericCharmRuntimeError):
            harness.charm.on.install.emit()
            assert harness.charm.unit.status == ErrorStatus()

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.ApiError", _FakeApiError)
    def test_blocked_on_apierror_on_crd_resource_handler(
        self,
        _: MagicMock,
        crd_resource_handler: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        harness: Harness,
    ):
        # Ensure the unit is in BlockedStatus
        # on exception when creating CRD resources
        crd_resource_handler.apply.side_effect = _FakeApiError(code=400, message="invalid name")

        harness.begin()
        with pytest.raises(GenericCharmRuntimeError):
            harness.charm.on.install.emit()
            assert harness.charm.unit.status == ErrorStatus()

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.ApiError", _FakeApiError)
    def test_blocked_on_apierror_on_trainjob_resource_handler(
        self,
        _: MagicMock,
        __: MagicMock,
        trainjob_resource_handler: MagicMock,
        ___: MagicMock,
        harness: Harness,
    ):
        # Ensure the unit is in BlockedStatus
        # on exception when creating TrainJob CRD resources
        trainjob_resource_handler.apply.side_effect = _FakeApiError(
            code=400, message="invalid name"
        )

        harness.begin()
        with pytest.raises(GenericCharmRuntimeError):
            harness.charm.on.install.emit()
            assert harness.charm.unit.status == ErrorStatus()

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.ApiError", _FakeApiError)
    def test_blocked_on_apierror_on_training_runtimes_resource_handler(
        self,
        training_runtimes_resource_handler: MagicMock,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        harness: Harness,
    ):
        # Ensure the unit is in BlockedStatus
        # on exception when creating TrainingRuntime resources
        training_runtimes_resource_handler.apply.side_effect = _FakeApiError()

        harness.begin()
        with pytest.raises(GenericCharmRuntimeError):
            harness.charm.on.install.emit()
            assert harness.charm.unit.status == ErrorStatus()

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.ApiError", _FakeApiError)
    def test_waiting_on_charm_pod_not_ready_on_training_runtimes_resource_handler(
        self,
        training_runtimes_resource_handler: MagicMock,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        harness: Harness,
    ):
        # Ensure the unit is in MaintenanceStatus with correct message
        # on 500 exception due to connect failure when creating TrainingRuntime resources
        training_runtimes_resource_handler.apply.side_effect = _FakeApiError(
            code=500, message="connect: failed"
        )
        harness.begin()
        harness.charm.on.install.emit()
        assert harness.charm.model.unit.status == MaintenanceStatus(
            "Charm Pod is not ready yet. Will apply TrainingRuntimes later."
        )

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.ApiError", _FakeApiError)
    def test_waiting_on_webhook_server_service_not_ready_on_training_runtimes_resource_handler(
        self,
        training_runtimes_resource_handler: MagicMock,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        harness: Harness,
    ):
        # Ensure the unit is in MaintenanceStatus with correct message
        # on 500 exception due to connect failure when creating TrainingRuntime resources
        training_runtimes_resource_handler.apply.side_effect = _FakeApiError(
            code=500, message="no endpoints available"
        )
        harness.begin()
        harness.charm.on.install.emit()
        assert harness.charm.model.unit.status == MaintenanceStatus(
            "Webhook Server Service endpoints not ready. Will apply ClusterServingRuntimes later."  # noqa E501
        )

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.TrainingOperatorCharm.ensure_crd_is_deleted")
    @patch("charm.delete_many")
    @patch("charm.TrainingOperatorCharm.policy_resource_manager_client")
    @patch("charm.PolicyResourceManager")
    @patch("charm.ServiceMeshConsumer")
    def test_on_remove_success(
        self,
        service_mesh_consumer_cls: MagicMock,
        policy_resource_manager_cls: MagicMock,
        policy_client: MagicMock,
        delete_many: MagicMock,
        _: MagicMock,
        __: MagicMock,
        trainjob_resource_handler: MagicMock,
        crd_resource_handler: MagicMock,
        k8s_resource_handler: MagicMock,
        harness: Harness,
    ):
        harness.begin()
        harness.charm.on.remove.emit()
        trainjob_resource_handler.assert_has_calls([call.render_manifests()])
        k8s_resource_handler.assert_has_calls([call.render_manifests()])
        crd_resource_handler.assert_has_calls([call.render_manifests()])
        delete_many.assert_called()
        policy_resource_manager_cls.return_value.delete.assert_called_once()

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.delete_many")
    @patch("charm.TrainingOperatorCharm.policy_resource_manager_client")
    @patch("charm.PolicyResourceManager")
    @patch("charm.ServiceMeshConsumer")
    def test_on_remove_failure(
        self,
        service_mesh_consumer_cls: MagicMock,
        _policy_resource_manager_cls: MagicMock,
        _policy_client: MagicMock,
        delete_many: MagicMock,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        ____: MagicMock,
        harness: Harness,
    ):
        delete_many.side_effect = _FakeApiError()
        harness.begin()
        with pytest.raises(ApiError):
            harness.charm.on.remove.emit()

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.TrainingOperatorCharm.policy_resource_manager_client")
    @patch("charm.PolicyResourceManager")
    @patch("charm.ServiceMeshConsumer")
    def test_reconcile_policy_resource_manager_with_mesh(
        self,
        service_mesh_consumer_cls: MagicMock,
        policy_resource_manager_cls: MagicMock,
        policy_client: MagicMock,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        ____: MagicMock,
        harness: Harness,
    ):
        harness.set_leader(True)
        harness.begin()

        # Add a service mesh relation
        rel_id = harness.add_relation("service-mesh", "istio")
        harness.add_relation_unit(rel_id, "istio/0")

        # Mock the _mesh._relation to be truthy and mock mesh_type
        mock_relation = MagicMock()
        with patch.object(harness.charm._mesh, "_relation", mock_relation), patch.object(
            harness.charm._mesh, "mesh_type", "ambient"
        ):
            # Call reconcile
            harness.charm._reconcile_policy_resource_manager()

        # Verify reconcile was called with the correct policies
        policy_resource_manager_cls.return_value.reconcile.assert_called_once_with(
            policies=[],
            mesh_type="ambient",
            raw_policies=harness.charm._allow_all_policies,
        )

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.TrainingOperatorCharm.policy_resource_manager_client")
    @patch("charm.PolicyResourceManager")
    @patch("charm.ServiceMeshConsumer")
    def test_reconcile_policy_resource_manager_without_mesh(
        self,
        service_mesh_consumer_cls: MagicMock,
        policy_resource_manager_cls: MagicMock,
        policy_client: MagicMock,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        ____: MagicMock,
        harness: Harness,
    ):
        # Configure mock to simulate no mesh relation
        service_mesh_consumer_cls.return_value._relation = None

        harness.set_leader(True)
        harness.begin()

        # Call reconcile without mesh relation
        harness.charm._reconcile_policy_resource_manager()

        # Verify reconcile was not called
        policy_resource_manager_cls.return_value.reconcile.assert_not_called()

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.TrainingOperatorCharm.ensure_crd_is_deleted")
    @patch("charm.delete_many")
    @patch("charm.TrainingOperatorCharm.policy_resource_manager_client")
    @patch("charm.PolicyResourceManager")
    @patch("charm.ServiceMeshConsumer")
    def test_on_remove_calls_remove_authorization_policies(
        self,
        service_mesh_consumer_cls: MagicMock,
        policy_resource_manager_cls: MagicMock,
        policy_client: MagicMock,
        delete_many: MagicMock,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        ____: MagicMock,
        _____: MagicMock,
        harness: Harness,
    ):
        harness.begin()
        harness.charm.on.remove.emit()

        # Verify _remove_authorization_policies was called (via delete())
        policy_resource_manager_cls.return_value.delete.assert_called_once()

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.trainjob_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.TrainingOperatorCharm.policy_resource_manager_client")
    @patch("charm.PolicyResourceManager")
    @patch("charm.ServiceMeshConsumer")
    def test_service_mesh_relation_broken(
        self,
        service_mesh_consumer_cls: MagicMock,
        policy_resource_manager_cls: MagicMock,
        policy_client: MagicMock,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        ____: MagicMock,
        harness: Harness,
    ):
        harness.set_leader(True)
        harness.begin()

        # Add a service mesh relation
        rel_id = harness.add_relation("service-mesh", "istio")
        harness.add_relation_unit(rel_id, "istio/0")

        # Remove the relation to trigger relation_broken
        harness.remove_relation(rel_id)

        # Verify delete was called
        policy_resource_manager_cls.return_value.delete.assert_called_once()
