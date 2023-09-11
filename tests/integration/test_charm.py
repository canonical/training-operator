# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import glob
import json
import logging
from pathlib import Path

import lightkube
import lightkube.codecs
import lightkube.generic_resource
import pytest
import requests
import tenacity
import yaml
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from lightkube.resources.rbac_authorization_v1 import ClusterRole
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = "training-operator"
CHARM_LOCATION = None


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm and deploy it with trust=True.

    Assert on the unit status.
    """
    charm_under_test = await ops_test.build_charm(".")
    image_path = METADATA["resources"]["training-operator-image"]["upstream-source"]
    resources = {"training-operator-image": image_path}

    await ops_test.model.deploy(
        charm_under_test, resources=resources, application_name=APP_NAME, trust=True
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=60 * 10
    )
    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"

    # store charm location in global to be used in other tests
    global CHARM_LOCATION
    CHARM_LOCATION = charm_under_test


def lightkube_create_global_resources() -> dict:
    """Returns a dict with GenericNamespacedResource as value for each CRD key."""
    crds_kinds = [
        crd["spec"]["names"]
        for crd in yaml.safe_load_all(Path("./src/templates/crds_manifests.yaml.j2").read_text())
    ]
    jobs_classes = {}
    for kind in crds_kinds:
        job_class = lightkube.generic_resource.create_namespaced_resource(
            group="kubeflow.org", version="v1", kind=kind["kind"], plural=kind["plural"]
        )
        jobs_classes[kind["kind"]] = job_class
    return jobs_classes


# TODO: Kubeflow upstream MXNet examples use GPU.
# Not testing MXNetjobs until we have a CPU mxjob example.
JOBS_CLASSES = lightkube_create_global_resources()


@pytest.mark.parametrize("example", glob.glob("examples/*.yaml"))
def test_create_training_jobs(ops_test: OpsTest, example: str):
    """Validates that a training job can be created and is running.

    Asserts on the *Job status.
    """
    namespace = ops_test.model_name
    lightkube_client = lightkube.Client()

    # Set up for creating an object of kind *Job
    job_yaml = yaml.safe_load(Path(example).read_text())
    job_object = lightkube.codecs.load_all_yaml(yaml.dump(job_yaml))[0]
    job_class = JOBS_CLASSES[job_object.kind]

    # Create *Job and check if it exists where expected
    lightkube_client.create(job_object, namespace=namespace)

    # Allow the resource to be created
    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=15),
        stop=tenacity.stop_after_delay(30),
        reraise=True,
    )
    def assert_get_job():
        """Asserts on the job.

        Retries multiple times using tenacity to allow time for the training job
        to be created.
        """
        job = lightkube_client.get(job_class, name=job_object.metadata.name, namespace=namespace)

        assert job is not None, f"{job_object.metadata.name} does not exist"

    # Wait for the *Job to have a status
    # TODO: change this workaround after
    # we have an implementation in lightkube
    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=2, min=1, max=30),
        stop=tenacity.stop_after_attempt(30),
        reraise=True,
    )
    def assert_job_status_running_success():
        """Asserts on the job status.

        Retries multiple times using tenacity to allow time for the training job
        to change its status from None -> Created -> Running/Succeeded.
        """
        job_status = lightkube_client.get(
            job_class.Status, name=job_object.metadata.name, namespace=namespace
        ).status["conditions"][-1]["type"]

        # Check whether the last status of *Job is Running/Success
        assert job_status in [
            "Running",
            "Succeeded",
        ], f"{job_object.metadata.name} was not running or did not succeed (status == {job_status})"

    assert_get_job()
    assert_job_status_running_success()


async def test_prometheus_grafana_integration(ops_test: OpsTest):
    """Deploy prometheus, grafana and required relations, then test the metrics."""
    prometheus = "prometheus-k8s"
    grafana = "grafana-k8s"
    prometheus_scrape = "prometheus-scrape-config-k8s"
    scrape_config = {"scrape_interval": "30s"}

    # Deploy and relate prometheus
    # FIXME: Unpin revision once https://github.com/canonical/bundle-kubeflow/issues/688 is closed
    await ops_test.juju(
        "deploy",
        prometheus,
        "--channel",
        "latest/edge",
        "--revision",
        "137",
        "--trust",
        check=True,
    )
    # FIXME: Unpin revision once https://github.com/canonical/bundle-kubeflow/issues/690 is closed
    await ops_test.juju(
        "deploy",
        grafana,
        "--channel",
        "latest/edge",
        "--revision",
        "89",
        "--trust",
        check=True,
    )
    await ops_test.model.deploy(prometheus_scrape, channel="latest/beta", config=scrape_config)

    await ops_test.model.add_relation(APP_NAME, prometheus_scrape)
    await ops_test.model.add_relation(
        f"{prometheus}:grafana-dashboard", f"{grafana}:grafana-dashboard"
    )
    await ops_test.model.add_relation(
        f"{APP_NAME}:grafana-dashboard", f"{grafana}:grafana-dashboard"
    )
    await ops_test.model.add_relation(
        f"{prometheus}:metrics-endpoint", f"{prometheus_scrape}:metrics-endpoint"
    )

    await ops_test.model.wait_for_idle(status="active", timeout=60 * 20)

    status = await ops_test.model.get_status()
    prometheus_unit_ip = status["applications"][prometheus]["units"][f"{prometheus}/0"]["address"]
    logger.info(f"Prometheus available at http://{prometheus_unit_ip}:9090")

    for attempt in retry_for_5_attempts:
        logger.info(
            f"Testing prometheus deployment (attempt " f"{attempt.retry_state.attempt_number})"
        )
        with attempt:
            r = requests.get(
                f"http://{prometheus_unit_ip}:9090/api/v1/query?"
                f'query=up{{juju_application="{APP_NAME}"}}'
            )
            response = json.loads(r.content.decode("utf-8"))
            response_status = response["status"]
            logger.info(f"Response status is {response_status}")
            assert response_status == "success"

            response_metric = response["data"]["result"][0]["metric"]
            assert response_metric["juju_application"] == APP_NAME
            assert response_metric["juju_model"] == ops_test.model_name


# Helper to retry calling a function over 30 seconds or 5 attempts
retry_for_5_attempts = tenacity.Retrying(
    stop=(tenacity.stop_after_attempt(5) | tenacity.stop_after_delay(30)),
    wait=tenacity.wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)


@pytest.mark.abort_on_fail
async def test_remove_with_resources_present(ops_test: OpsTest):
    """Test remove with all resources deployed.

    Verify that all deployed resources that need to be removed are removed.

    This test should be next before before test_upgrade(), because it removes deployed charm.
    """
    # remove deployed charm and verify that it is removed
    await ops_test.model.remove_application(app_name=APP_NAME, block_until_done=True)
    assert APP_NAME not in ops_test.model.applications

    # verify that all resources that were deployed are removed
    lightkube_client = lightkube.Client()
    crd_list = lightkube_client.list(
        CustomResourceDefinition,
        labels=[("app.juju.is/created-by", "training-operator")],
        namespace=ops_test.model.name,
    )
    # testing for empty list (iterator)
    _last = object()
    assert next(crd_list, _last) is _last


@pytest.mark.abort_on_fail
async def test_upgrade(ops_test: OpsTest):
    """Test upgrade.

    Verify that all upgrade process succeeds.

    There should be no charm with APP_NAME deployed (after test_remove_with_resources_present()),
    because it deploys stable version of this charm and peforms upgrade.
    """

    # deploy stable version of the charm
    await ops_test.model.deploy(entity_url=APP_NAME, channel="1.5/stable", trust=True)
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=60 * 10
    )

    # refresh (upgrade) using charm built in test_build_and_deploy()
    # NOTE: using ops_test.juju() because there is no functionality to refresh in ops_test
    image_path = METADATA["resources"]["training-operator-image"]["upstream-source"]
    await ops_test.juju(
        "refresh",
        APP_NAME,
        f"--path={CHARM_LOCATION}",
        f'--resource="training-operator-image={image_path}"',
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=60 * 10
    )

    # verify that all CRDs are installed
    lightkube_client = lightkube.Client()
    crd_list = lightkube_client.list(
        CustomResourceDefinition,
        labels=[("app.juju.is/created-by", "training-operator")],
        namespace=ops_test.model.name,
    )
    # testing for non empty list (iterator)
    _last = object()
    assert not next(crd_list, _last) is _last

    # check that all CRDs are installed and versions are correct
    test_crd_list = []
    for crd in yaml.safe_load_all(Path("./src/templates/crds_manifests.yaml.j2").read_text()):
        test_crd_list.append(
            (
                crd["metadata"]["name"],
                crd["metadata"]["annotations"]["controller-gen.kubebuilder.io/version"],
            )
        )
    for crd in crd_list:
        assert (
            (crd.metadata.name, crd.metadata.annotations["controller-gen.kubebuilder.io/version"])
        ) in test_crd_list

    # verify that if ClusterRole is installed and parameters are correct
    cluster_role = lightkube_client.get(
        ClusterRole,
        name=f"{ops_test.model.name}-{APP_NAME}-charm",
        namespace=ops_test.model.name,
    )
    for rule in cluster_role.rules:
        if rule.apiGroups == "kubeflow.org":
            assert "paddlejobs" in rule.resources


@pytest.mark.abort_on_fail
async def test_remove_without_resources(ops_test: OpsTest):
    """Test remove when no resources are present.

    Verify that application is removed and not stuck in error state.

    This test should be last in the test suite after test_upgrade(), because it removes deployed
    charm.
    """

    # remove all CRDs
    lightkube_client = lightkube.Client()
    crd_list = lightkube_client.list(
        CustomResourceDefinition,
        labels=[("app.juju.is/created-by", "training-operator")],
        namespace=ops_test.model.name,
    )
    for crd in crd_list:
        lightkube_client.delete(
            CustomResourceDefinition,
            name=crd.metadata.name,
            namespace=ops_test.model.name,
        )

    # remove deployed charm and verify that it is removed successfully
    await ops_test.model.remove_application(app_name=APP_NAME, block_until_done=True)
    assert APP_NAME not in ops_test.model.applications
