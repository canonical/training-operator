import unittest
from unittest.mock import MagicMock, Mock, patch
import logging

from lightkube import codecs
from lightkube.core.exceptions import ApiError
from ops.model import ActiveStatus, BlockedStatus
from ops.testing import Harness

from charm import TrainingOperatorCharm

logger = logging.getLogger(__name__)


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


@patch("lightkube.core.client.GenericSyncClient", Mock)
class TestCharm(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(TrainingOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    # Event handlers
    @patch("charm.TrainingOperatorCharm._create_crds")
    @patch("charm.TrainingOperatorCharm._create_resource")
    def test_install_event(self, create_auth, create_crds):
        self.harness.charm.on.install.emit()

        # Ensure create_auth and create_crds
        # are called once
        create_auth.assert_called_once()
        create_crds.assert_called_once()

        # Check status is Active
        self.assertEqual(self.harness.charm.unit.status, ActiveStatus())

    @patch("charm.TrainingOperatorCharm._create_crds")
    @patch("charm.TrainingOperatorCharm._create_resource")
    @patch("charm.ApiError", _FakeApiError)
    def test_blocked_on_appierror_on_install_event(self, create_auth, create_crds):
        # Ensure the unit is in BlockedStatus
        # on exception when creating auth resources
        subtests = (
            (
                create_auth,
                "Test BlockedStatus when ApiError is raised trying to create auth resources",
            ),
            (
                create_crds,
                "Test BlockedStatus when ApiError is raised trying to create CRDS",
            ),
        )
        for create_type, subtest_description in subtests:
            with self.subTest(msg=subtest_description):
                create_type.side_effect = _FakeApiError()
                try:
                    self.harness.charm.on.install.emit()
                except ApiError:
                    self.assertEqual(
                        self.harness.charm.unit.status,
                        BlockedStatus(
                            f"Creating/patching resources failed with code"
                            f"{create_type.side_effect.response.code}."
                        ),
                    )

    @patch("charm.TrainingOperatorCharm._update_layer")
    @patch("charm.TrainingOperatorCharm._patch_resource")
    @patch("charm.ApiError", _FakeApiError)
    def test_config_changed_event(self, patch, update):
        self.harness.charm.on.config_changed.emit()

        # Ensure _update_layer is called once and
        # _patch_resource is called
        update.assert_called_once()
        patch.assert_called()

        # Ensure the unit is in BlockedStatus
        # on exception when patching auth resources
        patch.side_effect = _FakeApiError()
        try:
            self.harness.charm.on.config_changed.emit()
        except ApiError:
            self.assertEqual(
                self.harness.charm.unit.status,
                BlockedStatus(
                    f"Patching resources failed with code {patch.side_effect.response.code}."
                ),
            )

    @patch("charm.TrainingOperatorCharm._update_layer")
    def test_on_training_operator_pebble_ready(self, update):
        self.harness.container_pebble_ready("training-operator")

        # Check the layer gets created
        self.assertIsNotNone(
            self.harness.get_container_pebble_plan("training-operator")._services
        )

    # Helpers
    @patch("charm.Client.create")
    def test_create_resource(self, client: MagicMock):
        subtests = (
            (
                "auth",
                {"namespace": "unit-kubeflow", "app_name": "unit-training-operator"},
                "auth_manifests.yaml",
            ),
            ("crds", {}, "crds_manifests.yaml"),
        )
        for resource_type, context, template in subtests:
            with self.subTest(msg=f"Testing {resource_type} creation"):
                self.harness.charm._create_resource(
                    resource_type=resource_type, context=context
                )

                # Ensure client is called to create resources in src/*.yaml
                with open(f"./src/{template}") as f:
                    for resource in codecs.load_all_yaml(f, context):
                        client.assert_any_call(resource)

    @patch("charm.TrainingOperatorCharm._create_resource")
    @patch("charm.ApiError", _FakeApiError)
    def test_create_crds(self, create_resource):
        self.harness.charm._create_crds()
        create_resource.assert_called_once()

        # Check ApiError is raised
        create_resource.side_effect = _FakeApiError()
        with self.assertRaises(ApiError):
            self.harness.charm._create_crds()

        # TODO: Ensure 'CRD already present' message is logged on
        # ApiError for reason='AlreadyExists'
