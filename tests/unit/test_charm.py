# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import MagicMock, call, patch

import pytest
from lightkube.core.exceptions import ApiError
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.testing import Harness

from charm import TrainingOperatorCharm


class _FakeResponse:
    """Used to fake an httpx response during testing only."""

    def __init__(self, code):
        self.code = code
        self.name = ""

    def json(self):
        reason = ""
        if self.code == 409:
            reason = "AlreadyExists"
        return {
            "apiVersion": 1,
            "code": self.code,
            "message": "broken",
            "reason": reason,
        }


class _FakeApiError(ApiError):
    """Used to simulate an ApiError during testing."""

    def __init__(self, code=400):
        super().__init__(response=_FakeResponse(code))


@pytest.fixture(scope="function")
def harness() -> Harness:
    """Create and return Harness for testing."""
    harness = Harness(TrainingOperatorCharm)

    return harness


class TestCharm:
    """Test class for TrainingOperatorCharm."""

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    def test_not_leader(
        self,
        _: MagicMock,  # k8s_resource_handler
        ___: MagicMock,  # crd_resource_handler
        harness: Harness,
    ):
        """Test not a leader scenario."""
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == WaitingStatus("Waiting for leadership")

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    def test_no_relation(
        self,
        _: MagicMock,  # k8s_resource_handler
        ___: MagicMock,  # crd_resource_handler
        harness: Harness,
    ):
        """Test no relation scenario."""
        harness.set_leader(True)

        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == ActiveStatus("")

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    def test_apply_k8s_resources_success(
        self,
        k8s_resource_handler: MagicMock,
        crd_resource_handler: MagicMock,
        harness: Harness,
    ):
        """Test if K8S resource handler is executed as expected."""
        harness.begin()
        harness.charm._apply_k8s_resources()
        crd_resource_handler.apply.assert_called()
        k8s_resource_handler.apply.assert_called()
        assert isinstance(harness.charm.model.unit.status, MaintenanceStatus)

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.ApiError", _FakeApiError)
    def test_blocked_on_appierror_on_k8s_resource_handler(
        self, k8s_resource_handler: MagicMock, _: MagicMock, harness: Harness
    ):
        # Ensure the unit is in BlockedStatus
        # on exception when creating auth resources
        k8s_resource_handler.side_effect = _FakeApiError()

        harness.begin()
        try:
            harness.charm.on.install.emit()
        except ApiError:
            self.assertEqual(
                harness.charm.unit.status,
                BlockedStatus(
                    f"Creating/patching resources failed with code"
                    f"{k8s_resource_handler.side_effect.response.code}."
                ),
            )

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.ApiError", _FakeApiError)
    def test_blocked_on_appierror_on_crd_resource_handler(
        self,
        k8s_resource_handler: MagicMock,
        crd_resource_handler: MagicMock,
        harness: Harness,
    ):
        # Ensure the unit is in BlockedStatus
        # on exception when creating auth resources
        crd_resource_handler.side_effect = _FakeApiError()

        harness.begin()
        try:
            harness.charm.on.install.emit()
        except ApiError:
            self.assertEqual(
                harness.charm.unit.status,
                BlockedStatus(
                    f"Creating/patching resources failed with code"
                    f"{crd_resource_handler.side_effect.response.code}."
                ),
            )

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.delete_many")
    @patch("charm.Client")
    def test_on_remove_success(
        self,
        _: MagicMock,  # Client
        delete_many: MagicMock,
        k8s_resource_handler: MagicMock,
        crd_resource_handler: MagicMock,
        harness: Harness,
    ):
        harness.begin()
        harness.charm.on.remove.emit()
        k8s_resource_handler.assert_has_calls([call.render_manifests()])
        crd_resource_handler.assert_has_calls([call.render_manifests()])
        delete_many.assert_called()

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.delete_many")
    @patch("charm.Client")
    def test_on_remove_failure(
        self,
        _: MagicMock,  # Client
        delete_many: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        harness: Harness,
    ):
        delete_many.side_effect = _FakeApiError()
        harness.begin()
        with pytest.raises(ApiError):
            harness.charm.on.remove.emit()

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.ServiceMeshConsumer")
    @patch("charm.Client")
    @patch("charm.PolicyResourceManager")
    def test_reconcile_policy_resource_manager_with_mesh(
        self,
        mock_policy_manager_class: MagicMock,
        mock_client: MagicMock,
        mock_service_mesh: MagicMock,
        crd_resource_handler: MagicMock,
        k8s_resource_handler: MagicMock,
        harness: Harness,
    ):
        """Test _reconcile_policy_resource_manager when service-mesh relation is present."""
        # Mock _relation property to indicate a relation exists
        mock_mesh_instance = mock_service_mesh.return_value
        mock_mesh_instance._relation = MagicMock()  # Relation exists
        mock_mesh_instance.mesh_type = "istio"

        harness.begin()
        harness.set_leader(True)

        # Mock the policy resource manager instance
        mock_policy_manager = mock_policy_manager_class.return_value

        harness.charm._reconcile_policy_resource_manager()

        # Verify reconcile was called with correct parameters
        mock_policy_manager.reconcile.assert_called_with(
            policies=[], mesh_type="istio", raw_policies=[harness.charm._allow_all_policy]
        )

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.ServiceMeshConsumer")
    @patch("charm.Client")
    @patch("charm.PolicyResourceManager")
    def test_reconcile_policy_resource_manager_without_mesh(
        self,
        mock_policy_manager_class: MagicMock,
        mock_client: MagicMock,
        mock_service_mesh: MagicMock,
        crd_resource_handler: MagicMock,
        k8s_resource_handler: MagicMock,
        harness: Harness,
    ):
        """Test _reconcile_policy_resource_manager when service-mesh relation is not present."""
        # Mock _relation property to return None (no relation established)
        mock_mesh_instance = mock_service_mesh.return_value
        mock_mesh_instance._relation = None

        harness.begin()
        harness.set_leader(True)

        # Mock the policy resource manager instance
        mock_policy_manager = mock_policy_manager_class.return_value

        harness.charm._reconcile_policy_resource_manager()

        # Verify reconcile was NOT called when there's no service-mesh relation
        mock_policy_manager.reconcile.assert_not_called()

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.ServiceMeshConsumer")
    @patch("charm.Client")
    @patch("charm.PolicyResourceManager")
    def test_on_remove_calls_remove_authorization_policies(
        self,
        mock_policy_manager_class: MagicMock,
        mock_client: MagicMock,
        mock_service_mesh: MagicMock,
        crd_resource_handler: MagicMock,
        k8s_resource_handler: MagicMock,
        harness: Harness,
    ):
        """Test that _on_remove calls _remove_authorization_policies."""
        harness.begin()
        harness.set_leader(True)

        # Mock render_manifests to return empty list
        k8s_resource_handler.render_manifests.return_value = []
        crd_resource_handler.render_manifests.return_value = []

        # Mock the policy resource manager instance
        mock_policy_manager = mock_policy_manager_class.return_value

        harness.charm._on_remove(None)

        # Verify _remove_authorization_policies was called (which calls delete)
        mock_policy_manager.delete.assert_called()

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.ServiceMeshConsumer")
    @patch("charm.Client")
    @patch("charm.PolicyResourceManager")
    def test_service_mesh_relation_broken(
        self,
        mock_policy_manager_class: MagicMock,
        mock_client: MagicMock,
        mock_service_mesh: MagicMock,
        crd_resource_handler: MagicMock,
        k8s_resource_handler: MagicMock,
        harness: Harness,
    ):
        """Test that service-mesh relation broken event removes authorization policies."""
        harness.begin()

        # Mock the policy resource manager instance
        mock_policy_manager = mock_policy_manager_class.return_value

        # Add a service-mesh relation
        relation_id = harness.add_relation("service-mesh", "istio-beacon-k8s")
        harness.add_relation_unit(relation_id, "istio-beacon-k8s/0")

        # Break the relation
        harness.remove_relation(relation_id)

        # Verify that delete was called when relation was broken
        mock_policy_manager.delete.assert_called()
