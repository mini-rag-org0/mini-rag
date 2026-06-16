# Mini-RAG Production System Review

**Date:** 2026-06-14  
**Scope:** Full codebase & infrastructure audit of the live AWS Lightsail deployment  
**Severity Scale:** `P0` Critical / Breaking — `P1` High — `P2` Medium — `P3` Low

---

## 1. System Overview

### 1.1 Deployment Architecture

```
                           ┌─────────────────────────────────┐
                           │       AWS Lightsail Instance     │
┌──────────┐    HTTPS      │  ┌──────────────┐               │
│ WhatsApp │──────────────►│  │  Nginx :80/443│               │
│ Cloud API│               │  │  (TLS termination)            │
└──────────┘               │  └──────┬───────┘               │
                           │         │ proxy_pass             │
┌──────────┐    HTTPS      │  ┌──────▼───────┐               │
│ Streamlit│───────────────►  │  FastAPI :8000│               │
│  (app.py)│  localhost     │  │  (uvicorn 4w) │              │
└──────────┘               │  └──┬────┬──────┘               │
                           │     │    │                       │
                           │  ┌──▼──┐ ┌▼─────────┐           │
                           │  │PGVec│ │ Qdrant   │           │
                           │  │:5432│ │ :6333    │           │
                           │  └─────┘ └──────────┘           │
                           │                                  │
                           │  ┌────────────┐ ┌────────────┐  │
                           │  │ Prometheus │ │  Grafana   │  │
                           │  │ :9090      │ │  :3000     │  │
                           │  └────────────┘ └────────────┘  │
                           │  ┌───────────┐ ┌─────────────┐  │
                           │  │ Node      │ │ PG          │  │
                           │  │ Exporter  │ │ Exporter    │  │
                           │  │ :9100     │ │ :9187       │  │
                           │  └───────────┘ └─────────────┘  │
                           └─────────────────────────────────┘
```

**Components:**
- **Nginx** — TLS termination via Let's Encrypt, reverse-proxy to FastAPI. Domain: `ibrahem-basem.duckdns.org`.
- **FastAPI** — Core API server (uvicorn, 4 workers). Handles REST endpoints, WhatsApp webhook, RAG pipeline.
- **PostgreSQL 17 + pgvector 0.8** — Primary relational store (projects, assets, chunks, chat_messages) + vector similarity search.
- **Qdrant v1.13.6** — Alternative vector DB (deployed but currently unused; config set to `PGVECTOR`).
- **Prometheus + Grafana + Node/PG Exporters** — Observability stack.
- **Streamlit** — Frontend chat UI (`app.py`), talks to FastAPI via `localhost:8000`.

### 1.2 MVC Architecture Flow

```
Routes (data.py, nlp.py, whatsapp.py)
  └─► Controllers (DataController, ProcessController, NLPController)
        ├─► Models (ProjectModel, AssetModel, ChunkModel, ChatModel)
        │     └─► DB Schemes (SQLAlchemy ORM → PostgreSQL)
        └─► Stores
              ├─► LLM Providers (OpenAIProvider, CoHereProvider)
              └─► VectorDB Providers (PGVectorProvider, QdrantDBProvider)
```

---

## 2. Critical Bugs Found

### BUG-01 `[P0]` CoHere `generate_text` Never Returns a Value
**File:** `src/stores/llm/providers/CoHereProvider.py` **Lines 47–70**

The `generate_text` method calls `self.client.chat()` but the function body never executes a `return` statement for the success path. It only logs an error when `response.text` is missing, but even then does not return `None` explicitly. The function implicitly returns `None` for **every** call, breaking the entire RAG answer pipeline when CoHere is the generation backend.

```python
# CURRENT (broken) — lines 60-70
response = self.client.chat(...)
if not response or not response.text:
    self.logger.error("Error while generating text whit CohHere")
# ← Missing: return response.text
```

**Fix:**
```python
response = self.client.chat(
    model=self.generation_model_id,
    chat_history=chat_history,
    message=self.process_text(prompt),
    temperature=temperature,
    max_tokens=max_output_tokens
)

if not response or not response.text:
    self.logger.error("Error while generating text with CoHere")
    return None

return response.text
```

---

### BUG-02 `[P0]` Mutable Default Argument `chat_history=[]` Shared Across All Requests
**Files:**
- `src/stores/llm/providers/OpenAIProvider.py` **Line 50**: `def generate_text(self, prompt: str, chat_history: list = [], ...)`
- `src/stores/llm/providers/CoHereProvider.py` **Line 47**: `def generate_text(self, prompt: str, chat_history: list = [], ...)`
- `src/stores/llm/LLMInterface.py` **Line 15**: `def generate_text(self, prompt: str, chat_history: list = [], ...)`
- `src/stores/llm/templates/template_parser.py` **Line 28**: `def get(self, group: str, key: str, vars: dict = {})`

Python evaluates default arguments **once at function definition time**. The same `list`/`dict` object is reused across every call. In `OpenAIProvider.generate_text`, line 64 does `chat_history.append(...)`, which **mutates the shared default list**. This means:
1. Chat history from User A leaks into User B's request.
2. The list grows indefinitely, eventually consuming all memory.

**Fix (apply to all four locations):**
```python
def generate_text(self, prompt: str, chat_history: list = None, ...):
    if chat_history is None:
        chat_history = []
    ...

def get(self, group: str, key: str, vars: dict = None):
    if vars is None:
        vars = {}
    ...
```

---

### BUG-03 `[P0]` `OpenAIProvider.generate_text` Mutates the Caller's `chat_history` List
**File:** `src/stores/llm/providers/OpenAIProvider.py` **Lines 64–66**

```python
chat_history.append(
    self.construct_prompt(prompt=prompt, role=OpenAIEnum.USER.value)
)
```

This appends the user prompt directly to the caller's list object. In `NLPController.answer_rag_question` (line 196–198), the `base_chat_history` variable is passed, and it gets silently modified. This corrupts the returned `base_chat_history`, and when combined with BUG-02's mutable default, causes cross-request state pollution.

**Fix:** Copy the list before mutating:
```python
def generate_text(self, prompt: str, chat_history: list = None, ...):
    if chat_history is None:
        chat_history = []
    messages = chat_history.copy()
    messages.append(
        self.construct_prompt(prompt=prompt, role=OpenAIEnum.USER.value)
    )
    response = self.client.chat.completions.create(
        model=self.generation_model_id,
        messages=messages,
        ...
    )
```

---

### BUG-04 `[P0]` `LLMProviderFactory` Swaps Temperature and Max-Tokens Values
**File:** `src/stores/llm/LLMProviderFactory.py` **Lines 9–24**

The factory passes constructor arguments to OpenAI/CoHere providers with **swapped config values**:

```python
return OpenAIProvider(
    ...
    default_generation_max_output_tokens=self.config.INPUT_DEFAULT_MAX_CHARACTERS,  # ← Should be GENERATION_DEFAULT_MAX_TOKENS
    default_generation_temprature=self.config.GENERATION_DEFAULT_MAX_TOKENS,         # ← Should be GENERATION_DEFAULT_TEMPERATURE
    default_input_max_characters=self.config.GENERATION_DEFAULT_TEMPERATURE           # ← Should be INPUT_DEFAULT_MAX_CHARACTERS
)
```

With the production `.env` values (`INPUT_DEFAULT_MAX_CHARACTERS=1024`, `GENERATION_DEFAULT_MAX_TOKENS=200`, `GENERATION_DEFAULT_TEMPERATURE=0.1`):
- `default_generation_max_output_tokens` = 1024 (intended: 200) — over-generates tokens
- `default_generation_temprature` = 200 (intended: 0.1) — **temperature of 200 will produce complete garbage output or API errors**
- `default_input_max_characters` = 0.1 (intended: 1024) — truncates all input to <1 character

**Fix:**
```python
return OpenAIProvider(
    api_key=self.config.OPENAI_API_KEY,
    api_url=self.config.OPENAI_API_URL,
    default_input_max_characters=self.config.INPUT_DEFAULT_MAX_CHARACTERS,
    default_generation_max_output_tokens=self.config.GENERATION_DEFAULT_MAX_TOKENS,
    default_generation_temprature=self.config.GENERATION_DEFAULT_TEMPERATURE,
)
```

---

### BUG-05 `[P1]` `QdrantDBProvider.delete_collection` Missing `await` on Async Call
**File:** `src/stores/vectordb/providers/QdrantDBProvider.py` **Line 42**

```python
async def delete_collection(self, collection_name: str):
    if self.is_collection_existed(collection_name):  # ← Missing await
```

`is_collection_existed` is an `async def`, so calling it without `await` always returns a truthy coroutine object (never `False`). Same issue on lines 50, 53, 70.

Additionally, on line 21:
```python
self.distance_method == models.Distance.DOT  # ← comparison (==) instead of assignment (=)
```

**Fix:**
```python
if await self.is_collection_existed(collection_name):
```
And: `self.distance_method = models.Distance.DOT`

---

### BUG-06 `[P1]` `ProcessController.process_simpler_splitter` Ignores `overlap_size` and Discards All Metadata
**File:** `src/controllers/ProcessController.py` **Lines 85–111**

The `process_file_content` method accepts `overlap_size` but passes only `chunk_size` to `process_simpler_splitter`. The splitter itself has **no sliding-window logic** — it simply fills chunks sequentially. The `overlap_size` parameter from the API request is silently ignored.

Additionally, every chunk is created with `metadata={}` (line 100), discarding all source document metadata (page numbers, filenames, etc.).

**Fix:**
```python
def process_simpler_splitter(self, texts: List[str], metadatas: List[dict],
                             chunk_size: int, overlap_size: int = 0,
                             splitter_tag: str = "\n"):
    full_text = " ".join(texts)
    combined_metadata = metadatas[0] if metadatas else {}
    lines = [doc.strip() for doc in full_text.split(splitter_tag) if len(doc.strip()) > 1]

    chunks = []
    current_chunk_lines = []
    current_len = 0

    for line in lines:
        current_chunk_lines.append(line)
        current_len += len(line) + len(splitter_tag)
        if current_len >= chunk_size:
            chunks.append(Document(
                page_content=splitter_tag.join(current_chunk_lines).strip(),
                metadata=combined_metadata.copy()
            ))
            # Implement overlap: keep trailing lines up to overlap_size characters
            overlap_lines = []
            overlap_len = 0
            for prev_line in reversed(current_chunk_lines):
                if overlap_len + len(prev_line) > overlap_size:
                    break
                overlap_lines.insert(0, prev_line)
                overlap_len += len(prev_line)
            current_chunk_lines = overlap_lines
            current_len = overlap_len

    if current_chunk_lines:
        chunks.append(Document(
            page_content=splitter_tag.join(current_chunk_lines).strip(),
            metadata=combined_metadata.copy()
        ))
    return chunks
```

---

### BUG-07 `[P1]` `get_setting()` Re-Parses `.env` on Every Single Call
**File:** `src/helpers/config.py` **Line 60–61**

```python
def get_setting():
    return Setting()
```

`Setting()` instantiates a new Pydantic `BaseSettings` object, which reads and parses the `.env` file from disk **on every invocation**. This function is called in almost every route handler and controller constructor — potentially dozens of times per request.

**Fix:** Use `functools.lru_cache` to create a singleton:
```python
from functools import lru_cache

@lru_cache()
def get_setting():
    return Setting()
```

---

### BUG-08 `[P1]` `uvicorn --workers 4` Breaks Shared In-Process State
**File:** `docker/minirag/Dockerfile` **Line 35**

```dockerfile
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

With `--workers 4`, uvicorn spawns 4 separate OS processes via `multiprocessing`. Each process runs `startup_span()` independently, creating its own `db_engine`, `vectordb_client`, `generation_client`, etc. on `app`. This is technically valid for the PostgreSQL-backed providers.

However, if `VECTOR_DB_BACKEND` were set to `QDRANT` (file-based client via `path=`), the 4 workers would lock-fight on the Qdrant storage directory.

More critically, the deprecated `app.on_event("startup")` API (line 69–70 of `main.py`) is used instead of the recommended `lifespan` context manager. FastAPI has deprecated `on_event` since v0.93.

---

## 3. Security Vulnerabilities

### SEC-01 `[P0]` Plaintext API Keys Committed to Version Control
**File:** `docker/env/.env.app` **Lines 25–27, 50–51**

Production secrets are **hardcoded in a tracked file**:
```
OPENAI_API_KEY = "sk-proj-qVnb0zlrj4EGU83APi..."
COHERE_API_KEY = "Pv4ZW2hcxVQrjrNd7Oh..."
WHATSAPP_API_TOKEN=EAAObkWpktEABO...
POSTGRES_PASSWORD="248e5678"
```

These keys are fully exposed in the git history. Anyone with repo access has full control of the OpenAI account, Cohere account, WhatsApp Business account, and database.

**Immediate Actions:**
1. Rotate **all** API keys and database passwords immediately.
2. Add `docker/env/.env.app`, `docker/env/.env.postgres`, `docker/env/.env.grafana`, `docker/env/.env.postgres-exporter` to `.gitignore`.
3. Use Docker Secrets or AWS Parameter Store for production credentials.

---

### SEC-02 `[P0]` SQL Injection via Unparameterized Table Names in PGVectorProvider
**File:** `src/stores/vectordb/providers/PGVectorProvider.py`

Multiple methods interpolate `collection_name` directly into SQL strings using f-strings:

| Line | Code |
|------|------|
| 80 | `sql_text(f'SELECT COUNT(*) FROM {collection_name}')` |
| 106 | `sql_text(f'DROP TABLE IF EXISTS {collection_name}')` |
| 124 | `sql_text(f'CREATE TABLE {collection_name} (...)')` |
| 167 | `sql_text(f'SELECT COUNT(*) FROM {collection_name}')` |
| 179 | `f"CREATE INDEX {index_name} ON {collection_name} ..."` |
| 192 | `f"DROP INDEX IF EXISTS {index_name}"` |
| 213 | `f'INSERT INTO {collection_name} ...'` |
| 269 | `f'INSERT INTO {collection_name} ...'` |
| 294 | `f'SELECT ... FROM {collection_name} ...'` |
| 297 | `f'LIMIT {limit}'` |

The `collection_name` is constructed from `project_id` in `NLPController.create_collection_name`, which receives its value from URL path parameters (`/api/v1/nlp/index/push/{project_id}`). While `project_id` is typed as `int` in the route signature, the `create_collection_name` method accepts `str` and never validates.

`limit` on line 297 is also directly interpolated. Although FastAPI's Pydantic model constrains it, defense-in-depth demands parameterization.

**Fix:** Validate collection names with a whitelist regex and use parameterized `LIMIT`:
```python
import re

def _validate_identifier(self, name: str):
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
        raise ValueError(f"Invalid SQL identifier: {name}")
    return name

# For LIMIT:
search_sql = sql_text(
    f'SELECT ... FROM {self._validate_identifier(collection_name)} '
    'ORDER BY score DESC LIMIT :limit'
)
result = await session.execute(search_sql, {"vector": vector, "limit": limit})
```

---

### SEC-03 `[P1]` WhatsApp Webhook Token Verification Vulnerable to Timing Attack
**File:** `src/routes/whatsapp.py` **Line 122**

```python
if mode == "subscribe" and token == verify_token:
```

Python's `==` operator for strings short-circuits on the first differing byte, allowing a timing side-channel attack to brute-force the `verify_token` character-by-character.

**Fix:**
```python
import hmac

if mode == "subscribe" and hmac.compare_digest(token, verify_token):
```

---

### SEC-04 `[P1]` Path Traversal in File Upload/Delete Operations
**File:** `src/controllers/DataController.py` **Line 54**

```python
cleaned_file_name = re.sub(r'[^\w.]', '', orig_file_name.strip())
```

This regex retains `.` characters, allowing filenames like `....etc.passwd` or relative path components. While it removes `/`, the `\w` character class includes Unicode word characters in Python 3, which could be exploited in edge cases.

More critically, the `get_project_path` method (ProjectController.py line 13) uses `str(project_id)` in a path join without validating that `project_id` doesn't contain path traversal sequences when it arrives as a string.

**Fix:**
```python
def get_clean_file_name(self, orig_file_name: str):
    # Extract only the basename, strip leading dots
    basename = os.path.basename(orig_file_name)
    cleaned = re.sub(r'[^\w.]', '', basename)
    cleaned = cleaned.lstrip('.')  # Prevent hidden files / traversal
    if not cleaned:
        cleaned = "unnamed_file"
    return cleaned
```

---

### SEC-05 `[P2]` Exposed Internal Services — No Network Isolation
**File:** `docker/docker-compose.yml`

All services bind to `0.0.0.0` with host ports:
- PostgreSQL `:5432` — Direct public access to the database
- Qdrant `:6333/:6334` — Unauthenticated vector DB access
- Prometheus `:9090` — Metrics data exposure
- Grafana `:3000` — Dashboard access (check if default credentials changed)
- Node Exporter `:9100` — System metrics leak
- PG Exporter `:9187` — Database metrics leak

**Fix:** Remove host port bindings for internal services; only expose Nginx:
```yaml
pgvector:
  # ports:          # REMOVE — accessed only via backend network
  #   - "5432:5432"
  expose:
    - "5432"
```

---

## 4. Performance Issues

### PERF-01 `[P0]` Synchronous LLM & Embedding Calls Block the Async Event Loop
**Files:**
- `src/stores/llm/providers/OpenAIProvider.py` **Line 68**: `self.client.chat.completions.create(...)` — synchronous `openai.OpenAI` (not `AsyncOpenAI`)
- `src/stores/llm/providers/OpenAIProvider.py` **Line 96**: `self.client.embeddings.create(...)` — synchronous
- `src/stores/llm/providers/CoHereProvider.py` **Line 60**: `self.client.chat(...)` — synchronous `cohere.Client` (not `cohere.AsyncClient`)
- `src/stores/llm/providers/CoHereProvider.py` **Line 88**: `self.client.embed(...)` — synchronous
- `src/controllers/NLPController.py` **Lines 47, 82, 130, 196**: calls `embed_text()` and `generate_text()` which are synchronous

These are network-bound calls taking 500ms–5s each. In a FastAPI async handler, synchronous calls block the event loop thread, preventing **all other requests** from being processed during that time. With 4 workers, at most 4 concurrent LLM requests can proceed — any additional requests queue up.

**Fix for OpenAI:**
```python
from openai import AsyncOpenAI

class OpenAIProvider(LLMInterface):
    def __init__(self, ...):
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=...)

    async def generate_text(self, prompt, chat_history=None, ...):
        messages = (chat_history or []).copy()
        messages.append(self.construct_prompt(prompt=prompt, role=OpenAIEnum.USER.value))
        response = await self.client.chat.completions.create(
            model=self.generation_model_id,
            messages=messages,
            max_tokens=max_output_tokens,
            temperature=temperature,
        )
        ...

    async def embed_text(self, text, ...):
        response = await self.client.embeddings.create(
            model=self.embedding_model_id,
            input=text,
        )
        ...
```

**Fix for CoHere:**
```python
import cohere

class CoHereProvider(LLMInterface):
    def __init__(self, ...):
        self.client = cohere.AsyncClient(api_key=self.api_key)

    async def generate_text(self, ...):
        response = await self.client.chat(...)
        ...
```

---

### PERF-02 `[P1]` `WhatsAppService` Creates a New `httpx.AsyncClient` Per Message
**File:** `src/services/whatsapp_service.py` **Lines 32–38**

```python
async with httpx.AsyncClient() as client:
    response = await client.post(...)
```

Each call opens a new TCP connection, performs TLS handshake with Meta's servers, sends the request, and tears down. For high-volume WhatsApp messaging, this adds ~200ms overhead per message.

**Fix:** Use a persistent client:
```python
class WhatsAppService:
    def __init__(self, api_token: str, phone_number_id: str):
        ...
        self._client = httpx.AsyncClient(
            headers=self.headers,
            timeout=10.0,
        )

    async def send_message(self, to_phone_number: str, text: str) -> bool:
        response = await self._client.post(self.base_url, json=payload)
        ...

    async def close(self):
        await self._client.aclose()
```

---

### PERF-03 `[P1]` Synchronous File I/O in `ProcessController.get_file_content`
**File:** `src/controllers/ProcessController.py` **Line 54**

```python
def get_file_content(self, file_id: str):
    loader = self.get_file_loader(file_id=file_id)
    if loader:
        return loader.load()  # ← Synchronous disk I/O
```

LangChain's `TextLoader.load()` and `PyMuPDFLoader.load()` are synchronous. Called inside an async route handler (`process_endpoint`), this blocks the event loop during file parsing — especially problematic for large PDFs.

**Fix:** Offload to a thread pool:
```python
import asyncio

async def get_file_content(self, file_id: str):
    loader = self.get_file_loader(file_id=file_id)
    if loader:
        return await asyncio.to_thread(loader.load)
    return None
```

---

### PERF-04 `[P2]` `tqdm` Progress Bar in Server-Side Request Handler
**File:** `src/routes/nlp.py` **Line 72**

```python
pbar = tqdm(total=total_chunk_count, desc="vectot indexing", position=0)
```

`tqdm` writes ANSI escape codes to stderr on every `update()` call. In a production server behind Nginx, there is no interactive terminal — this wastes CPU cycles on I/O buffering and log pollution.

**Fix:** Replace with a counter or structured logging:
```python
logger.info(f"Starting vector indexing: {total_chunk_count} chunks")
# ... in loop:
logger.info(f"Indexed {inserted_items_count}/{total_chunk_count} chunks")
```

---

### PERF-05 `[P2]` `PGVectorProvider.search_by_vector` Checks Collection Existence on Every Search
**File:** `src/stores/vectordb/providers/PGVectorProvider.py` **Lines 285–286**

Every similarity search first runs `SELECT * FROM pg_tables WHERE tablename = :name` before running the actual search query. This doubles the number of DB round-trips.

**Fix:** Remove the pre-check; let PostgreSQL raise a `ProgrammingError` if the table doesn't exist, and catch it:
```python
async def search_by_vector(self, collection_name, vector, limit):
    self._validate_identifier(collection_name)
    vector_str = "[" + ",".join(str(v) for v in vector) + "]"
    async with self.db_client() as session:
        async with session.begin():
            try:
                search_sql = sql_text(...)
                result = await session.execute(search_sql, {"vector": vector_str, "limit": limit})
                ...
            except ProgrammingError:
                self.logger.error(f"Collection does not exist: {collection_name}")
                return []
```

---

## 5. Additional Issues

### MISC-01 `[P2]` Deprecated `pymongo` Import in ChunkModel
**File:** `src/models/ChunkModel.py` **Line 4**

```python
from pymongo import InsertOne
```

This imports from the MongoDB driver, which is no longer used (project migrated to PostgreSQL). It's dead code but still requires `pymongo` to be installed.

---

### MISC-02 `[P2]` Dead MongoDB Dependencies in `requirements.txt`
**File:** `src/requirements.txt` **Lines 9–10**

```
motor==3.4.0
pydantic.mongo==2.3.0
```

These packages are for MongoDB, which was replaced by PostgreSQL. They add unnecessary image size and potential supply-chain risk.

---

### MISC-03 `[P2]` Duplicate `streamlit` Entry in `requirements.txt`
**File:** `src/requirements.txt` **Lines 20, 31**

```
streamlit==1.45.1
...
streamlit
```

The unpinned duplicate on line 31 could cause version conflicts.

---

### MISC-04 `[P2]` English Template Missing `$dialect_instruction` Variable
**File:** `src/stores/llm/templates/locales/en/rag.py` **Line 7**

The English `system_prompt` template does not include the `$dialect_instruction` variable that is present in the Arabic template. When `PRIMARY_LANG=en`, calling `template_parser.get("rag", "system_prompt", {"dialect_instruction": ...})` will raise a `ValueError` because the template doesn't have that placeholder.

**Fix:** Add `$dialect_instruction` to the English template (even if typically empty):
```python
system_prompt = Template("\n".join([
    ...
    "$dialect_instruction",
    "Be polite and respectful to the user.",
    ...
]))
```

---

### MISC-05 `[P2]` `PgVectorDistnaceMethodEnum.DOT` Has Wrong Value
**File:** `src/stores/vectordb/VectorDBEnums.py` **Line 22**

```python
class PgVectorDistnaceMethodEnum(Enum):
    DOT = "vector_12_ops"  # ← Should be "vector_ip_ops"
```

If anyone selects dot-product distance, the index creation will fail with an invalid operator class.

---

### MISC-06 `[P3]` `__intit__.py` Typo
**File:** `src/utils/__intit__.py`

The filename is misspelled (`__intit__` instead of `__init__`). This means `src/utils/` is not a proper Python package — imports that depend on it work only because `metrics.py` is imported via an explicit dotted path.

---

## 6. Remediation Priority Matrix

| Priority | ID | Category | Effort | Impact |
|----------|----|----------|--------|--------|
| **P0** | SEC-01 | Security | 30 min | Credential leak — rotate immediately |
| **P0** | BUG-04 | Bug | 10 min | Temperature=200 produces garbage output |
| **P0** | BUG-01 | Bug | 5 min | CoHere generation always returns None |
| **P0** | BUG-02 | Bug | 15 min | Cross-request state pollution |
| **P0** | BUG-03 | Bug | 10 min | Caller's chat history corrupted |
| **P0** | SEC-02 | Security | 2 hr | SQL injection in vector DB layer |
| **P0** | PERF-01 | Perf | 3 hr | Event loop blocked by sync API calls |
| **P1** | SEC-03 | Security | 5 min | Webhook timing attack |
| **P1** | SEC-04 | Security | 15 min | Path traversal |
| **P1** | SEC-05 | Security | 30 min | Exposed internal service ports |
| **P1** | BUG-05 | Bug | 10 min | Qdrant missing awaits |
| **P1** | BUG-06 | Bug | 1 hr | Overlap ignored, metadata lost |
| **P1** | BUG-07 | Bug | 5 min | Settings re-parsed per call |
| **P1** | PERF-02 | Perf | 20 min | HTTP client recreated per message |
| **P1** | PERF-03 | Perf | 15 min | Sync file I/O blocks event loop |
| **P2** | PERF-04 | Perf | 5 min | tqdm in production |
| **P2** | PERF-05 | Perf | 15 min | Double DB round-trip per search |
| **P2** | MISC-01–06 | Cleanup | 30 min | Dead code, typos, wrong enum value |

---

## 7. Nginx & TLS Configuration Notes

**Current Config (`docker/nginx/default.conf`):**
- Missing security headers: `X-Frame-Options`, `X-Content-Type-Options`, `Strict-Transport-Security`, `Content-Security-Policy`.
- No rate limiting on webhook or API endpoints.
- No request body size limit (`client_max_body_size`).
- No `proxy_read_timeout` — long RAG requests may timeout at Nginx's default 60s.

**Recommended additions:**
```nginx
server {
    listen 443 ssl;
    ...

    # Security headers
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;

    # Body size limit (match FILE_MAX_SIZE=10MB)
    client_max_body_size 12M;

    # Timeout for long RAG requests
    proxy_read_timeout 120s;
    proxy_connect_timeout 10s;

    # Rate limiting (define in http block)
    limit_req zone=api burst=20 nodelay;
}
```

---

*End of Review*
