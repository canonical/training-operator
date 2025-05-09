"""Charms dependencies for tests."""

from charmed_kubeflow_chisme.testing import CharmSpec

KUBEFLOW_PROFILES = CharmSpec(charm="kubeflow-profiles", channel="latest/edge", trust=True)
KUBEFLOW_ROLES = CharmSpec(charm="kubeflow-roles", channel="latest/edge", trust=True)
ISTIO_PILOT = CharmSpec(charm="istio-pilot", channel="latest/edge", trust=True)
