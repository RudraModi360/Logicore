# 🔍 SEO Checklist — Post-Publish

Run these steps **immediately after publishing** to maximize Google indexing speed.

---

## 1. Google Search Console (Critical)

These are the most impactful actions for getting indexed quickly.

- [ ] Go to [Google Search Console](https://search.google.com/search-console/)
- [ ] **Submit PyPI URL for indexing**:
  - Use the URL Inspection tool → enter `https://pypi.org/project/logicore/`
  - Click **"Request Indexing"**
- [ ] **Submit GitHub URL for indexing**:
  - Enter `https://github.com/RudraModi360/Agentry`
  - Click **"Request Indexing"**
- [ ] **Submit Docs URL** (if GitHub Pages is live):
  - Enter `https://rudramodi360.github.io/Agentry/`
  - Click **"Request Indexing"**

> **Note**: You need to have verified ownership of your docs domain in Search Console.
> For PyPI and GitHub, you can use the public URL inspection — it still helps.

---

## 2. GitHub Repository SEO

- [ ] **Update repo description** to include keywords:
  > "Logicore — A modular, multi-provider AI agent framework for Python. Build tool-using agents with Gemini, Groq, Ollama, Azure OpenAI."
- [ ] **Add GitHub Topics** (tags visible on the repo page):
  - `logicore`
  - `ai-agents`
  - `llm-framework`
  - `python`
  - `tool-use`
  - `function-calling`
  - `gemini`
  - `groq`
  - `ollama`
  - `mcp`
  - `agentic-ai`
- [ ] **Pin the repository** on your GitHub profile

---

## 3. Cross-Linking (Builds Google PageRank)

The more pages link to your package, the faster Google indexes and ranks it.

- [ ] **GitHub README** links to PyPI page ✅ (already done)
- [ ] **PyPI page** links back to GitHub ✅ (via `pyproject.toml` URLs)
- [ ] **GitHub Pages docs** links to both PyPI and GitHub ✅ (via navigation)

---

## 4. Social Signals (Drives Crawl Priority)

Google crawls URLs shared socially much faster.

- [ ] **Post on Twitter/X** with link to PyPI page
- [ ] **Post on LinkedIn** with a short intro
- [ ] **Post on Reddit** — subreddits:
  - r/Python
  - r/MachineLearning
  - r/LocalLLaMA
  - r/artificial
- [ ] **Post on Hacker News** (Show HN)
- [ ] **Post on Dev.to** or **Hashnode** — a blog article about logicore

---

## 5. Package Directories

- [ ] Submit to [awesome-python](https://github.com/vinta/awesome-python) (PR)
- [ ] Submit to [awesome-llm-agents](https://github.com/kaushikb11/awesome-llm-agents) (PR)
- [ ] Add to [Libraries.io](https://libraries.io/) (auto-indexed from PyPI)

---

## 6. Verify Indexing (48-72 hours later)

- [ ] Search Google for `logicore python package` — should appear
- [ ] Search Google for `logicore pypi` — should appear
- [ ] Search Google for `logicore AI agent framework` — should appear
- [ ] Check Google Search Console for indexed pages
