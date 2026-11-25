.DEFAULT_GOAL := help
PYTHON ?= python3
SIMULATOR ?=
DEVICE ?=

.PHONY: help setup setup-mobile start local stop status expo stop-expo simulator simulator-open device device-dev build check
help:
	@echo 'make setup           Install Mac dependencies/models and build the web UI'
	@echo 'make setup-mobile    Install Expo dependencies'
	@echo 'make start           Build web UI and start the local app (background)'
	@echo 'make expo            Start Expo/Metro (background)'
	@echo 'make simulator       Start local app + Expo, build/install/open iOS simulator app'
	@echo '                     Optional: SIMULATOR="iPhone 17 Pro"'
	@echo 'make simulator-open  Open an already-installed app via Expo (foreground; Ctrl-C stops)'
	@echo 'make device          Build/install standalone Release app on iPhone'
	@echo 'make device-dev      Start Metro and install iPhone development app with live updates'
	@echo '                     Optional: DEVICE="device name or UDID"; signing required'
	@echo 'make status          Show managed local app and Expo status'
	@echo 'make stop            Stop managed local app and Expo services'
	@echo 'make stop-expo       Stop only managed Expo/Metro'
	@echo 'make check           Run backend/mobile tests, TypeScript and web build'
setup:
	./scripts/setup.sh
setup-mobile:
	cd mobile && npm ci
build:
	npm --prefix frontend run build
start local: build
	$(PYTHON) scripts/services.py start local
expo:
	$(PYTHON) scripts/services.py start expo
simulator: start expo
	cd mobile && npm run ios -- --no-bundler $(if $(SIMULATOR),--device "$(SIMULATOR)",)
simulator-open: stop-expo
	cd mobile && npm start -- --ios
device:
	cd mobile && npm run ios -- --configuration Release --no-bundler --device $(if $(DEVICE),"$(DEVICE)",)
device-dev: expo
	cd mobile && npm run ios -- --configuration Debug --no-bundler --device $(if $(DEVICE),"$(DEVICE)",)
status:
	$(PYTHON) scripts/services.py status
stop:
	$(PYTHON) scripts/services.py stop
stop-expo:
	$(PYTHON) scripts/services.py stop expo
check:
	.venv/bin/python -m pytest -q
	npm --prefix mobile run typecheck
	npm --prefix mobile test
	npm --prefix frontend run build
