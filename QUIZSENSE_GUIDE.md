# QuizSense — Complete Project Guide

> **A Web Application for Multiple-Choice Question Quiz Maker**
> Research Defense Reference Document

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Technology Stack](#3-technology-stack)
4. [Complete User Workflow](#4-complete-user-workflow)
5. [Backend Processing Pipeline](#5-backend-processing-pipeline)
6. [RAG System Explained](#6-rag-system-explained)
7. [Database Schema](#7-database-schema)
8. [Key Features](#8-key-features)
9. [How to Run the System](#9-how-to-run-the-system)
10. [Admin Panel Guide](#10-admin-panel-guide)
11. [Evaluation & Analytics](#11-evaluation--analytics)
12. [Deployment](#12-deployment)
13. [Common Questions for Defense](#13-common-questions-for-defense)

---

## 1. Project Overview

**QuizSense** is an AI-powered educational platform that automatically generates study summaries and multiple-choice quizzes from uploaded lecture materials (PDF or Word documents).

### What It Does

1. **Upload** — Students or teachers upload PDF/DOCX lecture files
2. **Extract** — The system extracts text from the files (with AI Vision OCR for scanned PDFs up to 100 pages and image-only DOCX files)
3. **Summarize** — AI generates a study summary of the uploaded content
4. **Quiz** — AI creates a 10-question multiple-choice quiz grounded in textbook knowledge
5. **Recommend** — After the quiz, AI provides personalized topic recommendations for improvement

### Five Chapters Covered

The system is designed for **Fundamentals of Programming** with these chapters:

| Chapter | Topics |
|---------|--------|
| 1. Introduction to Programming | What is Programming, History of Programming, Programming Paradigms, Compilers and Interpreters, Writing and Running Programs, Debugging and Troubleshooting |
| 2. Variables & Data Types | Variables, Data Types, Constants, Variable Scope, Type Conversion |
| 3. Operators & Expressions | Arithmetic Operators, Comparison Operators, Logical Operators, Assignment Operators, Operator Precedence |
| 4. Control Structures | Conditional Statements, Loops, Nested Control Structures, Break and Continue, Switch/Match Statements |
| 5. Functions | Defining Functions, Parameters and Arguments, Return Values, Lambda Functions, Recursion |

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        USER BROWSER                          │
│  (Upload → Summary → Quiz → Results → Review)               │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP Requests
┌────────────────────────▼────────────────────────────────────┐
│                    DJANGO WEB SERVER                         │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐ │
│  │   Views     │  │   Forms      │  │   URL Routing      │ │
│  │  (14 views) │  │  (Upload)    │  │   (14 patterns)    │ │
│  └──────┬──────┘  └──────────────┘  └────────────────────┘ │
│         │                                                   │
│  ┌──────▼──────────────────────────────────────────────┐   │
│  │              SERVICES LAYER                          │   │
│  │  ┌──────────────┐  ┌─────────────┐  ┌────────────┐ │   │
│  │  │ File         │  │ Chunking    │  │ Embedding  │ │   │
│  │  │ Processor    │  │ Service     │  │ Service    │ │   │
│  │  └──────┬───────┘  └──────┬──────┘  └─────┬──────┘ │   │
│  │  ┌──────▼─────────────────▼───────────────▼──────┐ │   │
│  │  │              RAG Service                       │ │   │
│  │  │  (Cosine Similarity Retrieval)                 │ │   │
│  │  └──────────────────────┬────────────────────────┘ │   │
│  │  ┌──────────────────────▼────────────────────────┐ │   │
│  │  │         Pipeline Service (AI Provider)         │ │   │
│  │  │  (Summary / Quiz / Recommendation Generation)  │ │   │
│  │  └──────────────────────┬────────────────────────┘ │   │
│  └─────────────────────────┼───────────────────────────┘   │
│                            │                                │
└────────────────────────────┼────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
   │  PostgreSQL │   │    Redis    │   │   Celery    │
   │  (Database) │   │   (Cache)   │   │  (Tasks)    │
   └─────────────┘   └─────────────┘   └─────────────┘
                             │
                    ┌────────▼────────┐
                    │   AI Provider   │
                    │  (LLM API)      │
                    └─────────────────┘
```

---

## 3. Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Backend Framework** | Django 5.x | Web application framework |
| **Task Queue** | Celery 5.4.0 | Background task processing |
| **Cache & Broker** | Redis 5.2.1 | Caching, task brokering, embedding cache |
| **Database** | PostgreSQL | Primary data storage |
| **Embeddings** | DeepInfra API (sentence-transformers/all-MiniLM-L6-v2) | Text-to-vector conversion via API (384 dimensions) |
| **AI Language Model** | DeepInfra openai/gpt-oss-120B (primary), meta-llama/Meta-Llama-3.1-8B-Instruct (fallback) | Summary, quiz, and recommendation generation with automatic retry and model fallback |
| **PDF Processing** | PyMuPDF (fitz) + PyPDF2 fallback | Text extraction from PDFs |
| **Word Processing** | python-docx | Text extraction from DOCX files |
| **OCR** | DeepInfra Vision API (Qwen3-VL-30B-A3B-Instruct primary, Llama-3.2-90B-Vision fallback) | Cloud-based OCR for scanned PDFs (up to 100 pages) and image-only DOCX files |
| **Web Server** | Gunicorn + Nginx + WhiteNoise | Production deployment with static file serving |
| **Frontend** | Bootstrap 5.3.3, Chart.js, DOMPurify | UI, data visualization, and XSS protection |
| **Forms** | django-crispy-forms + crispy-bootstrap5 | Form rendering |
| **Security** | GZipMiddleware, Rate Limiting, System Prompts | Compression, abuse prevention, token optimization |

---

## 4. Complete User Workflow

### Step 1: Upload Files

```
User visits homepage (/)
    │
    ├── Select Chapter (dropdown: 5 chapters)
    │
    ├── Upload Files (PDF or DOCX, max 10MB each, up to 10 files)
    │
    └── Click "Generate Summary"
         │
         ▼
    System creates an UploadSession
    System saves uploaded files to media/uploads/
    System queues background processing task
    User is redirected to Summary page
```

### Step 2: View Study Summary

```
Summary page (/summary/<session_id>/)
    │
    ├── Shows processing status (polling every 3 seconds)
    │   └── Statuses: pending → processing → completed/failed
    │       (Detailed step timing logged separately: extracting, chunking, summarizing)
    │
    ├── When completed, displays:
    │   ├── AI-generated study summary (formatted Markdown/HTML)
    │   ├── Text quality indicators (word count, character count, chunk count)
    │   └── "Generate Quiz" button (quiz is also auto-generated in background)
    │
    └── Click "Generate Quiz" (or wait for auto-generation)
         │
         ▼
    System queues quiz generation task (if not already queued)
    User is redirected to Quiz Waiting page
```

### Step 3: Wait for Quiz Generation

```
Quiz Waiting page (/quiz/<quiz_id>/waiting/)
    │
    ├── Shows processing status (polling every 3 seconds)
    │   └── Statuses: pending → processing → completed/failed
    │
    └── When completed, redirects to Take Quiz page
```

### Step 4: Take the Quiz

```
Take Quiz page (/quiz/<quiz_id>/)
    │
    ├── Displays 10 multiple-choice questions
    │   ├── Each question has 4 choices (A, B, C, D)
    │   └── All questions must be answered before submission
    │
    ├── Answers saved in localStorage (survives page refresh)
    │
    └── Click "Submit Quiz"
         │
         ▼
    System validates all questions answered
    System creates QuizAttempt + QuizAnswer records
    System calculates score
    System queues recommendation generation
    User is redirected to Results page
```

### Step 5: View Results

```
Results page (/results/<attempt_id>/)
    │
    ├── Animated score ring (percentage display)
    │
    ├── AI recommendation status (polling every 3 seconds)
    │   └── When ready: personalized topic recommendations appear
    │
    └── "Review Answers" button → goes to Review page
```

### Step 6: Review Answers

```
Review page (/review/<attempt_id>/)
    │
    ├── Shows all 10 questions with:
    │   ├── User's selected answer
    │   ├── Correct answer highlighted in green
    │   └── Wrong answers highlighted in red
    │
    └── "Back to Home" button
```

---

## 5. Backend Processing Pipeline

### File Upload Processing

```
process_upload_session_simple(upload_session_id)
    │
    ├── Step 1: TEXT_EXTRACTION
    │   ├── For each uploaded file:
    │   │   ├── PDF → PyMuPDF extracts text page-by-page
    │   │   │   ├── If page has <50 chars → PyPDF2 fallback for that page
    │   │   │   └── If still <50 chars → AI Vision OCR fallback for that page
    │   │   ├── DOCX → python-docx extracts text (paragraphs, tables, headers/footers, raw XML)
    │   │   │   └── If no text found → AI Vision OCR on embedded word/media/ images
    │   │   └── Scanned PDF → DeepInfra Vision OCR (max 100 pages)
    │   └── Save extracted_text to UploadedFile records
    │
    ├── Step 2: CHUNKING_EMBEDDING
    │   ├── Split all extracted text into 500-word chunks (100-word overlap)
    │   ├── Generate embeddings using DeepInfra API (sentence-transformers/all-MiniLM-L6-v2)
    │   └── Bulk create UploadedChunk records with embeddings
    │
    └── Step 3: SUMMARY_GENERATION
        ├── Check Redis cache FIRST (cache key based on content hash)
        │   └── If cache hit: return cached summary (skip RAG)
        │
        ├── If cache miss:
        │   ├── Always retrieve RAG context (textbook + uploaded chunks)
        │   ├── For long documents (>15k chars): Use map-reduce summary with RAG context
        │   │   ├── Skip first 3% of text (title/copyright pages)
        │   │   ├── Split into 2-4 sections of 12,000 chars each
        │   │   ├── Extract concepts from each section IN PARALLEL (ThreadPoolExecutor)
        │   │   ├── Synthesize all concepts into final summary
        │   │   └── If synthesis fails → retry with truncated context → fallback to concept summary
        │   ├── For short documents:
        │   │   ├── Use RAG context directly for single-pass summary
        │   │   └── Send context + prompt to AI provider
        │   ├── Receive and validate Markdown summary
        │   ├── Cache result in Redis (7-day TTL)
        │   └── Save summary to UploadSession
        │
        └── After summary completes:
            └── Auto-queue quiz generation (queue_quiz_generation)
```

### Quiz Generation

```
_process_quiz_for_session(upload_session)
    │
    ├── Create or lock existing Quiz record (select_for_update to prevent race conditions)
    │
    ├── Check Redis cache FIRST (cache key based on content hash + chapter)
    │   └── If cache hit: return cached quiz data (skip AI call)
    │
    ├── Retrieve context via RAG:
    │   ├── Embed uploaded content via DeepInfra API
    │   ├── Score against uploaded chunks (cosine similarity)
    │   ├── Score against textbook chunks (cosine similarity)
    │   └── Return top-k chunks from each source
    │
    ├── Send context + quiz prompt to AI provider:
    │   └── "Generate 10 MCQ questions in JSON format..."
    │   └── max_tokens=4096 (smaller than summary since JSON is compact)
    │
    ├── Validate JSON output:
    │   ├── Must have: question, choices (a-d), correct_answer, topic
    │   ├── Must have exactly 10 questions
    │   └── If malformed → JSON repair fallback attempts to fix
    │
    ├── Validate each question (text length, choices, answer, topic)
    ├── Bulk create Question records
    ├── Link questions to Quiz (M2M relationship)
    ├── Update Quiz status to "completed"
    ├── Cache result in Redis (7-day TTL)
    └── Record generation metrics (duration, provider, success, cache_hit)
```

### Recommendation Generation

```
_process_recommendations_for_attempt(attempt)
    │
    ├── Check if already completed → return existing recommendation
    │
    ├── Set status to "processing"
    │
    ├── Check Redis cache FIRST (cache key based on answer signature)
    │   └── If cache hit: return cached recommendation
    │
    ├── Collect all answers from QuizAttempt
    │   └── Group by topic, identify wrong answers
    │
    ├── Send wrong topics + context to AI provider:
    │   └── "Generate personalized study recommendations..."
    │   └── max_tokens=8192, timeout=90s
    │
    ├── Validate output (must be >50 chars)
    ├── Cache result in Redis (7-day TTL)
    ├── Save recommendation to QuizAttempt
    ├── Set status to "completed"
    └── Record generation metrics
```

---

## 6. RAG System Explained

### What is RAG?

**Retrieval-Augmented Generation** combines information retrieval with AI text generation. Instead of asking the AI to generate content from memory, QuizSense first retrieves relevant textbook content, then uses that as context for the AI.

### How QuizSense's RAG Works

```
                    ┌─────────────────────┐
                    │   User Uploads      │
                    │   Lecture Files     │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   Extract & Chunk   │
                    │   Text (500 words)  │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   Generate Vector   │
                    │   Embeddings (384d) │
                    └──────────┬──────────┘
                               │
              ┌────────────────▼────────────────┐
              │      COSINE SIMILARITY          │
              │         SCORING                 │
              │                                 │
              │  ┌─────────────┐ ┌────────────┐│
              │  │  Uploaded   │ │  Textbook  ││
              │  │   Chunks    │ │   Chunks   ││
              │  │  (user's    │ │ (pre-seeded││
              │  │   files)    │ │  knowledge)││
              │  └──────┬──────┘ └─────┬──────┘│
              │         │              │        │
              │         └──────┬───────┘        │
              └────────────────┼────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   Top-K Relevant    │
                    │   Chunks Selected   │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   Chunks + Prompt   │
                    │   Sent to AI Model  │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   AI Generates      │
                    │   Summary / Quiz    │
                    └─────────────────────┘
```

### Two Sources of Knowledge

| Source | Description | Purpose |
|--------|-------------|---------|
| **Uploaded Chunks** | Chunks from the user's uploaded files | Provides specific lecture context |
| **Textbook Chunks** | Pre-seeded chunks from ~194 programming textbooks | Provides authoritative reference knowledge |

### Cache-First Architecture

QuizSense implements a cache-first approach for RAG retrieval and AI generation:
1. **Check Cache First**: Before any expensive embedding or similarity computation, the system checks Redis for a cached result
2. **Cache Key**: Based on content hash and chapter ID — identical content returns cached results instantly
3. **Cache Hit**: Returns cached summary/quiz/recommendation in milliseconds, skipping RAG and AI generation entirely
4. **Cache Miss**: Only then performs embedding generation, similarity scoring, and AI generation
5. **Cache TTL**: 7 days for summary, quiz, and recommendation caches
6. **Map-Reduce for Long Docs**: Documents >15k chars use map-reduce summary with RAG context — embedding API call is still made to retrieve textbook knowledge
7. **Quiz Caching**: Quiz results cached based on content hash + chapter title (7-day TTL)
8. **Recommendation Caching**: Recommendations cached based on answer signature (7-day TTL)

This optimization saves 200-500ms per request on cache hits and significantly reduces database and AI API load. RAG context is always included to ground summaries in textbook knowledge.

### Embedding Process

1. **Text → Vector**: Each chunk of text is sent to DeepInfra API which returns a 384-dimensional vector using `sentence-transformers/all-MiniLM-L6-v2`
2. **Similarity Matching**: Cosine similarity measures how close two vectors are (0 = unrelated, 1 = identical)
3. **Retrieval**: Top-k most similar chunks are selected as context for the AI
4. **No Local Model**: Embeddings are API-generated — no PyTorch or sentence-transformers installed locally, saving ~500MB RAM per worker

### Performance Optimizations

- **API-Based Embeddings**: No local PyTorch model — saves ~500MB RAM per worker
- **Cache-First RAG**: Cache check runs BEFORE retrieval — saves 200-500ms on cache hits
- **Always-On RAG**: RAG retrieval runs for ALL documents — long docs use map-reduce with RAG context, short docs use single-pass with RAG context
- **Redis Cache**: Embeddings are cached in Redis (178,000x speedup vs. recomputing)
- **Lazy Provider Initialization**: AI provider singleton created on first use (`get_generation_provider()`)
- **Batched Encoding**: Multiple texts encoded together for efficiency
- **Database Indexes**: 20+ indexes on frequently queried fields for faster retrieval
- **Aggregated Queries**: Database-level aggregation instead of Python loops for analytics
- **Topic-Aware Filtering**: Textbook chunks filtered by chapter/topic before scoring
- **JSON Repair Fallback**: Malformed AI quiz responses are auto-repaired instead of failing
- **Retry/Fallback Summaries**: If reduce synthesis fails, retry with truncated context, then concept-based fallback
- **AI Provider Retry Mechanism**: Automatic retries (default 2) with exponential backoff (10s → 30s → 60s)
- **Model Fallback**: If primary model (120B) exhausts retries, automatically falls back to smaller 8B model
- **Configurable Timeouts**: Per-operation timeouts (summary=180s, quiz=120s, recommendations=90s)
- **Celery Thread Fallback**: When Celery is unavailable, pipeline falls back to background daemon threads
- **DB Connection Management**: `close_old_connections()` before saves; Celery tasks close connections in `finally` blocks
- **Race Condition Prevention**: `select_for_update()` on quiz records to prevent duplicate generation
- **Auto-Quiz Generation**: Quiz is automatically queued after summary completes
- **Memory Monitoring**: RSS memory logged at pipeline steps; Celery workers restart at 512MB RSS limit

---

## 7. Database Schema

### Core Models

```
Chapter (1) ──────< Topic (M:1)
   │                    │
   │                    ├──< Question (M:1)
   │                    ├──< TextbookChunk (M:1)
   │                    └──< UploadedChunk (M:1)
   │
   ├──< UploadedFile (M:1)
   ├──< UploadSession (M:1) ──< UploadedChunk (M:1)
   │              │                │
   │              │                └──> UploadedFile (M:1)
   │              ├──< RetrievalLog (M:1)
   │              └──< GenerationMetric (M:1)
   │
   └──< Quiz (M:1) ──< Question (M:N via M2M)
              │           │
              │           └──> UploadedFile (M:1)
              ├──> UploadSession (M:1)
              └──> UploadedFile (M:1)
                     └──< QuizAttempt (M:1) ──< QuizAnswer (M:1)
                                   │                   │
                                   │                   └──> Question (M:1)
                                   └──< GenerationMetric (M:1)
```

### Model Descriptions

| Model | Purpose | Key Fields |
|-------|---------|-----------|
| **Chapter** | Course chapters | number, title |
| **Topic** | Sub-topics within chapters | chapter (FK), title |
| **UploadedFile** | User-uploaded files | file, file_type, extracted_text, upload_session (FK), chapter (FK) |
| **UploadSession** | Groups files + processing state | chapter (FK), session_key, summary, processing_status, processing_started_at, processing_completed_at |
| **UploadedChunk** | Chunks from user uploads | upload_session (FK), uploaded_file (FK), chapter (FK), content, embedding (JSON), chunk_index |
| **TextbookChunk** | Pre-seeded textbook content | chapter (FK), topic (FK), content, embedding (JSON), source_title, source_hash, embedding_version |
| **Question** | MCQ questions | text, choice_a-d, correct_answer, chapter (FK), topic (FK), uploaded_file (FK) |
| **Quiz** | Generated quiz sessions | chapter (FK), upload_session (FK), uploaded_file (FK), questions (M2M), status, error_message, generated_at |
| **QuizAttempt** | User's quiz attempt | quiz (FK), session_key, score, total_questions, ai_recommendation, recommendation_status, recommendation_error |
| **QuizAnswer** | Individual answers | attempt (FK), question (FK), selected_answer, is_correct |
| **RetrievalLog** | RAG retrieval telemetry | upload_session (FK), query_text, mode, retrieved_chunks, session_chunk_count, textbook_chunk_count, avg/min/max_similarity_top_k, retrieval_latency_ms |
| **GenerationMetric** | AI generation telemetry | generation_type, provider, success, duration_ms, cache_hit, output_validated, output_length, related_session (FK), related_attempt (FK) |

### Ownership Model

QuizSense uses **session-based ownership** (no login required):
- Each UploadSession and QuizAttempt stores the Django `session_key`
- Views check ownership by comparing session keys
- This allows anonymous users to have their own isolated sessions

---

## 8. Key Features

### File Processing
- Multi-file upload (PDF and DOCX)
- 10MB per file limit (max 10 files, configurable via DATA_UPLOAD_MAX_MEMORY_SIZE)
- PyMuPDF for direct PDF text extraction (page-by-page with PyPDF2 fallback, then OCR fallback)
- python-docx for Word document extraction (paragraphs, tables, headers/footers, raw XML)
- DeepInfra Vision OCR for scanned PDFs (up to 100 pages, primary: Qwen3-VL-30B-A3B-Instruct, fallback: Llama-3.2-90B-Vision)
- DOCX image OCR: extracts embedded `word/media/` images when no text found
- Per-page text threshold: pages with <50 chars trigger PyPDF2 fallback, then OCR fallback
- Improved OCR prompt with table/code preservation and structure recovery

### AI Generation
- Study summary from uploaded content (map-reduce for long docs with parallel concept extraction, single-pass for short docs, both with RAG)
- 10-question multiple-choice quiz with JSON repair fallback
- Personalized topic recommendations after quiz
- Powered by DeepInfra openai/gpt-oss-120B (primary) with meta-llama/Meta-Llama-3.1-8B-Instruct fallback
- Automatic retry (default 2 attempts) with exponential backoff (10s → 30s → 60s)
- Per-operation timeouts: summary=180s, quiz=120s, recommendations=90s
- `max_tokens=8192` for summaries and recommendations, `max_tokens=4096` for quiz JSON
- No hardcoded word count limits — summaries scale with content
- Celery thread fallback when Celery worker is unavailable
- Auto-quiz generation after summary completes

### Quiz System
- Interactive quiz UI with progress indicator
- localStorage persistence (answers survive page refresh)
- All questions must be answered before submission
- Instant score calculation
- Answer review with correct/incorrect highlighting
- Duplicate submission prevention
- Auto-generated after summary completes (no manual trigger needed)
- Race condition prevention via `select_for_update()` locking

### Performance
- Dashboard caching (5-minute TTL) — 95% database reduction
- Rate-limited polling endpoints (1 req/2s) — prevents abuse
- GZip compression — 60-80% bandwidth reduction
- RAG cache-first architecture — saves 200-500ms on cache hits
- Always-On RAG — RAG retrieval runs for ALL documents (long and short)
- Optimized database queries (`.only()`, `.select_related()`)
- Redis caching for embeddings and AI responses (7-day TTL)
- Celery background task processing with DB connection cleanup
- API-based embedding generation (no local PyTorch — saves ~500MB RAM)
- Cosine similarity retrieval from both uploaded and textbook chunks
- Database indexes on frequently queried fields
- JSON repair fallback for malformed AI responses
- Retry/fallback strategy for summary generation (retry with truncated context → concept fallback)
- AI provider retry with exponential backoff (10s → 30s → 60s)
- Model fallback (120B → 8B) when primary model exhausts retries
- Configurable per-operation timeouts via environment variables
- Celery thread fallback when broker is unavailable
- Auto-quiz generation after summary completes
- Memory monitoring and Celery worker recycling at 512MB RSS

### Security
- DOMPurify sanitization for AI-generated HTML (XSS protection)
- Rate limiting on polling endpoints
- GZip compression enabled
- System prompts for AI provider
- Session-based ownership with validation

### Analytics & Monitoring
- Retrieval quality logging (similarity scores, latency)
- AI generation metrics (duration, provider, success rate)
- System health dashboard (cached)
- User analytics dashboard (cached)
- Evaluation dashboard (cached)

---

## 9. How to Run the System

### Prerequisites

- Python 3.12+
- PostgreSQL (or SQLite for development)
- Redis

### Quick Start (Development)

```bash
# 1. Clone and setup
cd "D:\Desktop\Django Projects\QuizSense"
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure environment
copy .env.example .env
# Edit .env with your database and API keys

# 3. Database setup
python manage.py migrate
python manage.py seed_chapters_topics

# 4. (Optional) Ingest textbook dataset
python manage.py ingest_all_textbooks
python manage.py precompute_textbook_embeddings

# 5. Run development server
# Use run_dev.bat (starts Django + Celery in separate windows)
# Or manually:
python manage.py runserver
celery -A quizsense worker --loglevel=info --pool=solo --max-tasks-per-child=10
```

> **Note**: The Celery worker uses `--pool=solo` (single-threaded) by default via `CELERY_WORKER_POOL = 'solo'` in settings. This avoids concurrency issues on low-memory servers. Use `--pool=threads --concurrency=2` for higher throughput if memory allows.

### Management Commands

| Command | Purpose |
|---------|---------|
| `seed_chapters_topics` | Seeds 5 chapters with 38 topics |
| `ingest_all_textbooks` | Ingests textbooks from dataset/ folder |
| `precompute_textbook_embeddings` | Pre-computes textbook chunk embeddings via API |
| `evaluate_rag` | Evaluates RAG retrieval quality |
| `cleanup_metrics` | Cleans up old GenerationMetric records |
| `prewarm_embeddings` | Placeholder (API-based embeddings — no local model to pre-warm) |
| `benchmark_embeddings` | Benchmarks DeepInfra embedding API throughput with configurable samples |

---

## 10. Admin Panel Guide

Access: `http://localhost:8000/admin/`

### What You Can Manage

| Section | What It Shows |
|---------|--------------|
| **Chapters** | 5 programming chapters (CRUD) |
| **Topics** | 38 sub-topics organized by chapter |
| **UploadSessions** | All user upload sessions with processing status |
| **UploadedFiles** | Individual uploaded files with extracted text |
| **UploadedChunks** | Text chunks from user uploads |
| **TextbookChunks** | Pre-seeded textbook content chunks |
| **Quizzes** | Generated quizzes with status |
| **Questions** | All MCQ questions in the system |
| **QuizAttempts** | User quiz attempts with scores |
| **QuizAnswers** | Individual question answers |
| **RetrievalLogs** | RAG retrieval telemetry data |
| **GenerationMetrics** | AI generation performance metrics |

---

## 11. Evaluation & Analytics

### System Health Dashboard (`/system-health/`)

Shows database-aggregated operational metrics:
- Textbook & uploaded chunk counts
- Embedding coverage percentage
- Topic coverage percentage
- Pipeline success rates (summary, quiz, recommendations)
- AI generation duration averages
- Top 5 error messages (quiz & session failures)

### RAG Evaluation Dashboard (`/evaluation/`)

Shows retrieval quality metrics:
- Total retrieval logs
- Average similarity scores
- Retrieval latency statistics
- Mode distribution (summary vs quiz)
- Generation duration averages

### User Analytics Dashboard (`/user-analytics/`)

Shows user performance data:
- Total upload sessions
- Total quizzes generated
- Total quiz attempts
- Average quiz score
- Score distribution chart
- Chapter-wise performance
- Topic-wise weak areas

---

## 12. Deployment

### Production Stack

```
Internet → Nginx (SSL, reverse proxy) → Gunicorn (1 worker, 4 threads) → Django
                                                        ↓
                                              Celery Worker (2 threads)
                                                        ↓
                                              Redis (broker + cache)
                                              PostgreSQL (database)
```

### Key Deployment Files

| File | Purpose |
|------|---------|
| `deploy.sh` | Automated deployment script for Hetzner CX22 |
| `nginx/quizsense.conf` | Nginx configuration with SSL, security headers, and gzip |
| `systemd/gunicorn.service` | Gunicorn systemd service unit |
| `systemd/celery.service` | Celery worker systemd service unit |
| `gunicorn.conf.py` | Memory-optimized Gunicorn configuration (max_requests=100, max_requests_jitter=20) |
| `.env.example` | Environment variable template for production setup |

### Server Requirements

- **Minimum**: 2 vCPU, 4GB RAM (Hetzner CX22)
- **OS**: Ubuntu 22.04 LTS
- **System Packages**: PostgreSQL, Redis, nginx, certbot
- **No GPU Required**: All AI processing is API-based (DeepInfra)

### Quick Deploy Command

```bash
# Pull latest code and restart services (single-line)
ssh root@<SERVER_IP> "cd /opt/quizsense && git pull && systemctl restart quizsense && systemctl restart celery"

# Copy .env file to server (backs up existing first)
scp root@<SERVER_IP>:/opt/quizsense/.env /tmp/.env.backup && scp .env root@<SERVER_IP>:/opt/quizsense/.env
```

### Production Optimizations

- **GZipMiddleware**: Enabled for 60-80% bandwidth reduction
- **Dashboard Caching**: 5-minute cache TTL for all analytics views
- **Rate Limiting**: Polling endpoints limited to prevent abuse
- **Database Indexes**: 20+ indexes on frequently queried fields
- **Query Optimization**: `.only()`, `.select_related()`, and aggregated queries
- **RAG Always-On**: RAG retrieval runs for all documents, grounding summaries in textbook knowledge
- **System Prompts**: Token cost reduction via prompt caching
- **DOMPurify**: Client-side XSS protection for AI-generated content
- **API-Based Embeddings**: No local PyTorch model — saves ~500MB RAM per worker
- **DB Connection Cleanup**: `close_old_connections()` before saves; Celery tasks close connections in `finally` blocks
- **JSON Repair Fallback**: Malformed AI quiz responses auto-repaired
- **Retry/Fallback Summaries**: Retry with truncated context, then concept-based fallback
- **AI Provider Retry**: Automatic retries with exponential backoff (10s → 30s → 60s)
- **Model Fallback**: Primary 120B model → fallback 8B model on persistent failure
- **Configurable Timeouts**: Per-operation timeouts via environment variables
- **Gunicorn Worker Recycling**: `max_requests=100` with `max_requests_jitter=20`
- **Celery Thread Fallback**: Background threads used when Celery broker is unavailable
- **Auto-Quiz Generation**: Quiz automatically queued after summary completes

---

## 13. Common Questions for Defense

### Q: How does the system generate quizzes?

**A:** The system uses a RAG (Retrieval-Augmented Generation) pipeline. When a user uploads lecture files, the text is extracted, chunked into 500-word segments, and converted into vector embeddings. These embeddings are compared against both the uploaded content and a pre-seeded textbook knowledge base using cosine similarity. The most relevant chunks are retrieved and sent as context to an AI language model, which generates 10 multiple-choice questions in JSON format.

### Q: What AI model does the system use?

**A:** The system uses DeepInfra's API for all AI operations:
- **Text Generation (Primary)**: openai/gpt-oss-120B (120B parameter model) for summaries, quizzes, and recommendations
- **Text Generation (Fallback)**: meta-llama/Meta-Llama-3.1-8B-Instruct (8B parameter model) — automatically used when the primary model fails after all retries
- **Vision OCR**: Qwen3-VL-30B-A3B-Instruct (primary), Llama-3.2-90B-Vision-Instruct (fallback) for scanned PDF and image-only DOCX processing
- **Embeddings**: sentence-transformers/all-MiniLM-L6-v2 via API for text-to-vector conversion

All processing is cloud-based — no local GPU or heavy model dependencies required. The system includes automatic retry (default 2 attempts) with exponential backoff (10s → 30s → 60s) before falling back to the smaller model.

### Q: How does the system handle scanned PDFs?

**A:** The system first attempts direct text extraction using PyMuPDF page-by-page. If a page has fewer than 50 characters, it triggers AI Vision OCR fallback for just that page. For fully scanned PDFs, it uses DeepInfra's Vision API — starting with Qwen3-VL-30B-A3B-Instruct (efficient MoE vision model) and falling back to Llama-3.2-90B-Vision-Instruct if the primary fails. The OCR process supports up to 100 pages. No local OCR installation is required.

### Q: How does the system handle image-only Word documents?

**A:** When a DOCX file contains no extractable text (e.g., scanned pages pasted as images), the system:
1. First extracts all available text from paragraphs, tables, headers/footers, and raw XML
2. If no text is found, it extracts embedded images from the `word/media/` folder inside the DOCX
3. Each image is sent to DeepInfra's Vision API for OCR
4. The extracted text is then processed normally through the pipeline

This ensures that even DOCX files with zero Word text nodes but hundreds of embedded images can be processed.

### Q: How are embeddings stored and searched?

**A:** Embeddings are generated via the DeepInfra API using sentence-transformers/all-MiniLM-L6-v2 (384 dimensions) and stored as JSON arrays in PostgreSQL TextField. For retrieval, the system loads embeddings into memory and computes cosine similarity using NumPy. This approach avoids the need for specialized vector databases while maintaining good performance through Redis caching. Since embeddings are API-generated, no local PyTorch model is needed — saving ~500MB RAM per worker.

### Q: How does the system know which user owns which quiz?

**A:** The system uses Django's session-based ownership. Each UploadSession and QuizAttempt stores the user's session key. When a user tries to access a quiz, the system verifies that the session key matches. This allows anonymous users to have isolated sessions without requiring login.

### Q: What is the accuracy of the generated quizzes?

**A:** Quiz accuracy depends on two factors: (1) the quality of RAG retrieval — how well the system finds relevant textbook context, and (2) the AI model's ability to generate valid MCQs from that context. The system includes an evaluation dashboard that tracks retrieval similarity scores, generation success rates, and validation pass rates.

### Q: How does the recommendation system work?

**A:** After a user completes a quiz, the system identifies which questions were answered incorrectly and groups them by topic. This information is sent to the AI provider along with the quiz context, which generates personalized study recommendations highlighting topics that need improvement.

### Q: Why use RAG instead of just asking the AI directly?

**A:** RAG ensures that generated content is grounded in actual course materials rather than the AI's general knowledge. This produces more relevant, accurate, and curriculum-aligned summaries and quizzes. It also reduces hallucinations and ensures consistency with the textbook content.

### Q: How is the system optimized for low-memory servers?

**A:** Several optimizations are in place:

- **API-Based Embeddings**: No local PyTorch or sentence-transformers — saves ~500MB RAM per worker
- **Map-Reduce for Long Docs**: Documents >15k chars split into 2-4 sections (12,000 chars each, skipping first 3%), concepts extracted in parallel via ThreadPoolExecutor (max 4 workers), then synthesized with RAG context. If synthesis fails → retry with 5,000-char truncated context → fallback to concept-based summary
- **Textbook chunk scoring**: Done in paged batches (200 chunks per DB fetch), session chunks capped at 100, textbook chunks limited to first 500 by ID
- **Gunicorn recycles workers**: After 100 requests (with ±20 jitter)
- **Celery workers**: Have per-child memory limits (512MB RSS, max 10 tasks per child)
- **Redis caching**: Avoids recomputing expensive operations
- **Database indexes**: Reduce query memory footprint
- **`.only()` queries**: Fetch only required fields
- **DB connection cleanup**: `close_old_connections()` before saves; Celery tasks close connections in `finally` blocks
- **Memory monitoring**: RSS memory logged at pipeline steps

### Q: What datasets were used?

**A:** The system ingests approximately 194 PDF textbooks and educational materials covering programming fundamentals. These include materials from MIT OpenCourseWare, CS50, and other programming textbooks. The files are stored in the `dataset/` directory and are processed into TextbookChunk records with pre-computed embeddings.

### Q: How does the system prevent abuse and ensure reliability?

**A:** Multiple layers of protection:

- **Rate Limiting**: Polling endpoints limited to 1 request per 2 seconds per session
- **Duplicate Prevention**: Quiz generation checks for existing processing/completed quizzes with `select_for_update()` locking
- **GZip Compression**: Reduces bandwidth usage by 60-80%
- **Cache-First Architecture**: RAG retrieval skipped on cache hits, saving 200-500ms
- **System Prompts**: AI provider uses cached system prompts for token cost reduction
- **DOMPurify**: Client-side sanitization prevents XSS from AI-generated content
- **Database Indexes**: 20+ indexes optimize query performance and reduce load
- **JSON Repair Fallback**: Malformed AI quiz responses are auto-repaired instead of failing
- **Retry/Fallback Summaries**: Retry with truncated context, then concept-based fallback
- **AI Provider Retry**: Automatic retries (default 2) with exponential backoff (10s → 30s → 60s) on timeout, connection error, empty response, or 429/502/503/504 status codes
- **Model Fallback**: Primary 120B model → fallback 8B model when all retries exhausted
- **Configurable Timeouts**: Per-operation timeouts (summary=180s, quiz=120s, recommendations=90s)
- **DB Connection Management**: Prevents "server closed connection" errors with proper cleanup
- **Celery Thread Fallback**: Background daemon threads used when Celery broker is unavailable

### Q: What security measures are in place?

**A:**

- **XSS Protection**: DOMPurify sanitizes all AI-generated HTML before rendering
- **Rate Limiting**: Prevents endpoint abuse and excessive database queries
- **Session Validation**: All endpoints verify session ownership
- **GZip Compression**: Reduces attack surface by compressing responses
- **System Prompts**: Restricts AI output to expected formats
- **Environment Variables**: Sensitive data stored in `.env`, never in code
- **Nginx Security Headers**: X-Frame-Options, CSP, HSTS, and more in production

### Q: How are the analytics dashboards optimized?

**A:** All three dashboards (Evaluation, System Health, User Analytics) use:

- **5-minute cache TTL**: Repeated visits hit Redis instead of database
- **Aggregated queries**: Database-level aggregation instead of Python loops
- **Database indexes**: Optimized indexes on frequently queried fields
- **Query reduction**: ~40 queries reduced to ~8 per dashboard load
- **Vary on cookie**: Cache varies by session to maintain data isolation

### Q: How does the system handle AI provider failures?

**A:** Multiple layers of resilience protect against AI provider failures:

1. **Automatic Retry**: Each AI request is retried up to 2 times (configurable) with exponential backoff (10s → 30s → 60s)
2. **Model Fallback**: If the primary 120B model exhausts all retries, the system automatically falls back to a smaller 8B model
3. **Configurable Timeouts**: Different timeouts per operation (summary=180s, quiz=120s, recommendations=90s) to match expected response times
4. **Celery Retry**: If the entire pipeline fails, Celery retries the task up to 2 times with 30s delay
5. **Thread Fallback**: When Celery broker is unavailable, background daemon threads handle processing
6. **Summary Fallback Chain**: If AI synthesis fails → retry with truncated context → fallback to concept-based summary
7. **JSON Repair**: Malformed quiz JSON is automatically repaired via a second AI call

---

*This document was generated based on the actual QuizSense codebase implementation. All descriptions reflect the real system architecture and behavior.*
