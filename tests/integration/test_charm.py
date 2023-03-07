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
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = "training-operator"


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
    await ops_test.model.deploy(prometheus, channel="latest/edge", trust=True)
    await ops_test.model.deploy(grafana, channel="latest/edge", trust=True)
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
