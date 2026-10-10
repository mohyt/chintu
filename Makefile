PYTHON ?= python3
export PYTHONPATH := $(CURDIR)/services/gateway/src:$(CURDIR)/packages/agent-adapters/src:$(PYTHONPATH)
export PYTHONDONTWRITEBYTECODE := 1

.PHONY: bootstrap test contract test-gateway test-adapters test-integration test-android-core demo android-build inspect

bootstrap:
	uv venv --allow-existing --python 3.12
	uv pip sync --require-hashes requirements-dev.lock
	uv pip install --no-build-isolation --no-deps -e packages/agent-adapters -e services/gateway

test: contract test-gateway test-adapters test-integration test-android-core

contract:
	$(PYTHON) scripts/check_contract.py

test-gateway:
	$(PYTHON) -m unittest discover -s services/gateway/tests -v

test-adapters:
	$(PYTHON) -m unittest discover -s packages/agent-adapters/tests -v

test-integration:
	$(PYTHON) -m unittest discover -s tests -v

test-android-core:
	sh apps/android/tests/run-core-tests.sh
	sh apps/android/tests/run-voice-tests.sh

demo:
	$(PYTHON) scripts/demo_gateway.py

android-build:
	$(PYTHON) apps/android/tools/android.py build

inspect:
	$(PYTHON) scripts/inspect_setup.py
