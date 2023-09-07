# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import glob
from pathlib import Path

import lightkube
import pytest
import yaml
from lightkube import codecs
from lightkube.generic_resource import create_global_resource
from pytest_operator.plugin import OpsTest

basedir = Path("./").absolute()
PROFILE_NAMESPACE = "profile-example"
PROFILE_FILE_PATH = f"{basedir}/tests/integration/profile.yaml"
PROFILE_FILE = yaml.safe_load(Path(PROFILE_FILE_PATH).read_text())
KUBEFLOW_USER_NAME = PROFILE_FILE["spec"]["owner"]["name"]
METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = "training-operator"


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm and deploy."""
    charm_under_test = await ops_test.build_charm(".")
    image_path = METADATA["resources"]["training-operator-image"]["upstream-source"]
    resources = {"training-operator-image": image_path}

    await ops_test.model.deploy(
        charm_under_test, resources=resources, application_name=APP_NAME, trust=True
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=60 * 10
    )

    # Deploy kubeflow-roles and kubeflow-profiles to create a Profile
    await ops_test.model.deploy(
        apps=["kubeflow-roles", "kubeflow-profiles"],
        status="active",
        raised_on_blocked=True,
        timeout=60 * 10,
    )
    await ops_test.model.wait_for_idle(
        status="active", raised_on_blocked=True, raise_on_error=True, timeout=60 * 10
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
    _, stdout, __ = await ops_test.run(
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
    assert stdout == "yes"


def apply_manifests(lightkube_client: lightkube.Client, yaml_file_path: str):
    """Apply resources using manifest files and returns the applied object.

    Args:
        lightkube_client (lightkube.Client): an instance of lightkube.Client to
            use for applying resources.
        yaml_file_path (str): the resource yaml file.

    Returns:
        A namespaced or global lightkube resource (obj).
    """
    read_yaml = Path(yaml_file_path).read_text()
    yaml_loaded = codecs.load_all_yaml(read_yaml)
    for obj in yaml_loaded:
        try:
            lightkube_client.apply(
                obj=obj,
                name=obj.metadata.name,
            )
        except lightkube.core.exceptions.ApiError as api_error:
            raise api_error
    return obj


@pytest.fixture(scope="module")
def lightkube_client() -> lightkube.Client:
    """Returns a lightkube Client that can talk to the K8s API."""
    client = lightkube.Client(field_manager="kfp-operators")
    return client


@pytest.fixture(scope="module")
def apply_profile(lightkube_client):
    """Apply a Profile simulating a user."""
    # Create a Viewer namespaced resource
    profile_class_resource = create_global_resource(  # noqa F841
        group="kubeflow.org", version="v1", kind="Profile", plural="profiles"
    )

    # Apply Profile first
    apply_manifests(lightkube_client, PROFILE_FILE_PATH)

    yield

    # Remove namespace
    read_yaml = Path(PROFILE_FILE_PATH).read_text()
    yaml_loaded = codecs.load_all_yaml(read_yaml)
    for obj in yaml_loaded:
        try:
            lightkube_client.delete(
                res=type(obj),
                name=obj.metadata.name,
                namespace=obj.metadata.namespace,
            )
        except lightkube.core.exceptions.ApiError as api_error:
            raise api_error
