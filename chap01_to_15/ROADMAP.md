# Roadmap: Python for Data Science & LLMs

A continuation of the 15-lesson tour, aimed at a Java tech lead going into
data science and LLM application development. Every concept keeps the same
promise as the lessons: **here is the Java thing you already know**.

## Phase 0 — Environment status (you're nearly done)

Already installed in your Python 3.11 (verified):

✅ numpy, pandas, scikit-learn, **PyTorch**, **transformers**, openai SDK, requests/httpx

Still missing (install when you reach the relevant phase):

```powershell
pip install matplotlib jupyterlab python-dotenv   # phases 3, 5, 6
pip install seaborn chromadb                      # optional: nicer plots, vector store
pip install anthropic                             # optional: 2nd LLM provider
```

Note: this stack lives in your global interpreter. It works, but when a
project's dependencies conflict, give it its own venv (`python -m venv .venv`)
— same isolation idea as separate Maven projects.

## The big libraries, mapped to Java

| Library | What it is | Closest Java thing |
|---|---|---|
| **NumPy** | N-dimensional arrays + vectorized math | ND4J/EJML — but ubiquitous here |
| **pandas** | DataFrames: in-memory tables | `List<Row>` + Streams + SQL GROUP BY |
| **matplotlib / seaborn** | Plotting | JFreeChart |
| **Jupyter** | Notebook IDE: code + output + prose in cells | IntelliJ + a REPL + markdown |
| **scikit-learn** | Classical ML with one uniform API | Weka, but with a clean `fit`/`predict` interface |
| **PyTorch** | Deep learning framework | DL4J; tensors = numpy + autograd + GPU |
| **transformers** | Pretrained models (LLMs, BERT, ...) one pip away | DJL model zoo |
| **openai / anthropic SDK** | Hosted LLM REST APIs | any REST client + JSON |
| **LangChain / LlamaIndex** | LLM app orchestration (RAG, agents) | LangChain4j (same ideas) |
| **FastAPI** | Serve models as REST APIs | Spring Boot (you'll feel at home) |
| **pydantic** | Typed models + validation | Bean Validation + Jackson records |
| **polars** | Faster, modern DataFrame engine | the "Jackson of DataFrames" — know it exists |
| **uv / poetry** | Dependency management & lockfiles | Maven / Gradle |

## Phase 1 — NumPy (1 weekend)

Core ideas: `ndarray` (shape, dtype), **vectorization** (no Python loops!),
broadcasting, boolean masks, fancy indexing, aggregations, `np.random`.

Java mindset shift: `total = np.sum(sales * price)` — one expression over
whole arrays, executed in C. Think batch/SIMD, not `for` loops.
`a[a > 0]` is a filter without a loop; `a[:, 1]` is a column slice.

Do: the official NumPy "quickstart" + "absolute basics", then the
**100 NumPy exercises** repo (github.com/rougier/numpy-100).

Mini-project: linear regression via the normal equation on synthetic data —
zero explicit loops.

## Phase 2 — pandas (1 week)

Core ideas: `Series`/`DataFrame`, `read_csv`/`read_json`, `loc`/`iloc`,
boolean filtering, `groupby().agg()`, `merge()` (SQL joins), `pivot`/`melt`,
handling missing values, dtypes, `apply()` vs vectorized ops.

Java/SQL mappings you'll lean on:

| pandas | you already know it as |
|---|---|
| `df.groupby("dept")["salary"].mean()` | SQL `GROUP BY` / `Collectors.groupingBy` |
| `df.merge(other, on="id")` | SQL `JOIN` |
| `df[df.age > 30]` | stream `filter` + `toList` |
| `df.sort_values("x", ascending=False).head(5)` | `sorted.comparing.reversed.limit` |
| `df.iloc[3, 2]` | `list.get(3).get(2)` by position |
| `df.loc[row, "col"]` | map lookup by label |

Do: official "10 minutes to pandas", then Kaggle Learn's free **Pandas**
course (hands-on, in-browser).

Mini-project: take a real CSV (NYC taxi sample, IMDB, or your own data),
clean it, group/pivot it, export a summary.

Side-quest: `14_trading_data.py` runs these exact drills on live market data
(yfinance) and ends with a vectorized SMA200 backtest; then
`15_trading_dashboard.py` wraps the same analysis in a browser UI
(Streamlit — widgets, caching, interactive charts, zero JS).

## Phase 3 — Visualization (2–3 days)

`pip install matplotlib`, then learn just the core: `figure`/`axes`,
`plot`, `scatter`, `hist`, `bar`, `subplots`, labels/titles. Then **seaborn**
for statistical plots in one line (`sns.boxplot(df, x="dept", y="salary")`).

Java: JFreeChart works, but matplotlib is the lingua franca — every tutorial
assumes it. Rule of thumb: matplotlib for control, seaborn for speed.

Mini-project: 4 charts (trend, distribution, correlation, category) from
your Phase-2 dataset, saved as PNGs (`fig.savefig`).

## Phase 4 — Classical ML with scikit-learn (1 week)

The genius of sklearn is **one interface for every model**:

```python
model = RandomForestClassifier(n_estimators=200)
model.fit(X_train, y_train)        # every estimator has fit()
model.predict(X_test)              # ...and predict()
```

Learn in this order: `train_test_split` → metrics (`accuracy`, `precision`,
`recall`, `RMSE`) → `Pipeline` + `ColumnTransformer` (preprocessing chained
into the model — like a build pipeline for data) → `cross_val_score` →
one-hot encoding → LinearRegression, LogisticRegression, RandomForest,
GradientBoosting/HistGradientBoosting.

Do: sklearn "Getting Started" + relevant User Guide pages; Kaggle Learn
"Intro to Machine Learning" + "Intermediate ML" (free).

Mini-project: **Titanic** on Kaggle, end-to-end with a Pipeline — train,
cross-validate, submit. First ML leaderboard entry.

## Phase 5 — Jupyter & working style (ongoing)

Jupyter is the DS IDE: cells, inline plots, rapid iteration.
`pip install jupyterlab`, then in VS Code install the **Jupyter extension**
— notebooks run inside your editor with IntelliSense.

Culture shift from Java: notebooks are for *exploration*; anything you keep
goes into `.py` modules with tests. Notebooks murder git diffs — clear
outputs before committing (`jupyter nbconvert --clear-output --inplace`).

## Phase 6 — The LLM track (pick your lane — you can do both)

**Lane A: LLM application development** (no GPU, most career-relevant)

1. **openai SDK** (already installed): chat completions, streaming,
   structured outputs (JSON → pydantic model), function/tool calling.
   Java framing: it's a typed REST client; tool calling = the model
   asking *your* code to invoke a method and return JSON.
2. **Embeddings + RAG**: chunk documents → embed → store in a vector DB
   (`chromadb` is the zero-setup choice; pgvector if you're Postgres-minded)
   → retrieve top-k → stuff into the prompt. You already know search
   systems; RAG is search + prompt assembly.
3. **FastAPI + pydantic** to serve your LLM app — this is Spring Boot
   territory; you'll be productive in a day.
4. Frameworks (LangChain/LlamaIndex): learn the primitives first, use a
   framework only when the boilerplate actually hurts.
5. Keys via environment variables + `python-dotenv` — never hardcode.
   The **OpenAI Cookbook** (github.com/openai/openai-cookbook) is the best
   free resource here.

**Lane B: Deep learning / open models** (GPU helps)

1. `transformers` (already installed): `pipeline("sentiment-analysis")` in
   3 lines, then tokenizers, then the underlying model classes.
2. Run local models with **Ollama** (free, no API key, OpenAI-compatible
   endpoint) — great for development.
3. PyTorch fundamentals: tensor ≈ numpy array with autograd + GPU;
   `nn.Module`; the training loop. Then LoRA fine-tuning (`peft`) when ready.
4. Resource: Karpathy's "Neural Networks: Zero to Hero" (YouTube, free),
   and the free book **d2l.ai** (Dive into Deep Learning).

## Phase 7 — MLOps awareness (your tech-lead superpower)

You already know this world — just map the vocabulary:

| DS/ML practice | Java-world analog |
|---|---|
| FastAPI service around a model | Spring Boot microservice |
| Docker slim images, multi-stage builds | same discipline, python:3.11-slim base |
| `uv`/`poetry` lockfiles | Maven/Gradle dependency locking |
| MLflow / Weights & Biases | experiment tracking ≈ CI metrics dashboards |
| pytest + data validations | unit tests + Bean Validation |
| model registry, staged rollout | artifact repo + canary deploys |

Teams fail at ML for engineering reasons, not math reasons — this is where
you add outsized value.

## The short, high-signal reading list

**Official docs (bookmark — these are genuinely good):**

- NumPy: numpy.org/doc — "Absolute basics" then "Quickstart"
- pandas: pandas.pydata.org — "10 minutes to pandas", then the "Group by" guide
- scikit-learn: scikit-learn.org — "Getting Started" + User Guide sections
- matplotlib: matplotlib.org/stable/tutorials
- transformers: huggingface.co/docs/transformers
- OpenAI Cookbook: github.com/openai/openai-cookbook (recipes for Lane A)
- Prompt Engineering Guide: promptingguide.ai

**Free books & courses (the cream):**

- *Python Data Science Handbook* — Jake VanderPlas (entire book free, GitHub)
- *Dive into Deep Learning* — d2l.ai (free, PyTorch-first)
- **Kaggle Learn** — free hands-on micro-courses: Pandas, Intro/Intermediate ML
- **DeepLearning.AI short courses** — free 1–2 h each: prompt engineering, RAG, agents
- fast.ai *Practical Deep Learning* — free, top-down (for Lane B, later)

**Paid but worth it:**

- *Hands-On Machine Learning* — Aurélien Géron (the standard sklearn+DL book)
- *Designing Machine Learning Systems* — Chip Huyen (the MLOps book — fits your role)

**Video:**

- StatQuest (statistics/ML concepts, zero fluff)
- Andrej Karpathy — "Neural Networks: Zero to Hero" + his "Intro to LLMs" talk
- 3Blue1Brown — linear algebra & neural-net intuition

## A realistic 6-week plan (nights + weekends)

| Week | Goal | Done means |
|---|---|---|
| 1 | NumPy: quickstart + ~30 of the 100 exercises | can reshape, mask, broadcast without googling every time |
| 2 | pandas: "10 minutes" + Kaggle Pandas course | mini-project: clean + group + pivot a real CSV |
| 3 | matplotlib/seaborn + sklearn Getting Started | 4 charts of your data; understand `fit`/`predict` |
| 4 | sklearn Pipeline + cross-validation | **Titanic submitted** on Kaggle |
| 5 | openai SDK: chat, structured outputs, tool calling | a CLI chatbot with tools, keys via .env |
| 6 | embeddings + chromadb + FastAPI | capstone: **RAG over your own notes, served as an API**, pushed to this repo |

After week 6: either deepen Lane A (evaluations, agents, LangGraph) or start
Lane B (Ollama locally → transformers → PyTorch training loop).

Rule that beats any roadmap: **one tiny shipped thing per week** — a script,
a chart, a README. Momentum compounds; courses don't.
