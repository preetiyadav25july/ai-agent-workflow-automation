# Excel-Driven AI Agent Workflow Engine

One reusable agent that reads business workflows from an Excel file, picks the right one for a
user's request, runs its steps through a tool registry, handles conditions/errors, and prints
**which workflow was selected, which steps ran, and the final result**.

**There are no 10 chatbots.** There is one engine (`src/agent/`). The 10 workflows are rows in
`data/workflows.xlsx`; the business logic lives in small tool functions.

## Architecture

```
User request
     |
     v
 Router  ---- Gemini (JSON) picks workflow + extracts inputs;  keyword scorer = offline fallback
     |        low confidence -> asks the user to clarify (never guesses)
     v
 Loader  ---- data/workflows.xlsx  ->  Workflow(id, steps[], decision rule, ...)
     |
     v
 Executor ---- for each step text: resolve tool -> run -> retry on TransientError
     |         NeedInfo -> stop & ask user | StepError -> fail cleanly | any bug -> caught
     v
 Tool registry  (@step("text as written in Excel"))  -> CSV readers, calculators,
     |                                                  simulated Order API, Gemini calls
     v
 Shared Context (state between steps)  ->  Result + execution trace + files in outputs/
     |
     v
 logs -> data/execution_logs.csv  (WF010 reads the agent's own history)
```

| Module | Responsibility |
|---|---|
| `loader.py` | Only place that knows the Excel layout. Splits `Steps` on `→`. |
| `router.py` | Workflow selection + input extraction. LLM first, keyword TF-IDF fallback, confidence threshold. |
| `registry.py` | `@step("...")` decorator. Exact match, then fuzzy match, else a clean "no tool" error. |
| `executor.py` | Generic runner. Zero workflow-specific code. Retries, branching outcomes, step timing. |
| `tools/*.py` | One file per workflow family. Thresholds come from `config/settings.json`. |
| `llm.py` | Gemini wrapper (`google-genai`). Every caller has a deterministic fallback. |
| `agent.py` | Orchestration + the printed report. |

### Key decisions (and why)
1. **Excel is configuration, tools are code.** Step names in Excel are the keys into the tool registry, so a new workflow that reuses existing steps needs *only an Excel row*.
2. **LLM only where judgement is needed** (routing, text generation, intent classification). Math, thresholds, matching and validation are plain Python: testable, deterministic, cheap.
3. **Three distinct outcomes, not just pass/fail:** `needs_input` (designed branch: "order not found", "dates missing"), `escalated` (WF009 rule), `failed` (real error). WF010 does not count `needs_input` as failures.
4. **Never crash, never guess:** unknown request -> clarification; unknown step -> clean failure; tool exception -> caught and logged; transient API error -> retried.
5. **Works offline.** No API key -> keyword router and template text. Same code path, so it is always demo-able.
6. **Thresholds in config** (`price_diff_threshold_pct`, failure rate, etc.), not buried in code.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # add GEMINI_API_KEY (get one at https://aistudio.google.com/apikey)
python scripts/generate_sample_data.py                 # optional: rebuilds data/*.csv (already included)
```
No key? Everything still runs in offline mode (`LLM_MODE=offline` forces it).
Model is set with `GEMINI_MODEL` (default `gemini-2.5-flash`); change it if Google retires that name.

## Run
```bash
python main.py --list                                   # workflows loaded from Excel
python main.py "Which products need restocking?"
python main.py "Where is order ORD-1001?"
python main.py "Process this vendor spreadsheet and show invalid rows." --file data/vendor_file_messy.csv
python main.py "Generate SEO content for this product." -p product_name="Trail Backpack 30L" -p category=Bags
python main.py "Create a campaign brief for the new collection."      # asks for goal + dates
python main.py --interactive                            # chat: agent asks follow-ups and resumes
python run_examples.py          # all 10 workflows  -> outputs/example_results.txt
python run_examples.py --edge   # conditions/errors -> outputs/edge_case_results.txt
python -m unittest discover -s tests -v                 # 18 tests, no API key needed
```
Sample output (WF001):
```
Selected Workflow: WF001 - Inventory Restock Check
Steps Executed:
  1. Load inventory -> OK
  2. compare current stock with minimum threshold -> OK
  ...
Result:
Products requiring restocking (6): ...
```

## The 10 workflows
| ID | Request example | Condition handled |
|---|---|---|
| WF001 Inventory Restock | "Which products need restocking?" | `current < minimum` (equal is NOT low); bad numeric rows skipped with a note |
| WF002 Price Validation | "...vendor price differs by more than 10%" | `abs(% diff) > 10`; threshold overridable ("above 24%"); unmatched SKUs reported |
| WF003 Vendor File | "Process this vendor spreadsheet..." | Column synonyms auto-detected; missing SKU/name = invalid; missing required column = clean failure; no file = asks |
| WF004 Description Generator | "Generate SEO content..." | Missing attributes marked, never invented; generated text is validated for invented materials/colours; length limits |
| WF005 Order Status | "Where is order ORD-1001?" | Order ID or email; not found -> asks another identifier; simulated API timeout (ORD-5000) auto-retried |
| WF006 Duplicates | "Find likely duplicate products..." | Same SKU = DEFINITE; high name+attribute similarity = POSSIBLE with score |
| WF007 Campaign Brief | "Create a campaign brief..." | Goal or dates missing -> asks BEFORE generating anything; invalid date ranges rejected |
| WF008 Keywords | "Classify these keywords..." | De-dupes, 4 intents, category mapping, priority, export CSV |
| WF009 Task Assignment | "Assign this urgent task to the best available developer." | Skill match + free capacity ranking; no suitable person -> ESCALATED |
| WF010 Performance Report | "Which workflows are failing most often?" | Flags failure rate > 10% or avg time > threshold; reads real agent logs |

## Adding an 11th workflow
**Case A: it reuses existing steps** (proved by `test_eleventh_workflow_needs_only_an_excel_row`)
-> add one row to `data/workflows.xlsx`. Done. No Python changes.

**Case B: it needs a new step**
1. Add the row to Excel with the new step text, e.g. `Load supplier list → check delivery delays → create alert`.
2. Write the function in `src/agent/tools/` (or a new file; add one import line in `tools/__init__.py`):
```python
@step("check delivery delays")
def check_delays(ctx):
    df = ctx.data["suppliers"]
    ...
    ctx.finish("Delayed suppliers: ...")       # final result
    return "3 suppliers delayed"               # short line shown in the trace
```
Raise `NeedInfo("question for user")` to pause and ask, `StepError` to fail, `TransientError` to retry.
`test_every_excel_step_has_a_tool` fails loudly if a row references a step with no tool.

## Simulated vs real
Real APIs are unavailable, so: inventory/orders/shipments/employees/logs are CSV files in `data/`
behind small adapter functions (`OrderAPI` in `tools/orders.py` mimics a service, including a timeout).
Swapping in a real API means changing only that adapter.

## Known limits (honest notes)
- Duplicate detection is O(n^2); for large catalogs add blocking (e.g. by brand/category) first.
- Offline text generation is template-based and plain by design; Gemini output is richer.
- Routing/extraction by Gemini needs network + key; the offline fallback is keyword-based.

## Repo layout
```
config/settings.json   thresholds & paths          data/workflows.xlsx  the 10 workflows (source of truth)
src/agent/             engine + tools/             data/*.csv           simulated systems & inputs
examples/              requests + edge cases       outputs/             saved results of every run
tests/test_agent.py    18 tests                    main.py, run_examples.py
```
