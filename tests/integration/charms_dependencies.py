"""Charms dependencies for tests."""

from charmed_kubeflow_chisme.testing import CharmSpec

KUBEFLOW_PROFILES = CharmSpec(charm="kubeflow-profiles", channel="2.0/edge", trust=True)
KUBEFLOW_ROLES = CharmSpec(charm="kubeflow-roles", channel="1.10/edge", trust=True)
ISTIO_PILOT = CharmSpec(charm="istio-pilot", channel="1.28/edge", trust=True)
