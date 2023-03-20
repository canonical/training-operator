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

    # setup container networking simulation
    harness.set_can_connect("training-operator", True)

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
        harness.container_pebble_ready("training-operator")
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
        harness.add_oci_resource(
            "training-operator-image",
            {
                "registrypath": "ci-test",
                "username": "",
                "password": "",
            },
        )

        harness.begin_with_initial_hooks()
        harness.container_pebble_ready("training-operator")
        assert harness.charm.model.unit.status == ActiveStatus("")

    @patch("charm.TrainingOperatorCharm.k8s_resource_handler")
    @patch("charm.TrainingOperatorCharm.crd_resource_handler")
    def test_pebble_layer(
        self,
        _: MagicMock,  # k8s_resource_handler
        ___: MagicMock,  # crd_resource_handler
        harness: Harness,
    ):
        """Test creation of Pebble layer. Only testing specific items."""
        harness.set_leader(True)
        harness.set_model_name("test_kubeflow")
        harness.begin_with_initial_hooks()
        harness.container_pebble_ready("training-operator")
        pebble_plan = harness.get_container_pebble_plan("training-operator")
        assert pebble_plan
        assert pebble_plan._services
        pebble_plan_info = pebble_plan.to_dict()
        assert pebble_plan_info["services"]["training-operator"]["command"] == "/manager"
        test_env = pebble_plan_info["services"]["training-operator"]["environment"]
        assert 2 == len(test_env)
        assert "test_kubeflow" == test_env["MY_POD_NAMESPACE"]

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
    def test_on_remove_success(
        self,
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
    def test_on_remove_failure(
        self, delete_many: MagicMock, _: MagicMock, __: MagicMock, harness: Harness
    ):
        delete_many.side_effect = _FakeApiError()
        harness.begin()
        with pytest.raises(ApiError):
            harness.charm.on.remove.emit()
