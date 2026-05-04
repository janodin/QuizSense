# Long-term Optimizations Report

## 1. Pre-compute Textbook Embeddings

### Files Created/Modified
- `quiz/management/commands/precompute_textbook_embeddings.py` (NEW)
- `quiz/models.py` (MODIFIED - added `embedding_version` and `source_hash` fields to `TextbookChunk`)
- `quiz/migrations/0017_add_embedding_version_and_source_hash.py` (NEW)
- `quiz/services/rag_service.py` (MODIFIED - uses pre-computed embeddings)

### Usage
```bash
# Pre-compute all textbook embeddings
python manage.py precompute_textbook_embeddings

# Reset and re-compute all embeddings
python manage.py precompute_textbook_embeddings --reset

# Limit to first 5 textbooks (for testing)
python manage.py precompute_textbook_embeddings --limit 5

# Dry run (show what would be processed)
python manage.py precompute_textbook_embeddings --dry-run

# Custom batch size for embedding generation
python manage.py precompute_textbook_embeddings --batch-size 64
```

### How It Works
1. Scans `dataset/` directory for PDF/DOCX files
2. Computes MD5 hash of each file for change detection
3. Checks if embeddings already exist with matching `source_title`, `embedding_version`, and `source_hash`
4. Skips files that are already embedded and up-to-date
5. Extracts text, chunks using existing `chunking_service.py`, generates embeddings via `embedding_service.py`
6. Saves `TextbookChunk` records with version number and source hash

### Versioning Strategy
- `EMBEDDING_VERSION` constant in the management command (currently `1`)
- Increment when chunking strategy or embedding model changes
- Old embeddings are automatically replaced when re-running with a new version

---

## 2. ONNX Runtime for Embeddings

### Files Created/Modified
- `quiz/services/embedding_service.py` (MODIFIED - added `ONNXEmbeddingProvider` class)
- `quizsense/settings.py` (MODIFIED - added `USE_ONNX_EMBEDDINGS` setting)
- `quiz/management/commands/benchmark_embeddings.py` (NEW)

### Installation
```bash
pip install onnxruntime transformers
```

### Configuration
Add to `.env`:
```env
USE_ONNX_EMBEDDINGS=true
```

### How It Works
1. `ONNXEmbeddingProvider` class exports `all-MiniLM-L6-v2` to ONNX format on first use
2. ONNX model is cached in `.onnx_models/all-MiniLM-L6-v2/`
3. Inference runs via `onnxruntime` with mean pooling and L2 normalization
4. Falls back to PyTorch if ONNX is unavailable or fails

### Benchmarking
```bash
python manage.py benchmark_embeddings
python manage.py benchmark_embeddings --samples 100 --batch-size 32
```

Results are saved to `benchmark_results.json`.

### Expected Performance
ONNX Runtime typically provides **2-4x speedup** on CPU for the all-MiniLM-L6-v2 model compared to PyTorch, due to:
- Graph optimizations
- Reduced memory overhead
- Optimized CPU kernels

---

## 3. pgvector for RAG Search

### Files Created/Modified
- `quiz/services/rag_service.py` (MODIFIED - added pgvector support)
- `quizsense/settings.py` (MODIFIED - added `USE_PGVECTOR` setting)

### Installation Steps for pgvector

#### Ubuntu/Debian
```bash
# Install pgvector
sudo apt install postgresql-16-pgvector  # Adjust version number

# Enable the extension in your database
psql -d quizsense -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

#### macOS (Homebrew)
```bash
brew install pgvector
# Then add to postgresql.conf:
# shared_preload_libraries = 'vector'
# Restart PostgreSQL, then:
psql -d quizsense -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

#### Windows
pgvector is not officially supported on Windows PostgreSQL. Options:
1. Use WSL2 with Linux PostgreSQL
2. Use Docker: `docker run -e POSTGRES_PASSWORD=postgres -p 5432:5432 pgvector/pgvector:pg16`
3. Continue using in-memory numpy fallback (works fine for moderate datasets)

#### Docker
```bash
docker run -d \
  --name pgvector \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=quizsense \
  -p 5432:5432 \
  pgvector/pgvector:pg16
```

### Configuration
Add to `.env`:
```env
USE_PGVECTOR=true
```

### How It Works
1. `_is_pgvector_available()` checks if PostgreSQL has the `vector` type
2. If available, uses `CREATE EXTENSION IF NOT EXISTS vector;`
3. `_pgvector_cosine_similarity()` uses the `<=>` operator for cosine distance
4. Falls back to in-memory numpy scoring if pgvector is unavailable

### Migration Plan for pgvector Column
The existing `VectorEmbedding` field stores embeddings as JSON text. To use pgvector's native vector type:

```sql
-- Step 1: Install pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Step 2: Add native vector column
ALTER TABLE quiz_textbookchunk ADD COLUMN embedding_vec vector(384);
ALTER TABLE quiz_uploadedchunk ADD COLUMN embedding_vec vector(384);

-- Step 3: Migrate existing JSON embeddings to vector type
-- (Requires a Django data migration or SQL script)

-- Step 4: Create HNSW index for fast similarity search
CREATE INDEX ON quiz_textbookchunk USING hnsw (embedding_vec vector_cosine_ops);
CREATE INDEX ON quiz_uploadedchunk USING hnsw (embedding_vec vector_cosine_ops);

-- Step 5: Update Django queries to use embedding_vec instead of embedding
```

**Note**: The current implementation works with the JSON-based `VectorEmbedding` field and uses pgvector's ability to cast JSON arrays to vector type via `::vector`. This avoids the need for a schema migration while still benefiting from pgvector's optimized similarity search.

### pgvector Query Example
```python
# Using pgvector's <=> cosine distance operator
# similarity = 1 - (embedding <=> query_vector)
TextbookChunk.objects.annotate(
    cosine_similarity=RawSQL(
        "1 - (embedding <=> %s::vector)",
        [vector_str],
    )
).order_by('-cosine_similarity')[:top_k]
```

---

## Settings Summary

| Setting | Default | Description |
|---------|---------|-------------|
| `USE_ONNX_EMBEDDINGS` | `False` | Enable ONNX Runtime for faster CPU embeddings |
| `USE_PGVECTOR` | `False` | Enable pgvector for PostgreSQL similarity search |
| `EMBEDDING_DIMENSIONS` | `1536` | Embedding dimension (note: all-MiniLM-L6-v2 uses 384) |

---

## Fallback Mechanisms

1. **ONNX → PyTorch**: If `onnxruntime` is not installed or model export fails, embeddings fall back to PyTorch/sentence-transformers automatically.

2. **pgvector → In-memory numpy**: If PostgreSQL doesn't have pgvector or the database is SQLite, similarity search falls back to numpy-based cosine similarity scoring.

3. **Pre-computed → On-the-fly**: If no pre-computed embeddings exist, the RAG service still works by computing embeddings on demand (original behavior).

---

## Benchmark Results

Run `python manage.py benchmark_embeddings` to generate benchmark results for your specific hardware. Results are saved to `benchmark_results.json`.

Expected results on a typical CPU (Hetzner CX22, 2 vCPU):
- PyTorch: ~5-10ms per sample (batch size 16)
- ONNX: ~2-4ms per sample (batch size 16)
- Speedup: ~2-3x

---

## Files Modified Summary

| File | Change |
|------|--------|
| `quiz/models.py` | Added `embedding_version`, `source_hash` fields and index to `TextbookChunk` |
| `quiz/services/embedding_service.py` | Added `ONNXEmbeddingProvider` class, updated `embed_texts_batched` to use ONNX when enabled |
| `quiz/services/rag_service.py` | Added pgvector support with `_is_pgvector_available()` and `_pgvector_cosine_similarity()` |
| `quizsense/settings.py` | Added `USE_ONNX_EMBEDDINGS` and `USE_PGVECTOR` settings |
| `quiz/management/commands/precompute_textbook_embeddings.py` | NEW - Pre-compute embeddings management command |
| `quiz/management/commands/benchmark_embeddings.py` | NEW - Benchmark PyTorch vs ONNX performance |
| `quiz/migrations/0017_add_embedding_version_and_source_hash.py` | NEW - Migration for new fields |
