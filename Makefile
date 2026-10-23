.PHONY: help install install-quite test test-all check dist release

PYTHON ?= python
TEST_FLAGS ?= -q

help:
	@echo "install - install pinned development/test requirements into your chosen environment"
	@echo "test/check - run the complete synthetic regression suite"
	@echo "test-all - run the target matrix using tox (install tox separately)"
	@echo "dist - build wheel and sdist without publishing"
	@echo "release - disabled; publication requires separate approval"

install:
	$(PYTHON) -m pip install -r requirements-dev.txt

install-quite:
	$(PYTHON) -m pip install -r requirements-dev.txt > /dev/null

test:
	$(PYTHON) -m pytest $(TEST_FLAGS)

check: test

# tox is an optional developer runner, not a runtime package dependency.
test-all:
	$(PYTHON) -m tox

dist:
	$(PYTHON) -m build --no-isolation

release:
	@echo "Automatic upload is disabled. Review and publish an exact artifact separately."
	@exit 1
