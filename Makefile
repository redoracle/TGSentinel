# TGSentinel - Python Project Commands
# Professional Makefile for development, testing, and deployment workflows
# Run these with: make <command>

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
MAKEFLAGS += --warn-undefined-variables
MAKEFLAGS += --no-builtin-rules

# Project configuration
PROJECT_NAME := tgsentinel
PYTHON := python3
PIP := $(PYTHON) -m pip
VENV_DIR := .venv
VENV_ACTIVATE := $(VENV_DIR)/bin/activate

# Source directories
SRC_DIRS := src/ tests/ tools/
UI_DIR := ui/
ALL_DIRS := $(SRC_DIRS) $(UI_DIR)

# Code quality tools configuration
BLACK_ARGS := --line-length 88 --quiet
ISORT_ARGS := --profile black --quiet
FLAKE8_ARGS := --max-line-length=120 --extend-ignore=E203,W503,E402
MYPY_ARGS := --ignore-missing-imports --no-error-summary

# Test configuration
PYTEST_ARGS := -v
PYTEST_COV_ARGS := --cov=src/$(PROJECT_NAME) --cov-report=term-missing --cov-report=html

# Colors for output
CYAN := \033[36m
GREEN := \033[32m
YELLOW := \033[33m
RED := \033[31m
RESET := \033[0m

# Ensure virtual environment is activated
define ensure_venv
	@if [ -z "$$VIRTUAL_ENV" ]; then \
		if [ -f "$(VENV_ACTIVATE)" ]; then \
			echo "$(YELLOW)⚠️  Activating virtual environment...$(RESET)"; \
			. "$(VENV_ACTIVATE)"; \
		else \
			echo "$(RED)❌ Virtual environment not found at $(VENV_ACTIVATE)$(RESET)" >&2; \
			echo "$(YELLOW)💡 Create one with: $(PYTHON) -m venv $(VENV_DIR)$(RESET)" >&2; \
			exit 1; \
		fi; \
	fi
endef

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help message with available targets
	@printf '$(CYAN)TGSentinel - Available Make Targets$(RESET)\n'
	@echo ''
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-20s$(RESET) %s\n", $$1, $$2}' 2>/dev/null || true
	@echo ''
	@printf '$(CYAN)Usage:$(RESET) make $(GREEN)<target>$(RESET)\n'
	@echo ''

# =============================================================================
# Development Environment
# =============================================================================

.PHONY: venv
venv: ## Create a new virtual environment
	@printf "$(CYAN)🔧 Creating virtual environment...$(RESET)\n"
	@$(PYTHON) -m venv $(VENV_DIR)
	@. $(VENV_ACTIVATE) && $(PIP) install --upgrade pip setuptools wheel
	@printf "$(GREEN)✅ Virtual environment created at $(VENV_DIR)$(RESET)\n"
	@printf "$(YELLOW)💡 Activate with: source $(VENV_ACTIVATE)$(RESET)\n"

.PHONY: install
install: ## Install project dependencies
	@printf "$(CYAN)📦 Installing dependencies...$(RESET)\n"
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && $(PIP) install -r requirements.txt
	@printf "$(GREEN)✅ Dependencies installed!$(RESET)\n"

.PHONY: install-dev
install-dev: ## Install development dependencies
	@printf "$(CYAN)📦 Installing development dependencies...$(RESET)\n"
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && $(PIP) install -r requirements.txt && \
		$(PIP) install black isort flake8 mypy pytest pytest-cov pytest-asyncio ruff
	@printf "$(GREEN)✅ Development dependencies installed!$(RESET)\n"

# =============================================================================
# Code Quality & Formatting
# =============================================================================

.PHONY: format
format: ## Format all Python files with black and isort
	@printf "$(CYAN)🔧 Formatting Python files...$(RESET)\n"
	@black $(SRC_DIRS) $(BLACK_ARGS)
	@isort $(SRC_DIRS) $(ISORT_ARGS)
	@printf "$(GREEN)✅ All files formatted!$(RESET)\n"

.PHONY: format-ui
format-ui: ## Format UI Python files separately
	@printf "$(CYAN)🔧 Formatting UI files...$(RESET)\n"
	@black $(UI_DIR) $(BLACK_ARGS)
	@isort $(UI_DIR) $(ISORT_ARGS)
	@printf "$(GREEN)✅ UI files formatted!$(RESET)\n"

.PHONY: format-all
format-all: format format-ui ## Format all Python files including UI

.PHONY: format-check
format-check: ## Check if files need formatting (CI mode)
	@printf "$(CYAN)🔍 Checking Python formatting...$(RESET)\n"
	@black $(SRC_DIRS) --line-length 88 --check --quiet || \
		(printf "$(RED)❌ Black formatting check failed!$(RESET)\n" && \
		 printf "$(YELLOW)💡 Run 'make format' to fix$(RESET)\n" && exit 1)
	@isort $(SRC_DIRS) --profile black --check-only --quiet || \
		(printf "$(RED)❌ Isort check failed!$(RESET)\n" && \
		 printf "$(YELLOW)💡 Run 'make format' to fix$(RESET)\n" && exit 1)
	@flake8 $(ALL_DIRS) $(FLAKE8_ARGS) || \
		(printf "$(RED)❌ Flake8 check failed!$(RESET)\n" && \
		 printf "$(YELLOW)💡 Fix linting issues manually$(RESET)\n" && exit 1)
	@printf "$(GREEN)✅ All files properly formatted!$(RESET)\n"

.PHONY: lint
lint: ## Run all linters (flake8, mypy, ruff)
	@printf "$(CYAN)🔍 Running flake8...$(RESET)\n"
	@flake8 $(ALL_DIRS) $(FLAKE8_ARGS) --statistics || true
	@printf "$(CYAN)🔍 Running mypy...$(RESET)\n"
	@mypy src/ $(MYPY_ARGS) || true
	@printf "$(CYAN)🔍 Running ruff...$(RESET)\n"
	@ruff check $(ALL_DIRS) || true
	@printf "$(GREEN)✅ Linting complete!$(RESET)\n"

.PHONY: lint-fix
lint-fix: ## Auto-fix linting issues where possible
	@printf "$(CYAN)🔧 Auto-fixing linting issues...$(RESET)\n"
	@ruff check $(ALL_DIRS) --fix || true
	@$(MAKE) format-all
	@printf "$(GREEN)✅ Auto-fix complete!$(RESET)\n"

.PHONY: type-check
type-check: ## Run mypy type checking
	@printf "$(CYAN)🔍 Running type checker...$(RESET)\n"
	@mypy src/ $(MYPY_ARGS)
	@printf "$(GREEN)✅ Type checking complete!$(RESET)\n"

# =============================================================================
# Testing
# =============================================================================

.PHONY: test
test: ## Run all tests (unit + integration + contracts)
	@printf "$(CYAN)🧪 Running all tests...$(RESET)\n"
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && $(PYTHON) tools/run_tests.py

.PHONY: test-unit
test-unit: ## Run unit tests only
	@printf "$(CYAN)🧪 Running unit tests...$(RESET)\n"
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && pytest tests/unit/ $(PYTEST_ARGS)

.PHONY: test-integration
test-integration: ## Run integration tests only
	@printf "$(CYAN)🧪 Running integration tests...$(RESET)\n"
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && pytest tests/integration/ $(PYTEST_ARGS)

.PHONY: test-contracts
test-contracts: ## Run contract tests only
	@printf "$(CYAN)🧪 Running contract tests...$(RESET)\n"
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && pytest tests/contracts/ $(PYTEST_ARGS)

.PHONY: test-validation
test-validation: ## Run validation tests only
	@printf "$(CYAN)🧪 Running validation tests...$(RESET)\n"
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && pytest tests/validation/ $(PYTEST_ARGS)

.PHONY: test-infra
test-infra: ## Run infrastructure tests (requires running services)
	@printf "$(CYAN)🧪 Running infrastructure tests...$(RESET)\n"
	@printf "$(YELLOW)⚠️  Note: Requires Redis, Sentinel, and UI services to be running$(RESET)\n"
	@printf "$(YELLOW)   Start with: docker compose up -d redis sentinel ui$(RESET)\n"
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && pytest tests/infrastructure/ $(PYTEST_ARGS)

.PHONY: test-cov
test-cov: ## Run tests with coverage report
	@printf "$(CYAN)🧪 Running tests with coverage...$(RESET)\n"
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && pytest $(PYTEST_COV_ARGS)
	@printf "$(GREEN)✅ Coverage report generated in htmlcov/index.html$(RESET)\n"

.PHONY: test-watch
test-watch: ## Run tests in watch mode (re-run on file changes)
	@printf "$(CYAN)🧪 Running tests in watch mode...$(RESET)\n"
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && pytest-watch -- $(PYTEST_ARGS)

.PHONY: test-failed
test-failed: ## Re-run only failed tests from last run
	@printf "$(CYAN)🧪 Re-running failed tests...$(RESET)\n"
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && pytest --lf $(PYTEST_ARGS)

.PHONY: code-test
code-test: ## Run code logic tests only (unit + integration + contracts)
	@printf "$(CYAN)🧪 Running code logic tests...$(RESET)\n"
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && pytest tests/unit/ tests/integration/ tests/contracts/ $(PYTEST_ARGS)

# =============================================================================
# Docker Operations
# =============================================================================

.PHONY: docker-build
docker-build: ## Build Docker images
	@printf "$(CYAN)🐳 Building Docker images...$(RESET)\n"
	@docker compose build
	@printf "$(GREEN)✅ Docker images built!$(RESET)\n"

.PHONY: docker-up
docker-up: ## Start all services in background
	@printf "$(CYAN)🐳 Starting services...$(RESET)\n"
	@docker compose up -d
	@printf "$(GREEN)✅ Services started!$(RESET)\n"
	@printf "$(YELLOW)💡 View logs with: make docker-logs$(RESET)\n"

.PHONY: docker-down
docker-down: ## Stop all services
	@printf "$(CYAN)🐳 Stopping services...$(RESET)\n"
	@docker compose down
	@printf "$(GREEN)✅ Services stopped!$(RESET)\n"

.PHONY: docker-restart
docker-restart: docker-down docker-up ## Restart all services

.PHONY: docker-logs
docker-logs: ## Follow service logs (sentinel by default)
	@docker compose logs -f sentinel

.PHONY: docker-logs-all
docker-logs-all: ## Follow logs for all services
	@docker compose logs -f

.PHONY: docker-test
docker-test: ## Run tests inside Docker container
	@printf "$(CYAN)🐳 Running tests in Docker...$(RESET)\n"
	@docker compose run --rm sentinel python tools/run_tests.py

.PHONY: docker-clean
docker-clean: ## Remove all containers, volumes, and images
	@printf "$(CYAN)🐳 Cleaning Docker resources...$(RESET)\n"
	@docker compose down -v --rmi all
	@printf "$(GREEN)✅ Docker cleanup complete!$(RESET)\n"

.PHONY: docker-shell
docker-shell: ## Open a shell in the sentinel container
	@docker compose exec sentinel /bin/bash

.PHONY: docker-rebuild
docker-rebuild: docker-clean docker-build ## Clean rebuild of all Docker images

# =============================================================================
# Database & Redis Operations
# =============================================================================

.PHONY: redis-cli
redis-cli: ## Connect to Redis CLI
	@docker compose exec redis redis-cli

.PHONY: db-backup
db-backup: ## Backup databases (requires running containers)
	@printf "$(CYAN)💾 Backing up databases...$(RESET)\n"
	@mkdir -p backups
	@docker compose exec -T sentinel sqlite3 /app/data/sentinel.db ".backup '/app/data/backup_$$(date +%Y%m%d_%H%M%S).db'"
	@printf "$(GREEN)✅ Database backed up!$(RESET)\n"

# =============================================================================
# Maintenance & Cleanup
# =============================================================================

.PHONY: clean
clean: ## Clean up generated files and caches
	@printf "$(CYAN)🧹 Cleaning up...$(RESET)\n"
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete
	@find . -type f -name '*.pyo' -delete
	@find . -type f -name '.coverage' -delete
	@find . -type f -name '.DS_Store' -delete
	@printf "$(GREEN)✅ Cleanup complete!$(RESET)\n"

.PHONY: clean-all
clean-all: clean docker-clean ## Deep clean: remove all generated files and Docker resources

.PHONY: clean-logs
clean-logs: ## Remove log files
	@printf "$(CYAN)🧹 Cleaning log files...$(RESET)\n"
	@find . -type f -name '*.log' -delete
	@printf "$(GREEN)✅ Log files cleaned!$(RESET)\n"

# =============================================================================
# Code Analysis & Metrics
# =============================================================================

.PHONY: complexity
complexity: ## Analyze code complexity with radon
	@printf "$(CYAN)📊 Analyzing code complexity...$(RESET)\n"
	@radon cc src/ -a -nb || (echo "$(YELLOW)💡 Install radon: pip install radon$(RESET)" && exit 1)

.PHONY: metrics
metrics: ## Show code metrics (lines of code, etc.)
	@printf "$(CYAN)📊 Code Metrics:$(RESET)\n"
	@echo ""
	@printf "$(GREEN)Source Code:$(RESET)\n"
	@find src/ -name '*.py' -exec wc -l {} + | tail -1
	@printf "$(GREEN)Tests:$(RESET)\n"
	@find tests/ -name '*.py' -exec wc -l {} + | tail -1
	@printf "$(GREEN)Tools:$(RESET)\n"
	@find tools/ -name '*.py' -exec wc -l {} + | tail -1

.PHONY: security
security: ## Run security checks with bandit
	@printf "$(CYAN)🔒 Running security checks...$(RESET)\n"
	@bandit -r src/ -f screen || (echo "$(YELLOW)💡 Install bandit: pip install bandit$(RESET)" && exit 1)

# =============================================================================
# CI/CD Targets
# =============================================================================

.PHONY: ci-test
ci-test: format-check lint test ## Run all CI checks (format, lint, test)

.PHONY: pre-commit
pre-commit: format lint test-unit ## Run pre-commit checks
	@printf "$(GREEN)✅ Pre-commit checks passed!$(RESET)\n"

.PHONY: pre-push
pre-push: format-check test ## Run pre-push checks
	@printf "$(GREEN)✅ Pre-push checks passed!$(RESET)\n"

# =============================================================================
# Utility Targets
# =============================================================================

.PHONY: deps-list
deps-list: ## List all installed Python packages
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && $(PIP) list

.PHONY: deps-outdated
deps-outdated: ## Show outdated dependencies
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && $(PIP) list --outdated

.PHONY: deps-tree
deps-tree: ## Show dependency tree
	$(call ensure_venv)
	@. $(VENV_ACTIVATE) && pipdeptree || (echo "$(YELLOW)💡 Install pipdeptree: pip install pipdeptree$(RESET)" && exit 1)

.PHONY: version
version: ## Show project and tool versions
	@printf "$(CYAN)Version Information:$(RESET)\n"
	@echo "Python: $$($(PYTHON) --version)"
	@echo "Pip: $$($(PIP) --version)"
	@$(call ensure_venv)
	@. $(VENV_ACTIVATE) && echo "Black: $$(black --version 2>&1)" || echo "Black: not installed"
	@. $(VENV_ACTIVATE) && echo "Flake8: $$(flake8 --version 2>&1 | head -1)" || echo "Flake8: not installed"
	@. $(VENV_ACTIVATE) && echo "Pytest: $$(pytest --version 2>&1)" || echo "Pytest: not installed"

.PHONY: info
info: version metrics ## Show comprehensive project information
