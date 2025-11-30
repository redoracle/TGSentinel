# Contributing to TG Sentinel

Thank you for your interest in contributing to TG Sentinel! 🎉

## 📋 Table of Contents

- [Code of Conduct](#-code-of-conduct)
- [Getting Started](#-getting-started)
- [Development Setup](#%EF%B8%8F-development-setup)
- [How to Contribute](#-how-to-contribute)
- [Pull Request Process](#-pull-request-process)
- [Coding Standards](#-coding-standards)
- [Testing Guidelines](#-testing-guidelines)
- [Documentation](#-documentation)

## 🤝 Code of Conduct

Be respectful, inclusive, and professional. We're here to build something great together.

## 🚀 Getting Started

1. **Fork the repository**

   ```bash
   # Fork on GitHub, then clone your fork
   git clone https://github.com/YOUR_USERNAME/TGSentinel.git
   cd TGSentinel
   ```

2. **Set up remote**

   ```bash
   git remote add upstream https://github.com/redoracle/TGSentinel.git
   ```

3. **Create a branch**

   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/your-bug-fix
   ```

## 🛠️ Development Setup

### Prerequisites

- Python 3.10+
- Docker & Docker Compose
- Git
- Telegram API credentials

### Local Setup

1. **Install dependencies**

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate

   # Install pip-tools for dependency management
   pip install pip-tools

   # Generate locked requirements.txt from requirements.in
   pip-compile --upgrade requirements.in

   # Install all dependencies
   pip install -r requirements.txt
   ```

2. **Configure environment**

   ```bash
   cp .env.sample .env
   # Edit .env with your credentials
   ```

3. **Run tests**

   ```bash
   make test
   ```

4. **Start development environment**

   ```bash
   docker compose up -d
   ```

## 🎯 How to Contribute

### Reporting Bugs

- Use the [Bug Report template](.github/ISSUE_TEMPLATE/bug_report.md)
- Search existing issues first to avoid duplicates
- Include clear reproduction steps
- Provide environment details

### Suggesting Features

- Use the [Feature Request template](.github/ISSUE_TEMPLATE/feature_request.md)
- Explain the use case and benefit
- Consider implementation complexity

### Code Contributions

#### Good First Issues

- Look for issues labeled `good first issue`
- These are beginner-friendly tasks

#### Areas That Need Help

- UI/UX improvements
- Documentation
- Test coverage
- Performance optimization
- New alert profile types
- Additional heuristics

## 🔄 Pull Request Process

1. **Sync with upstream**

   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Make your changes**

   - Write clean, documented code
   - Follow existing patterns
   - Add tests for new functionality

3. **Run quality checks**

   ```bash
   make format      # Format code
   make test        # Run tests
   make lint        # Check code quality
   ```

4. **Commit your changes**

   ```bash
   git add .
   git commit -m "feat: add new alert profile type"
   # Follow conventional commits format
   ```

5. **Push and create PR**

   ```bash
   git push origin feature/your-feature-name
   # Open PR on GitHub
   ```

6. **PR Requirements**
   - Fill out the PR template completely
   - Link related issues
   - Ensure CI checks pass
   - Respond to review feedback

## 💻 Coding Standards

### Python Style

- Follow PEP 8
- Use `black` for formatting (line length: 120)
- Use `isort` for import sorting
- Type hints where appropriate
- Docstrings for public functions

```python
def calculate_score(message: str, keywords: list[str]) -> float:
    """
    Calculate importance score based on keyword matches.

    Args:
        message: The message text to analyze
        keywords: List of keywords to match against

    Returns:
        Importance score between 0.0 and 1.0
    """
    pass
```

### File Organization

- One class per file (generally)
- Group related functions together
- Keep files under 500 lines when possible
- Use meaningful file and variable names

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat: add new feature
fix: resolve bug
docs: update documentation
style: format code
refactor: restructure code
test: add tests
chore: update dependencies
```

## 🧪 Testing Guidelines

### Writing Tests

- Test files mirror source structure: `src/tgsentinel/client.py` → `tests/test_client.py`
- Use descriptive test names: `test_alert_profile_matches_vip_sender`
- Cover edge cases and error conditions
- Use fixtures for common setup

### Running Tests

```bash
# All tests
make test

# Specific test file
pytest tests/test_client.py

# With coverage
make test-cov

# Specific test
pytest tests/test_client.py::test_function_name
```

### Coverage

- Aim for >80% coverage on new code
- Critical paths should have >90% coverage
- Don't sacrifice quality for coverage numbers

## 📚 Documentation

### Update Documentation When

- Adding new features
- Changing configuration options
- Modifying API endpoints
- Updating dependencies

### Documentation Files

- `README.md`: Project overview, quick start
- `docs/USER_GUIDE.md`: End-user documentation
- `docs/USAGE.md`: Deployment and operations
- `docs/ENGINEERING_GUIDELINES.md`: Technical architecture
- `docs/CONFIGURATION.md`: Configuration reference

### Inline Documentation

- Clear comments for complex logic
- Docstrings for all public functions/classes
- Type hints for better IDE support

## 🏷️ Issue and PR Labels

- `bug`: Something isn't working
- `enhancement`: New feature or request
- `documentation`: Documentation improvements
- `good first issue`: Good for newcomers
- `help wanted`: Extra attention needed
- `dependencies`: Dependency updates
- `security`: Security-related issues
- `performance`: Performance improvements
- `ui`: UI/UX related

## ❓ Questions?

- 💬 [GitHub Discussions](https://github.com/redoracle/TGSentinel/discussions)
- 📚 [Documentation](https://github.com/redoracle/TGSentinel/tree/main/docs)
- 🐛 [Issue Tracker](https://github.com/redoracle/TGSentinel/issues)

## 🙏 Thank You

Your contributions make TG Sentinel better for everyone. We appreciate your time and effort! ❤️

---

**Happy Coding!** 🚀
