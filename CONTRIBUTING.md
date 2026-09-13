# Contributing to Navidrome Lyrics Aggregator

Thank you for your interest in contributing to **Navidrome Lyrics Aggregator**! We welcome bug reports, feature suggestions, provider additions, and pull requests.

---

## 🛠️ Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/olafix52/navidrome-lyrics-agregator.git
   cd navidrome-lyrics-agregator
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Set up local configuration:**
   Copy the example config:
   ```bash
   cp config.example.yaml config.local.yaml
   ```
   `config.local.yaml` is ignored by Git and will be automatically picked up when running the aggregator locally.

---

## 🧪 Running Tests

Always ensure all unit tests pass before submitting a pull request:

```bash
pytest
```

If you introduce a new provider or feature, please include corresponding unit tests in `tests/`.

---

## 📝 Coding Guidelines

- Write clean, type-annotated, and async-safe Python code.
- Follow PEP 8 style standards (recommended max line length: 120 chars).
- Do not commit sensitive data, personal tokens, or API keys.
- Write informative commit messages (e.g. Conventional Commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`).

---

## 🚀 Submitting a Pull Request

1. Fork the repository and create your feature branch:
   ```bash
   git checkout -b feat/my-new-feature
   ```
2. Commit your changes:
   ```bash
   git commit -m "feat: add support for XYZ provider"
   ```
3. Push to your fork:
   ```bash
   git push origin feat/my-new-feature
   ```
4. Open a Pull Request on GitHub against the `main` branch.
