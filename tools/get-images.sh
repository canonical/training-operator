#!/bin/bash
#
# This script returns list of container images that are managed by this charm and/or its workload
#
# dynamic list
IMAGE_LIST=()
IMAGE_LIST+=($(find -type f -name config.yaml -exec yq '.options | with_entries(select(.key | test("-image$"))) | .[].default' {} \;))
printf "%s\n" "${IMAGE_LIST[@]}"

