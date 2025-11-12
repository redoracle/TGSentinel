# TGSentinel - Python Project Commands
# Run these with: make <command>

.PHONY: help format format-check test test-cov lint clean docker-build docker-up docker-down docker-logs docker-test

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

format: ## Format all Python files with black and isort
	@echo "🔧 Formatting Python files..."
	@black src/ tests/ tools/ --line-length 88 --quiet
	@isort src/ tests/ tools/ --profile black --quiet
	@echo "✅ All files formatted!"

format-check: ## Check if files need formatting (CI mode)
	@echo "🔍 Checking Python formatting..."
	@black src/ tests/ tools/ --line-length 88 --check --quiet
	@isort src/ tests/ tools/ --profile black --check-only --quiet
	@echo "✅ All files properly formatted!"

test: ## Run all tests
	@python tools/run_tests.py

test-cov: ## Run tests with coverage report
	@pytest --cov=src/tgsentinel --cov-report=term-missing --cov-report=html

lint: ## Run static type checking and linting
	@echo "🔍 Running mypy..."
	@mypy src/ --ignore-missing-imports || true
	@echo "🔍 Running ruff..."
	@ruff check src/ tests/ || true

clean: ## Clean up generated files
	@echo "🧹 Cleaning up..."
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete
	@find . -type f -name '.coverage' -delete
	@echo "✅ Cleanup complete!"

docker-build: ## Build Docker image
	@docker compose build

docker-up: ## Start services in background
	@docker compose up -d

docker-down: ## Stop all services
	@docker compose down

docker-logs: ## Follow service logs
	@docker compose logs -f sentinel

docker-test: ## Run tests inside Docker container
	@docker compose run --rm sentinel python tools/run_tests.py
