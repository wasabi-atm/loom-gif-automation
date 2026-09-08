VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

.PHONY: setup doctor test clean

setup: ## Create the venv, install deps and the Chromium runtime
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install -e .
	$(VENV)/bin/playwright install chromium
	@test -f .env || cp .env.example .env
	@echo "\nDone. Fill in .env, then: $(VENV)/bin/loomgif doctor"

doctor:
	$(VENV)/bin/loomgif doctor

test:
	$(PY) -m unittest discover -s tests -v

clean:
	rm -rf output/* .venv **/__pycache__
	@touch output/.gitkeep
