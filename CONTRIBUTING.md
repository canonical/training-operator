# Contributing

## Overview

This document explains the processes and practices recommended for contributing enhancements to
this operator.

- Generally, before developing enhancements to this charm, you should consider [opening an issue
  ](https://github.com/canonical/training-operator/issues) explaining your use case.
- If you would like to chat with us about your use-cases or proposed implementation, you can reach
  us at [Canonical Mattermost public channel](https://chat.charmhub.io/charmhub/channels/charm-dev)
  or [Discourse](https://discourse.charmhub.io/).
- Familiarising yourself with the [Charmed Operator Framework](https://juju.is/docs/sdk) library
  will help you a lot when working on new features or bug fixes.
- All enhancements require review before being merged. Code review typically examines
  - code quality
  - test coverage
  - user experience for Juju administrators of this charm.
- Please help us out in ensuring easy to review branches by rebasing your pull request branch onto
  the `main` branch. This also avoids merge commits and creates a linear Git commit history.

## Developing

You can use the environments created by `tox` for development:

```shell
tox --notest -e unit
source .tox/unit/bin/activate
```

### Testing

```shell
tox -e lint          # code style
tox -e unit          # unit tests
tox -e integration   # integration tests
tox                  # runs 'lint' and 'unit' environments
```

## Build charm

Build the charm in this git repository using:

```shell
charmcraft pack
```

### Deploy

```bash
# Create a model
juju add-model dev
# Enable DEBUG logging
juju model-config logging-config="<root>=INFO;unit=DEBUG"
# Deploy the charm
juju deploy ./training-operator_ubuntu@24.04-amd64.charm \
    --resource training-operator-image=$(yq '.resources."training-operator-image"."upstream-source"' metadata.yaml)
```

## Updating the charm for new versions of the workload

To upgrade the source and resources Charmed Training Operator, you must:

1) Bump the `training-operator-image` in `metadata.yaml`
2) Update the charm source for any changes, such as:

* YAML manifests in src/ and/or any Kubernetes resource in pod_spec
* New or changed configurations passed to pebble workloads or through pod.set_spec

3) Ensure integration and unit tests are passing; fix/adapt them otherwise

The following table collects a series of links and information that can help in the upgrade process.
 
| Image registry 	| Image tag format	| Manifest location 	| Next version 	|
|---	|---	|---	|---	|
| [kubeflow/training-operator](https://hub.docker.com/r/kubeflow/training-operator/) 	| `v1-<commit sha>`<br>The commit sha comes from the HEAD of the release tag 	| [kubeflow/manifests](https://github.com/kubeflow/manifests/tree/master/apps/training-operator/upstream) 	| Check for latest version released or pre-released in <br>the repo [releases](https://github.com/kubeflow/training-operator/releases), it should match the release plan usually stated in issues like <br>[Training Operator WG and Kubeflow 1.6](https://github.com/kubeflow/manifests/issues/2196) release and [KF Release 1.6 Tracking](https://github.com/kubeflow/manifests/issues/2194) 	|

## Canonical Contributor Agreement

Canonical welcomes contributions to the Charmed Training Operator. Please check out our [contributor agreement](https://ubuntu.com/legal/contributors) if you're interested in contributing to the solution.


## How to Manage Python Dependencies and Environments


### Prerequisites

`tox` is the only tool required locally, as `tox` internally installs and uses `poetry`, be it to manage Python dependencies or to run `tox` environments. To install it: `pipx install tox`.

Optionally, `poerty` can be additionally installed independently just for the sake of running Python commands locally outside of `tox` during debugging/development. To install it: `pipx install poetry`.


### Updating Dependencies

To add/update/remove any dependencies and/or to upgrade Python, simply:

1. add/update/remove such dependencies to/in/from the desired group(s) below `[tool.poetry.group.<your-group>.dependencies]` in `pyproject.toml`, and/or upgrade Python itself in `requires-python` under `[project]`

    _⚠️ dependencies for the charm itself are also defined as dependencies of a dedicated group called `charm`, specifically below `[tool.poetry.group.charm.dependencies]`, and not as project dependencies below `[project.dependencies]` or `[tool.poetry.dependencies]` ⚠️_

2. run `tox -e update-requirements` to update the lock file

    by this point, `poerty`, through `tox`, will let you know if there are any dependency conflicts to solve.

3. optionally, if you also want to update your local environment for running Python commands/scripts yourself and not through tox, see [Running Python Environments](#running-python-environments) below


### Running `tox` Environments

To run `tox` environments, either locally for development or in CI workflows for testing, ensure to have `tox` installed first and then simply run your `tox` environments natively (e.g.: `tox -e lint`). `tox` will internally first install `poetry` and then rely on it to install and run its environments.


### Running Python Environments

To run Python commands locally for debugging/development from any environments built from any combinations of dependency groups without relying on `tox`:
1. ensure you have `poetry` installed
2. install any required dependency groups: `poetry install --only <your-group-a>,<your-group-b>` (or all groups, if you prefer: `poetry install --all-groups`)
3. run Python commands via poetry: `poetry run python3 <your-command>`
