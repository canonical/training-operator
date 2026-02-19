# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import glob
import logging
import time
from pathlib import Path

import lightkube
import pytest
import yaml
from lightkube import codecs
from lightkube.generic_resource import create_global_resource
from lightkube.resources.core_v1 import ServiceAccount
from lightkube.resources.rbac_authorization_v1 import RoleBinding
from pytest_operator.plugin import OpsTest
from tenacity import (
    RetryError,
    Retrying,
    before_sleep_log,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
)

basedir = Path("./").absolute()
PROFILE_NAMESPACE = "profile-example"
PROFILE_NAME = "profile-example"
PROFILE_FILE_PATH = basedir / "tests/integration/profile.yaml"
PROFILE_FILE = yaml.safe_load(PROFILE_FILE_PATH.read_text())
APP_NAME = "training-operator"

KUBEFLOW_ROLES = "kubeflow-roles"
KUBEFLOW_ROLES_CHANNEL = "1.10/stable"
KUBEFLOW_ROLES_TRUST = True
KUBEFLOW_PROFILES = "kubeflow-profiles"
KUBEFLOW_PROFILES_CHANNEL = "1.10/stable"
KUBEFLOW_PROFILES_TRUST = True
ISTIO_PILOT_NAME = "istio-pilot"
ISTIO_PILOT_CHANNEL = "latest/edge"
ISTIO_PILOT_TRUST = True

log = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, request: pytest.FixtureRequest):
    """Build the charm and deploy it with trust=True.

    Assert on the unit status.
    """
    entity_url = (
        await ops_test.build_charm(".")
        if not (entity_url := request.config.getoption("--charm-path"))
        else Path(entity_url).resolve()
    )
    await ops_test.model.deploy(entity_url, application_name=APP_NAME, trust=True)

    # Deploy kubeflow-roles and kubeflow-profiles to create a Profile
    await ops_test.model.deploy(
        entity_url=KUBEFLOW_ROLES,
        channel=KUBEFLOW_ROLES_CHANNEL,
        trust=KUBEFLOW_ROLES_TRUST,
    )
    await ops_test.model.deploy(
        entity_url=KUBEFLOW_PROFILES,
        channel=KUBEFLOW_PROFILES_CHANNEL,
        trust=KUBEFLOW_PROFILES_TRUST,
    )

    # The profile controller needs AuthorizationPolicies to create Profiles
    # Let's just deploy istio-pilot to provide the k8s cluster with this CRD
    await ops_test.model.deploy(
        entity_url=ISTIO_PILOT_NAME,
        channel=ISTIO_PILOT_CHANNEL,
        trust=ISTIO_PILOT_TRUST,
    )

    await ops_test.model.wait_for_idle(
        status="active", raise_on_blocked=True, raise_on_error=True, timeout=60 * 10
    )


@pytest.mark.parametrize("example", glob.glob("examples/*.yaml"))
@pytest.mark.abort_on_fail
async def test_authorization_for_creating_resources(
    example, ops_test: OpsTest, lightkube_client, apply_profile
):
    """Assert a *Job can be created by a user in the user namespace."""
    # Set up for creating an object of kind *Job
    job_yaml = yaml.safe_load(Path(example).read_text())
    training_job = job_yaml["kind"]
    log.info(f"Checking `kubectl can-i create` for {training_job}")
    _, stdout, _ = await ops_test.run(
        "kubectl",
        "auth",
        "can-i",
        "create",
        f"{training_job}",
        f"--as=system:serviceaccount:{PROFILE_NAMESPACE}:default-editor",
        f"--namespace={PROFILE_NAMESPACE}",
        check=True,
        fail_msg="Failed to execute kubectl auth",
    )
    assert stdout.strip() == "yes"


def apply_manifests(lightkube_client: lightkube.Client, yaml_file_path: Path):
    """Apply resources using manifest files and return the applied object.

    Args:
        lightkube_client (lightkube.Client): an instance of lightkube.Client to
            use for applying resources.
        yaml_file_path (Path): the path to the resource yaml file.

    Returns:
        A namespaced or global lightkube resource (obj).
    """
    read_yaml = yaml_file_path.read_text()
    yaml_loaded = codecs.load_all_yaml(read_yaml)
    for obj in yaml_loaded:
        lightkube_client.apply(
            obj=obj,
            name=obj.metadata.name,
        )
    return obj


@pytest.fixture(scope="module")
def lightkube_client() -> lightkube.Client:
    """Return a lightkube Client that can talk to the K8s API."""
    client = lightkube.Client(field_manager="kfp-operators")
    return client


@pytest.fixture(scope="module")
def apply_profile(lightkube_client):
    """Apply a Profile simulating a user."""
    # Create a Profile global resource
    profile_resource = create_global_resource(
        group="kubeflow.org", version="v1", kind="Profile", plural="profiles"
    )

    # Apply Profile first
    apply_manifests(lightkube_client, PROFILE_FILE_PATH)
    log.info(f"Profile from {PROFILE_FILE_PATH} applied.")

    # Ensure the Profile is fully initialized
    try:
        for attempt in Retrying(
            stop=(stop_after_attempt(10) | stop_after_delay(30)),
            wait=wait_exponential(multiplier=1, min=5, max=10),
            reraise=True,
            before_sleep=before_sleep_log(log, logging.INFO),
        ):
            with attempt:
                # Look for the Profile and some of the objects expected in an instantiated
                # Profile (to avoid https://github.com/canonical/training-operator/issues/136)
                for resource, name, namespace in (
                    (profile_resource, PROFILE_NAME, None),
                    (ServiceAccount, "default-editor", PROFILE_NAME),
                    (RoleBinding, "default-editor", PROFILE_NAME),
                ):
                    log.info(f"Looking for {resource} of name {name}")
                    lightkube_client.get(resource, name=name, namespace=namespace)
                    log.info(f"Found {resource} of name {name}")
    except RetryError:
        log.info(f"Profile {PROFILE_NAME} not found or found to be incomplete.")

    # Wait a few seconds more just in case the profile-controller is still creating the Profile
    # to avoid https://github.com/canonical/training-operator/issues/136
    sleeptime = 5
    log.info(f"Waiting {sleeptime}s to ensure Profile is fully initialized")
    time.sleep(sleeptime)

    yield

    # Remove namespace
    read_yaml = PROFILE_FILE_PATH.read_text()
    yaml_loaded = codecs.load_all_yaml(read_yaml)
    for obj in yaml_loaded:
        lightkube_client.delete(
            res=type(obj),
            name=obj.metadata.name,
            namespace=obj.metadata.namespace,
        )
