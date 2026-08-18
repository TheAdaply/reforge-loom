# conduit-verify — Akasxh/conduit as loom eval target

Source clone: `/private/tmp/claude-501/-Users-cero-Desktop-PROJECTS-reforge-workspace-re-forge-irl-data-team-collab/6458dacd-1b63-4e60-82c7-dac1ea52eb51/scratchpad/vendor/conduit`
Remote: `https://github.com/Akasxh/conduit.git` · single commit `04ce2ec "Conduit — AI-assisted API integration engine"`

**Headline: this is NOT a RealWorld/Conduit implementation.** The name collides; the software does not.
PLAN-v1.md §2 ("RealWorld / Conduit example apps") and §6 pair 2 ("comment model and serializer") are
built on a false premise. The repo is still a good eval target — with a re-designed pair 2.

---

## 1. LICENSE

**No LICENSE file exists in the repo.** Verified:

```
ls <clone> | grep -i licen   -> (no output)
grep -rin "license" README.md HOW_TO_USE.md pyproject.toml SKILLS.md   -> (no matches, exit 0 via head)
```

`pyproject.toml` has no `license` key and no `[project].license-files`. Authors field is
`[{name = "Conduit"}]` — no individual named.

**Restriction that matters:** absent an explicit license, the work is **all rights reserved** under
default copyright. We therefore may NOT copy source from it into loom, redistribute it, or vendor it
as a submodule in a public loom repo.

**Why this is nonetheless acceptable for our use:**
1. We use it as an **eval target repo** — agents *edit* it in throwaway worktrees; zero conduit code
   is copied into `loom/`. Nothing in this doc's ADOPT section is conduit source.
2. The GitHub owner is `Akasxh`, i.e. this workspace's own user (`drakathakash@gmail.com`,
   Akash). Consent is presumably available. **Action item: add an explicit MIT LICENSE to
   Akasxh/conduit before any public loom demo or submodule reference.** Until that lands, keep the
   eval target as a local clone path in config, not a git submodule in a published repo.

---

## 2. ADOPT

Patterns only, no verbatim code — the no-license status makes that a hard rule, not a preference.
Line references below are for *targeting agent edits*, not for copying.

### 2.1 What the repo actually is (Q1 evidence)

- **Language/runtime:** Python `>=3.11`, `src/` layout, hatchling build, uv-locked (`uv.lock`
  present). Frontend is a separate React + Vite + TS tree under `frontend/` (out of MVP scope per
  PLAN §1 "MVP is one language").
- **Framework:** FastAPI `>=0.115` + Starlette middleware + SQLAlchemy 2.0 async (`aiosqlite` for
  tests, `asyncpg` for prod) + Alembic + Pydantic v2. Auth is hand-rolled: PyJWT + stdlib
  PBKDF2-HMAC-SHA256.
- **Domain:** upload an OpenAPI/Swagger spec → LLM (OpenAI GPT-4.1-mini) drafts field mappings →
  deterministic rules override low-confidence LLM output → chain-test an inferred dependency DAG
  across API calls. README, lines 3–9 and "The Solution".
- **Size:** `src/` = **11,934 LOC** across ~60 modules; `tests/` = **14,573 LOC**. Package root
  `src/conduit/` with subpackages `api/routes/`, `core/`, `models/`, `schemas/`, `services/`
  (`chain/`, `config_engine/`, `llm/`, `parsing/`, `registry/`, `simulation/`).

This is a **real, dense, well-tested Python repo with genuine cross-file coupling** — which is
exactly what PLAN §6 needs from a target, independent of whether it is RealWorld.

### 2.2 Test command + measured baseline (Q2 evidence)

Setup (venv lives inside the clone under scratchpad, never global):

```
cd /Users/cero
uv sync --directory <CLONE> --extra dev          # resolved clean, no manual pinning needed
uv run --directory <CLONE> pytest -q -p no:cacheprovider --no-cov
```

Notes: `pyproject.toml [tool.pytest.ini_options] addopts` forces `--cov`; pass `--no-cov` for eval
speed. `asyncio_mode = "auto"`, `fail_under = 40` on coverage — the coverage gate will fail a plain
`pytest` run if the agent's edits drop coverage, so keep `--no-cov` in the harness.

**Actual tail (full suite, 18.20s wall):**

```
=========================== short test summary info ============================
FAILED tests/unit/test_config_pipeline.py::TestGenerateConfigurationPipeline::test_llm_path_with_augmentation
FAILED tests/unit/test_document_upload_security.py::TestPathTraversalPrevention::test_path_traversal_filename_stripped
FAILED tests/unit/test_production_hardening.py::TestGeminiClientLifecycle::test_get_llm_client_returns_singleton
FAILED tests/unit/test_production_hardening.py::TestGeminiClientLifecycle::test_lifespan_closes_shared_client
======================= 4 failed, 1039 passed in 18.20s ========================
```

**Tests do NOT fully pass on main. Baseline is 1039 passed / 4 failed.** All four were re-run in
isolation and failed identically, so they are **deterministic pre-existing failures, not
order-dependent flakes**:

| Node ID | Root cause (from captured error) |
|---|---|
| `tests/unit/test_config_pipeline.py::TestGenerateConfigurationPipeline::test_llm_path_with_augmentation` | `ValueError: OpenAI API key is not set. Set CONDUIT_OPENAI_API_KEY in .env or pass api_key explicitly.` |
| `tests/unit/test_production_hardening.py::TestGeminiClientLifecycle::test_get_llm_client_returns_singleton` | same missing-API-key path (LLM client singleton) |
| `tests/unit/test_production_hardening.py::TestGeminiClientLifecycle::test_lifespan_closes_shared_client` | `sqlite3.OperationalError: no such table: users` — lifespan test bypasses the schema-creating fixture |
| `tests/unit/test_document_upload_security.py::TestPathTraversalPrevention::test_path_traversal_filename_stripped` | `pypdf.errors.PdfStreamError: Stream has ended unexpectedly` — truncated PDF fixture vs pypdf 4.x |

**This matters directly to the eval.** PLAN §6 names *post-merge test failures* as the headline
metric. A non-zero baseline silently inflates arm A and arm B alike. The harness MUST pin the
baseline. Canonical eval command:

```
uv run --directory <TARGET_WORKTREE> pytest -q -p no:cacheprovider --no-cov \
  --deselect tests/unit/test_config_pipeline.py::TestGenerateConfigurationPipeline::test_llm_path_with_augmentation \
  --deselect tests/unit/test_document_upload_security.py::TestPathTraversalPrevention::test_path_traversal_filename_stripped \
  --deselect tests/unit/test_production_hardening.py::TestGeminiClientLifecycle
```

Expected green baseline after deselect: **1039 passed, 0 failed**. `eval/metrics.py` should compute
post-merge failures as `failures_after_merge − 0` against that command, and assert the baseline is
green *before* the arms run (a pre-flight `assert_baseline_green()` in the harness).

Secondary: the 18s suite is fast enough to run after every arm without budget concern, and
`--durations` is unnecessary.

### 2.3 Collision-pair symbols (Q3) — real, verified qualnames

All paths relative to the clone root. Format is loom's canonical node-ref convention (GATE-1
fix 2, from serena.md C1): `relative/path.py::Qualname`, with `/` — Serena's real
`NAME_PATH_SEP` — separating components *inside* the qualname (`Class/method`, never
`Class.method`).

**Pair 1 — authenticate-shaped. EXISTS, strong fit.**

| Role | Symbol | Line |
|---|---|---|
| The authenticate function (login path) | `src/conduit/api/routes/auth.py::login` | 131 |
| Password check it calls | `src/conduit/api/routes/auth.py::_verify_password` | 33 |
| Password hasher (PBKDF2, 260k iters) | `src/conduit/api/routes/auth.py::_hash_password` | 27 |
| Token minting helper | `src/conduit/api/routes/auth.py::_make_tokens` | 82 |
| Token issue | `src/conduit/core/security.py::create_jwt_token` | 42 |
| **Token verify — the shared node** | `src/conduit/core/security.py::decode_jwt_token` | 50 |
| Per-request auth enforcement | `src/conduit/core/middleware.py::TenantMiddleware/dispatch` | 53 |
| Other `decode_jwt_token` call sites | `src/conduit/api/routes/auth.py::refresh_token` (149), `::me` (184) | — |

Signature ground truth:

```python
# src/conduit/api/routes/auth.py:131
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> LoginResponse:

# src/conduit/core/security.py:50
def decode_jwt_token(token: str) -> dict[str, str]:
```

**Why this collides for real.** "Harden authenticate" (agent A) naturally edits `login` (L131) —
rate limiting, timing-safe failure, lockout on `user.is_active` — and pulls in `_verify_password`
(L33). "Cache authenticate results" (agent B) naturally edits `TenantMiddleware/dispatch` (L53) to
memoize `decode_jwt_token` + the user lookup. **Both converge on
`src/conduit/core/security.py::decode_jwt_token`** via the one-hop CALLS expansion PLAN §4.2
specifies, and both touch the JWT payload shape minted in `_make_tokens` (L82: `sub`, `email`,
`role`, `tenant_id`, `tenant_name`, `type`). That is a *semantic* conflict — a cache keyed on a
payload field that A renames merges clean and breaks at runtime. Exactly the failure loom claims to
prevent. Note also `core/rate_limiter.py` already exists, so hardening has a real target to wire.

**Pair 2 — comment model + serializer. DOES NOT EXIST.**

```
grep -rni "comment" <CLONE>/src --include="*.py" -l
-> src/conduit/services/search.py     (only file; it is not a comment feature)
```

There is no `Comment` model, no comment route, no comment serializer. `models/` contains exactly:
`adapter.py, audit.py, base.py, configuration.py, document.py, simulation.py, tenant.py, user.py,
webhook.py`. Pair 2 as written in PLAN §6 is unimplementable here.

**Adopt this substitute pair instead — model + serializer + two crossing features:**

| Role | Symbol | Line |
|---|---|---|
| The "model" | `src/conduit/models/document.py::Document` | 9 |
| The "serializer" (write side) | `src/conduit/schemas/documents.py::DocumentUploadResponse` | 85 |
| The "serializer" (read side) | `src/conduit/schemas/documents.py::DocumentDetailResponse` | 98 |
| Shared parse payload both features read | `src/conduit/schemas/documents.py::ParsedDocumentResult` | 67 |
| Endpoint DTO | `src/conduit/schemas/documents.py::ExtractedEndpoint` | 35 |
| Route: create | `src/conduit/api/routes/documents.py::upload_document` | 39 |
| Route: read | `src/conduit/api/routes/documents.py::get_document` | 216 |
| Route: mutate | `src/conduit/api/routes/documents.py::reanalyze_document` | 251 |
| Route: delete | `src/conduit/api/routes/documents.py::delete_document` | 350 |
| Route: list | `src/conduit/api/routes/documents.py::list_documents` | 391 |
| Producer of the serializer payload | `src/conduit/services/parsing/document_parser.py::DocumentParser/build_result_from_llm` | 234 |
| Async producer | `src/conduit/services/parsing/document_parser.py::DocumentParser/parse_with_llm` | 309 |

`Document` columns (models/document.py L14–25): `filename, file_type, file_size, doc_type, status,
raw_text, parsed_result, error_message`, plus `UUIDMixin/TenantMixin/TimestampMixin`.

Suggested task pair, isomorphic to the plan's "editing vs moderation" intent:
- **Task 2A — document editing:** allow renaming/re-typing a stored document. Touches `Document`
  (L9), `DocumentDetailResponse` (L98), `reanalyze_document` (L251).
- **Task 2B — document retention/moderation:** add a `status`-driven quarantine + soft-delete with
  redaction. Touches `Document` (L9), `DocumentUploadResponse` (L85), `delete_document` (L350),
  `list_documents` (L391).
Both cross `models/document.py::Document` and `schemas/documents.py` — a genuine model+serializer
double collision, with four import-fanout consumers proven by grep:
`api/routes/adapters.py:26`, `api/routes/documents.py:25`, `services/chain/heuristics.py:19,21,35`,
`services/parsing/document_parser.py:12`, `tests/unit/test_chain.py:5`.

### 2.4 Indexer smoke-test value (adopt as M1 acceptance fixture)

PLAN §5 M1 requires "20 known call sites" spot-checked. This repo supplies them cheaply and with
adversarial variety the indexer must survive:

- **Function-local imports** — `auth.py:115` (`from uuid import uuid4` inside `register`),
  `auth.py:151` and `:186` (`import jwt as pyjwt` inside a function body), `auth.py:167`
  (a second `from datetime import timedelta` shadowing the module-level one). A naive
  module-level-only IMPORTS query misses all four.
- **`TYPE_CHECKING`-guarded imports** — `services/chain/heuristics.py:19` imports
  `ExtractedEndpoint` under a type-checking guard while `:21` imports `InjectRule` at runtime.
  The indexer must decide whether guarded imports produce IMPORTS edges (recommendation: yes, with
  an edge attribute, so `assumes` still catches signature drift).
- **Decorator-wrapped defs** — every FastAPI route is `@router.post(...)` over `async def`; the
  tree-sitter capture must anchor the qualname to the `function_definition`, not the decorator.
- **`Depends(get_db)` default-arg call sites** — CALLS edges hiding in parameter defaults
  (`auth.py:106,131,149`; all of `api/dependencies.py`). Easy to miss, high signal for loom.
- **Static/class methods on a large class** — `DocumentParser` has 27 methods including
  `@staticmethod _normalize_doc_type` (L182), `_resolve_ref` (L564), `_infer_field_type` (L941).
  Qualname must render `services/parsing/document_parser.py::DocumentParser/_resolve_ref` (GATE-1 fix 2: `/` within the symbol part).

Use these as the literal M1 acceptance checklist.

---

## 3. ADAPT

1. **Pair 2 is re-specified**, not deleted. Replace PLAN §6's "add comment editing vs add comment
   moderation, both crossing the comment model and serializer" with the Document
   editing-vs-retention pair in §2.3 above. Same collision shape (model + serializer, two features),
   real symbols.
2. **Target repo is a local clone path, not a git submodule.** PLAN §2 says "Lands in:
   `eval/target-repo` as a submodule." With no license on the source, do not commit a submodule
   pointer in a repo we intend to publish. Use `eval/config.yaml: target_repo_path: <abs path>` and
   have the harness `git clone --local` into a scratch worktree per arm. Revisit once a LICENSE
   lands upstream.
3. **Baseline-failure quarantine becomes a first-class harness concept.** `eval/metrics.py` needs
   `baseline_failures: set[str]` loaded from config, and post-merge failures reported as
   `new_failures = observed − baseline`. Without this the headline metric is off by 4 in every arm.
   Pair it with a pre-flight assertion that the un-edited worktree is green under the deselect list.
4. **Pin `CONDUIT_OPENAI_API_KEY` out of the loop.** Two of the four baseline failures are just a
   missing key. Do NOT "fix" them by supplying a real key — that makes eval runs network-dependent,
   nondeterministic, and billable. Keep them deselected.
5. **Run pytest with `--no-cov`.** The repo's `addopts` forces coverage with `fail_under = 40`; an
   agent's edit can trip the coverage gate and be scored as a "post-merge test failure" that is
   really a coverage failure. Strip it in the harness.
6. **Language choice is settled: Python.** PLAN §1 says "pick the demo repo's language, Python or
   TypeScript." The target has both (`src/` Python, `frontend/` React+TS). Scope the indexer and the
   eval to `src/**/*.py` only; exclude `frontend/`, `alembic/`, and `tests/` from claimable nodes
   (tests import everything and are exactly the "low-signal edges" PLAN §7 warns about — mute them
   from day one, not at 10 users).
7. **Claim granularity is validated by this repo.** `DocumentParser` is 27 methods in one ~950-line
   file. File-granularity claims would serialize both pair-2 agents on `document_parser.py` for no
   reason. Symbol granularity is load-bearing here — good, that is the plan's bet.
8. **Use `uv sync --extra dev` per worktree, once, and reuse `.venv`** across arms if the worktrees
   share a filesystem; a cold `uv sync` dominates the 18s test time and would pollute wall-clock
   metrics.

---

## 4. REJECT

1. **Reject "RealWorld / Conduit example apps" as a single manifest line (PLAN §2).** The two are
   unrelated. Akasxh/conduit is an API-integration engine; RealWorld is a Medium clone spec. Keeping
   them fused in the manifest will keep producing wrong plans.
2. **Reject the comment-model/serializer pair (PLAN §6 pair 2) against this repo.** No comment
   model exists — grep proves only `services/search.py` even contains the substring.
3. **Reject vendoring this repo as a git submodule** in any published loom repo — no license.
   (See ADAPT 2.)
4. **Reject the `frontend/` tree entirely for MVP.** React + Vite + TS; indexing it means a second
   tree-sitter language and doubles M1 for zero eval value. PLAN §1 already forbids multi-language.
5. **Reject "fix the 4 failing tests first."** They are environmental (API key, PDF fixture, a test
   that skips schema creation) and unrelated to any collision pair. Fixing them burns MVP hours and
   changes the target repo out from under both arms. Quarantine, don't repair.
6. **Reject using the LLM-touching services (`services/llm/`, `services/parsing/*llm*`) as any part
   of a task pair.** They require a live OpenAI key; agent edits there produce nondeterministic test
   outcomes and real spend. Keep collision pairs on `auth`/`security`/`middleware` and
   `models`/`schemas`/`routes`, all of which are fully offline-testable.
7. **Reject the Webhook model as the pair-2 substrate** even though `models/webhook.py::Webhook`
   (L9) + `schemas/webhooks.py::WebhookCreate` (L8) / `WebhookResponse` (L15) /
   `WebhookDeliveryResponse` (L26) look structurally similar. Document has richer real coupling
   (six route handlers, a 950-line producer in `document_parser.py`, five import sites), which is
   what makes the collision non-trivial rather than a two-file diff.

---

## 5. CORRECTIONS to PLAN-v1.md

| Plan location | Stale / wrong | Correct |
|---|---|---|
| §2, lines 72–74 | "RealWorld / Conduit example apps — Take: the demo codebase and the overlapping task-pair design. Lands in: `eval/target-repo` as a submodule." | The available clone (`Akasxh/conduit`) is **not** a RealWorld implementation. It is a FastAPI + SQLAlchemy 2.0 async + Pydantic v2 API-integration engine, ~11.9k LOC src / 14.6k LOC tests, single commit `04ce2ec`. Rewrite the manifest entry as "Eval target repo: Akasxh/conduit (FastAPI, Python 3.11)." Drop "submodule" → local clone path (no license). |
| §6, "Pair 2, add comment editing versus add comment moderation, both crossing the comment model and serializer" | There is no comment model, route, or serializer in the target. | Replace with Document editing vs Document retention/moderation, crossing `src/conduit/models/document.py::Document` (L9) and `src/conduit/schemas/documents.py::DocumentUploadResponse` (L85) / `DocumentDetailResponse` (L98). |
| §6, "Pair 1, harden authenticate versus cache authenticate results" | Directionally correct but there is no function literally named `authenticate`. | The authenticate-shaped symbol is `src/conduit/api/routes/auth.py::login` (L131). The node both tasks collide on is `src/conduit/core/security.py::decode_jwt_token` (L50); the caching task's edit site is `src/conduit/core/middleware.py::TenantMiddleware/dispatch` (L53). Pair 1 survives verbatim in spirit, with these three symbols named explicitly. |
| §6, "Post-merge test failures … this is the headline" | Assumes a green baseline. | Target main is **1039 passed / 4 failed**. Four deterministic pre-existing failures must be deselected (list in §2.2) or the headline metric is wrong by +4 in every arm. Harness needs a `baseline_failures` config and a pre-flight green assertion. |
| §5, M1 accept: "spot-checked against 20 known call sites" | Unspecified. | Use the checklist in §2.4: function-local imports (`auth.py:115,151,167,186`), `TYPE_CHECKING`-guarded imports (`services/chain/heuristics.py:19` vs `:21`), decorator-wrapped `async def` routes, `Depends()` call sites in parameter defaults, and `@staticmethod`s inside `DocumentParser`. |
| §1, "MVP is one language (pick the demo repo's language, Python or TypeScript)" | Ambiguous — the target has both. | Resolved: **Python**. Index `src/**/*.py` only; exclude `frontend/`, `alembic/`, `tests/`. |
| §7, "muting of low-signal edges (test files importing everything)" — framed as a 10-user tuning knob | The target's `tests/` is larger than its `src/` (14,573 vs 11,934 LOC) and imports nearly every module. | Test-file edge muting is needed **at 2 users on this repo**, not at 10. Move it into MVP config defaults. |

### Verdict (Q4): **YES — fit as eval target, with pair 2 re-specified.**

Against what PLAN §6 actually requires: Python ✓ · real cross-file coupling ✓ · a fast deterministic
test suite (18.2s, 1039 green after quarantine) ✓ · an authenticate-shaped path with a genuine
shared downstream node ✓ · a model+serializer pair with multi-consumer fanout ✓ · offline-runnable
✓. What dies is the *comment-based pair-2 design*, not the target repo. The only open risk is
licensing, and it is cheap to close (§1 action item).

**Fallback, only if the team insists on literal RealWorld parity:** clone
`gothinkster/django-realworld-example-app` — it ships an actual `Comment` model + serializer, so
PLAN §6 pair 2 runs unmodified; cost is a smaller, thinner call graph that makes the indexer's
one-hop expansion look trivially easy. Second fallback: generate a purpose-built ~800-LOC FastAPI
demo app with the two collision pairs designed in — full control and a guaranteed-green baseline,
at the price of the eval no longer being on real third-party code.
