# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import glob
import logging
from pathlib import Path

import lightkube
import lightkube.codecs
import lightkube.generic_resource
import pytest
import tenacity
import yaml
from charmed_kubeflow_chisme.testing import (
    assert_alert_rules,
    assert_metrics_endpoint,
    deploy_and_assert_grafana_agent,
    get_alert_rules,
)
from lightkube.resources.apiextensions_v1 import CustomResourceDefinition
from lightkube.resources.rbac_authorization_v1 import ClusterRole
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

APP_NAME = "training-operator"
CHARM_LOCATION = None
APP_PREVIOUS_CHANNEL = "1.7/stable"
METRICS_PATH = "/metrics"
METRICS_PORT = 8080


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm and deploy it with trust=True.

    Assert on the unit status.
    """
    charm_under_test = await ops_test.build_charm(".")

    await ops_test.model.deploy(charm_under_test, application_name=APP_NAME, trust=True)
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=60 * 10
    )
    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"

    # store charm location in global to be used in other tests
    global CHARM_LOCATION
    CHARM_LOCATION = charm_under_test

    # Deploy grafana-agent for COS integration tests
    await deploy_and_assert_grafana_agent(ops_test.model, APP_NAME, metrics=True)


    # Wait for the training-operator workload Pod to run and the operator to start
    await ensure_training_operator_is_running(ops_test)

@tenacity.retry(
    wait=tenacity.wait_exponential(multiplier=1, min=1, max=30),
    stop=tenacity.stop_after_delay(30),
    reraise=True,
)
async def ensure_training_operator_is_running(ops_test: OpsTest) -> None:
    """Waits until the training-operator workload Pod's status is Running."""
    # The training-operator workload Pod gets a random name, the easiest way
    # to wait for it to be ready is using kubectl directly
    await ops_test.run(
        "kubectl",
        "wait",
        "--for=condition=ready",
        "pod",
        "-lapp.kubernetes.io/name=training-operator",
        f"-n{ops_test.model_name}",
        "--timeout=10m",
        check=True,
    )

    _, out, err = await ops_test.run(
        "kubectl",
        "get",
        "pods" f"-n{ops_test.model_name}",
        "--field-selector",
        "status.phase!=Running",
        check=True,
    )
    assert "training-operator" not in out

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


async def test_alert_rules(ops_test: OpsTest):
    """Test check charm alert rules and rules defined in relation data bag."""
    app = ops_test.model.applications[APP_NAME]
    alert_rules = get_alert_rules()
    logger.info("found alert_rules: %s", alert_rules)
    await assert_alert_rules(app, alert_rules)


async def test_metrics_enpoint(ops_test: OpsTest):
    """Test metrics_endpoints are defined in relation data bag and their accessibility.

    This function gets all the metrics_endpoints from the relation data bag, checks if
    they are available from the grafana-agent-k8s charm and finally compares them with the
    ones provided to the function.
    """
    app = ops_test.model.applications[APP_NAME]
    # metrics_target should be the same as the one defined in the charm code when instantiating
    # the MetricsEndpointProvider. It is set to the training-operator Service name because this
    # charm is not a sidecar, once this is re-written in sidecar pattern, this value can be *
    metrics_target = f"{APP_NAME}.{ops_test.model.name}.svc"
    await assert_metrics_endpoint(
        app, metrics_port=METRICS_PORT, metrics_path=METRICS_PATH, metrics_target=metrics_target
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
        namespace=ops_test.model_name,
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
    await ops_test.model.deploy(entity_url=APP_NAME, channel=APP_PREVIOUS_CHANNEL, trust=True)
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=60 * 10
    )

    # refresh (upgrade) using charm built in test_build_and_deploy()
    # NOTE: using ops_test.juju() because there is no functionality to refresh in ops_test
    await ops_test.juju(
        "refresh",
        APP_NAME,
        f"--path={CHARM_LOCATION}",
        "--trust",
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=60 * 10
    )

    # verify that all CRDs are installed
    lightkube_client = lightkube.Client()
    crd_list = lightkube_client.list(
        CustomResourceDefinition,
        labels=[("app.juju.is/created-by", "training-operator")],
        namespace=ops_test.model_name,
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
        name=f"{ops_test.model_name}-{APP_NAME}-charm",
        namespace=ops_test.model_name,
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
        namespace=ops_test.model_name,
    )
    for crd in crd_list:
        lightkube_client.delete(
            CustomResourceDefinition,
            name=crd.metadata.name,
            namespace=ops_test.model_name,
        )

    # remove deployed charm and verify that it is removed successfully
    await ops_test.model.remove_application(app_name=APP_NAME, block_until_done=True)
    assert APP_NAME not in ops_test.model.applications
