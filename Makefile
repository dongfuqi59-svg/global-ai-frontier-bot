PYTHON ?= python3.12

.PHONY: build-AppFunction
build-AppFunction:
	$(PYTHON) -m pip install --requirement requirements.txt --target "$(ARTIFACTS_DIR)"
	cp -R src "$(ARTIFACTS_DIR)/src"
	cp -R config "$(ARTIFACTS_DIR)/config"
	find "$(ARTIFACTS_DIR)" -type d -name __pycache__ -prune -exec rm -rf {} +
	find "$(ARTIFACTS_DIR)" -type f -name '*.py[co]' -delete
