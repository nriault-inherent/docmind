# NotebookLM Local RAG — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local RAG application (NotebookLM-style) with Ollama, ChromaDB, and Streamlit — fully offline, no cloud APIs.

**Architecture:** Three-module Python app: `ingest.py` handles document extraction/chunking/indexing into ChromaDB, `rag.py` performs semantic retrieval and streams responses from Ollama, and `app.py` provides a Streamlit UI with authentication, file upload, and chat interface.

**Tech Stack:** Ollama (qwen3:8b + nomic-embed-text), ChromaDB, llama-index, unstructured, Streamlit, streamlit-authenticator, Python 3.11+.

---

### Task 1: Project scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `README.md`
- Create: `data/chroma_db/` directory
- Modify: `pyproject.toml` (add dependencies)

- [ ] **Step 1: Create requirements.txt**

Create `requirements.txt` with these exact contents:

```
streamlit>=1.38.0
streamlit-authenticator>=0.4.1
llama-index>=0.12.0
llama-index-embeddings-ollama>=0.3.0
llama-index-llms-ollama>=0.4.0
llama-index-vector-stores-chroma>=0.3.0
chromadb>=0.5.0
unstructured[all-docs]>=0.16.0
pyyaml>=6.0
python-dotenv>=1.0.0
```

- [ ] **Step 2: Create README.md**

Create `README.md` with installation instructions covering: Ollama setup, model download (`qwen3:8b`, `nomic-embed-text`), Python venv + pip install, bcrypt hash generation for config.yaml, streamlit launch command, and Cloudflare Tunnel setup (install cloudflared, login, tunnel command, optional persistent tunnel).

- [ ] **Step 3: Create data/chroma_db/ directory**

```bash
mkdir -p data/chroma_db
```

- [ ] **Step 4: Verify .gitignore includes data/chroma_db/**

Ensure `.gitignore` contains `data/chroma_db/` and `data/uploads/`.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt README.md data/ .gitignore
git commit -m "chore: add requirements, README, and chroma_db placeholder"
```

---

### Task 2: Document ingestion pipeline

**Files:**
- Create: `ingest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ingest.py`:

```python
from types import SimpleNamespace

import pytest

import ingest


def test_safe_source_name_removes_path_and_validates_extension():
    assert ingest._safe_source_name("../../notes/Rapport.PDF") == "Rapport.PDF"
    with pytest.raises(ingest.IngestionError, match="Format non pris en charge"):
        ingest._safe_source_name("archive.zip")


def test_extract_pages_rejects_empty_file():
    with pytest.raises(ingest.IngestionError, match="est vide"):
        ingest._extract_pages(b"", "vide.txt")


def test_ingest_embeds_before_replacing_existing_document(monkeypatch):
    events = []
    nodes = [SimpleNamespace(get_content=lambda: "contenu", embedding=None)]

    class FakeEmbedModel:
        def get_text_embedding_batch(self, texts, show_progress):
            events.append(("embed", texts, show_progress))
            return [[0.1, 0.2]]

    class FakeCollection:
        def delete(self, where):
            assert nodes[0].embedding == [0.1, 0.2]
            events.append(("delete", where))

    class FakeVectorStore:
        def __init__(self, chroma_collection):
            self.collection = chroma_collection

        def add(self, added_nodes):
            events.append(("add", added_nodes))

    monkeypatch.setattr(ingest, "_build_nodes", lambda *args: nodes)
    monkeypatch.setattr(ingest, "_embed_model", FakeEmbedModel)
    monkeypatch.setattr(ingest, "_collection", FakeCollection)
    monkeypatch.setattr(ingest, "ChromaVectorStore", FakeVectorStore)

    assert ingest.ingest_document(b"document", "notes.txt") == 1
    assert [event[0] for event in events] == ["embed", "delete", "add"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingest.py -v`
Expected: FAIL with "module not found" or "attribute not found"

- [ ] **Step 3: Write ingest.py implementation**

Create `ingest.py` with the complete implementation (see file in project root — 180 lines covering `_safe_source_name`, `_extract_pages`, `_build_nodes`, `ingest_document`, `list_documents`, `delete_document`, `IngestionError`, `IndexedDocument`).

Key behaviors:
- `_safe_source_name`: strips path, validates extension against `.pdf .docx .txt .md .markdown .html .htm`
- `_extract_pages`: uses `unstructured.partition.auto.partition`, groups by page number
- `_build_nodes`: creates llama-index Documents, splits with `SentenceSplitter(chunk_size=512, chunk_overlap=50)`
- `ingest_document`: replaces existing source (delete by `where={"source": source}`), then adds new nodes
- `list_documents`: groups chunks by source, counts pages
- `delete_document`: deletes all chunks for a source filename

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ingest.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add ingest.py tests/test_ingest.py
git commit -m "feat: add document ingestion pipeline with unstructured extraction"
```

---

### Task 3: RAG retrieval and generation

**Files:**
- Create: `rag.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_rag.py`:

```python
from types import SimpleNamespace

import pytest

import rag


class FakeCollection:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


def test_empty_collection_returns_guardrail_without_loading_models(monkeypatch):
    monkeypatch.setattr(rag, "_collection", lambda: FakeCollection(0))
    monkeypatch.setattr(
        rag,
        "_models",
        lambda: pytest.fail("Les modèles ne doivent pas être chargés sans document"),
    )

    response = rag.answer_question("Une question ?", [])

    assert "Je ne sais pas" in "".join(response.chunks)
    assert response.sources == []


def test_irrelevant_chunks_do_not_call_llm(monkeypatch):
    node = SimpleNamespace(
        metadata={"source": "guide.pdf", "page": 2},
        get_content=lambda: "Texte sans rapport",
    )
    retrieved = [SimpleNamespace(node=node, score=0.1)]

    class FakeIndex:
        @classmethod
        def from_vector_store(cls, vector_store, embed_model):
            return cls()

        def as_retriever(self, similarity_top_k):
            return SimpleNamespace(retrieve=lambda question: retrieved)

    fake_llm = SimpleNamespace(
        stream_complete=lambda prompt: pytest.fail("Le LLM ne doit pas répondre sans contexte pertinent")
    )
    monkeypatch.setattr(rag, "_collection", lambda: FakeCollection(1))
    monkeypatch.setattr(rag, "_models", lambda: (object(), fake_llm))
    monkeypatch.setattr(rag, "ChromaVectorStore", lambda chroma_collection: object())
    monkeypatch.setattr(rag, "VectorStoreIndex", FakeIndex)

    response = rag.answer_question("Une question ?", [], similarity_cutoff=0.2)

    assert "Je ne sais pas" in "".join(response.chunks)
    assert response.sources == []


def test_relevant_chunk_is_streamed_and_exposed_as_source(monkeypatch):
    node = SimpleNamespace(
        metadata={"source": "guide.pdf", "page": 3},
        get_content=lambda: "La réponse est locale.",
    )
    retrieved = [SimpleNamespace(node=node, score=0.8)]

    class FakeIndex:
        @classmethod
        def from_vector_store(cls, vector_store, embed_model):
            return cls()

        def as_retriever(self, similarity_top_k):
            assert similarity_top_k == 5
            return SimpleNamespace(retrieve=lambda question: retrieved)

    fake_llm = SimpleNamespace(
        stream_complete=lambda prompt: iter(
            [SimpleNamespace(delta="Réponse "), SimpleNamespace(delta="locale.")]
        )
    )
    monkeypatch.setattr(rag, "_collection", lambda: FakeCollection(1))
    monkeypatch.setattr(rag, "_models", lambda: (object(), fake_llm))
    monkeypatch.setattr(rag, "ChromaVectorStore", lambda chroma_collection: object())
    monkeypatch.setattr(rag, "VectorStoreIndex", FakeIndex)

    response = rag.answer_question("Où est la réponse ?", [])

    assert "".join(response.chunks) == "Réponse locale."
    assert response.sources[0].document == "guide.pdf"
    assert response.sources[0].page == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_rag.py -v`
Expected: FAIL with "module not found"

- [ ] **Step 3: Write rag.py implementation**

Create `rag.py` with complete implementation (see file in project root — 158 lines covering `RagError`, `Source`, `RagResponse`, `_models`, `_history_text`, `_sources`, `_context`, `_stream_completion`, `answer_question`).

Key behaviors:
- `answer_question`: early-return "Je ne sais pas" if collection is empty or no relevant chunks
- `similarity_cutoff`: filters retrieved nodes by score threshold (default 0.2)
- `_stream_completion`: yields deltas from `llm.stream_complete()`
- System prompt instructs model to only use provided context and cite sources as `[nom du fichier, p. X]`
- History passed as last 6 messages in `role : content` format

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_rag.py -v`
Expected: 3 passed

- [ ] **Step 4: Commit**

```bash
git add rag.py tests/test_rag.py
git commit -m "feat: add RAG retrieval and streaming generation with Ollama"
```

---

### Task 4: Streamlit UI with authentication

**Files:**
- Create: `app.py`
- Create: `config.yaml`

- [ ] **Step 1: Create config.yaml**

Create `config.yaml`:

```yaml
credentials:
  usernames:
    admin:
      email: admin@localhost
      name: Administrateur
      password: REPLACE_WITH_BCRYPT_HASH
cookie:
  expiry_days: 7
  key: 885f163383dd1da81eb54f539667e2fc43c36931a98bcc4e7c62693036989477
  name: docmind_auth
```

- [ ] **Step 2: Write app.py implementation**

Create `app.py` with complete implementation (see file in project root — 177 lines).

Key components:
- `authenticate()`: loads config, checks for placeholder password, shows login form, renders logout in sidebar
- `documents_tab()`: multi-file uploader (PDF/DOCX/TXT/MD/HTML), progress bar, document list with delete buttons
- `chat_tab()`: session-state message history, sidebar with k slider (1-15) and cutoff slider (0.0-1.0), clear conversation button, streaming response display, source expander
- `render_sources()`: shows document name, page, relevance score, and excerpt in collapsible section

- [ ] **Step 3: Run to verify no import errors**

Run: `python -c "import app"` (from project root)
Expected: no output (no errors)

- [ ] **Step 4: Commit**

```bash
git add app.py config.yaml
git commit -m "feat: add Streamlit UI with auth, upload, and chat interface"
```

---

### Task 5: Final verification

**Files:**
- All existing files

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: all tests pass

- [ ] **Step 2: Verify requirements install cleanly**

Run: `pip install -r requirements.txt --dry-run`
Expected: no conflicts

- [ ] **Step 3: Verify .gitignore is complete**

Check `.gitignore` includes: `.venv/`, `__pycache__/`, `data/chroma_db/`, `data/uploads/`, `.env`, `*.py[cod]`, `.pytest_cache/`

- [ ] **Step 4: Final commit if any fixes were made**

```bash
git add -A
git commit -m "chore: final verification and cleanup"
```
