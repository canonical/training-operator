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
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    def test_not_leader(
        self,
        _: MagicMock,  # k8s_resource_handler
        __: MagicMock,  # crd_resource_handler
        ___: MagicMock,  # training_runtimes_resource_handler
        harness: Harness,
    ):
        """Test not a leader scenario."""
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == WaitingStatus("Waiting for leadership")

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    def test_no_relation(
        self,
        _: MagicMock,  # k8s_resource_handler
        __: MagicMock,  # crd_resource_handler
        ___: MagicMock,  # training_runtimes_resource_handler
        harness: Harness,
    ):
        """Test no relation scenario."""
        harness.set_leader(True)

        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == ActiveStatus("")

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    def test_apply_k8s_resources_success(
        self,
        k8s_resource_handler: MagicMock,
        crd_resource_handler: MagicMock,
        training_runtimes_resource_handler: MagicMock,
        harness: Harness,
    ):
        """Test if K8S resource handler is executed as expected."""
        harness.begin()
        # passing any event to _apply_k8s_resources works
        harness.charm._apply_k8s_resources()
        crd_resource_handler.apply.assert_called()
        training_runtimes_resource_handler.apply.assert_called()
        k8s_resource_handler.apply.assert_called()
        assert isinstance(harness.charm.model.unit.status, MaintenanceStatus)

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.ApiError", _FakeApiError)
    def test_blocked_on_apierror_on_k8s_resource_handler(
        self, _: MagicMock, __: MagicMock, k8s_resource_handler: MagicMock, harness: Harness
    ):
        # Ensure the unit is in BlockedStatus
        # on exception when creating auth resources
        k8s_resource_handler.apply.side_effect = _FakeApiError(code=400, message="invalid name")

        harness.begin()
        harness.charm.on.install.emit()
        assert harness.charm.unit.status == BlockedStatus(
            f"K8s resources creation failed: "
            f"{k8s_resource_handler.apply.side_effect.response.message}"
        )

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.ApiError", _FakeApiError)
    def test_blocked_on_apierror_on_crd_resource_handler(
        self,
        _: MagicMock,
        crd_resource_handler: MagicMock,
        __: MagicMock,
        harness: Harness,
    ):
        # Ensure the unit is in BlockedStatus
        # on exception when creating auth resources
        crd_resource_handler.apply.side_effect = _FakeApiError(code=400, message="invalid name")

        harness.begin()
        harness.charm.on.install.emit()
        assert harness.charm.unit.status == BlockedStatus(
            f"CRD resources creation failed: "
            f"{crd_resource_handler.apply.side_effect.response.message}"
        )

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.ApiError", _FakeApiError)
    def test_blocked_on_apierror_on_training_runtimes_resource_handler(
        self,
        training_runtimes_resource_handler: MagicMock,
        _: MagicMock,
        __: MagicMock,
        harness: Harness,
    ):
        # Ensure the unit is in BlockedStatus
        # on exception when creating TrainingRuntime resources
        training_runtimes_resource_handler.apply.side_effect = _FakeApiError()

        harness.begin()
        harness.charm.on.install.emit()
        assert harness.charm.unit.status == BlockedStatus(
            f"TrainingRuntime resources creation failed: "
            f"{training_runtimes_resource_handler.apply.side_effect.response.message}"
        )

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.ApiError", _FakeApiError)
    def test_waiting_on_charm_pod_not_ready_on_training_runtimes_resource_handler(
        self,
        training_runtimes_resource_handler: MagicMock,
        _: MagicMock,
        __: MagicMock,
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
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.ApiError", _FakeApiError)
    def test_waiting_on_webhook_server_service_not_ready_on_training_runtimes_resource_handler(
        self,
        training_runtimes_resource_handler: MagicMock,
        _: MagicMock,
        __: MagicMock,
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
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.delete_many")
    def test_on_remove_success(
        self,
        delete_many: MagicMock,
        _: MagicMock,
        crd_resource_handler: MagicMock,
        k8s_resource_handler: MagicMock,
        harness: Harness,
    ):
        harness.begin()
        harness.charm.on.remove.emit()
        k8s_resource_handler.assert_has_calls([call.render_manifests()])
        crd_resource_handler.assert_has_calls([call.render_manifests()])
        delete_many.assert_called()

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    @patch("charm.TrainingOperatorCharm.training_runtimes_resource_handler")
    @patch("charm.delete_many")
    def test_on_remove_failure(
        self, delete_many: MagicMock, _: MagicMock, __: MagicMock, ___: MagicMock, harness: Harness
    ):
        delete_many.side_effect = _FakeApiError()
        harness.begin()
        with pytest.raises(ApiError):
            harness.charm.on.remove.emit()
