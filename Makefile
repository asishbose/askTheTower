# Ask the Tower — the project's one CLI. `make help` lists everything. ENV=local|eks|aws selects a target.
SHELL := /bin/bash
.DEFAULT_GOAL := help
include mk/vars.mk
include mk/help.mk
include mk/stack.mk
include mk/demo.mk
include mk/test.mk
include mk/build.mk
include mk/aws.mk
include mk/eks.mk
include mk/docs.mk
