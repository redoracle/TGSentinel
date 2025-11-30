#!/bin/bash
# Format all Python files with black and isort (equivalent to prettier for Python)

set -e

echo "🔧 Formatting Python files with black..."
black src/ tests/ tools/ --line-length 88 --quiet

echo "📦 Sorting imports with isort..."
isort src/ tests/ tools/ --profile black --quiet

echo "✅ All Python files formatted!"
