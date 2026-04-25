# QuizSense - Web Application Development Tasks

## Phase 1: Project Setup
- [x] Create Django project (`quizsense`)
- [x] Create Django app (`quiz`)
- [x] Install and configure required dependencies (`requirements.txt`)
- [x] Configure static files and templates directory
- [x] Add Bootstrap CDN to base template
- [x] Configure Gemini API key in `settings.py` (via `.env`)

## Phase 2: Database Models
- [x] Create `Chapter` model (5 chapters of Fundamentals of Programming)
- [x] Create `Topic` model (38 topics linked to chapters)
- [x] Create `UploadedFile` model (PDF/Word file uploads)
- [x] Create `Question` model (multiple-choice questions with 4 choices)
- [x] Create `Quiz` model (10 questions per quiz)
- [x] Create `QuizAttempt` model (user quiz submissions and scores)
- [x] Create `QuizAnswer` model (individual question answers per attempt)
- [x] Run migrations

## Phase 3: File Upload & Processing (OCR + File Parsing)
- [x] Implement PDF text extraction (file parsing)
- [x] Implement Word document text extraction (file parsing)
- [x] Implement OCR processing for scanned/image-based PDFs
- [x] Create file upload view and template (Landing Page)
- [x] Store extracted text and associate with chapters/topics

## Phase 4: Gemini API Integration
- [x] Install `google-generativeai` package (upgraded to `google-genai` 1.66.0 new SDK)
- [x] Create Gemini API service (`services/gemini_service.py`)
- [x] Implement prompt engineering for MCQ generation (10 questions, 4 choices, JSON response)
- [x] Implement prompt for topic weakness analysis and recommendations
- [x] Handle Gemini API response parsing and error handling

## Phase 5: Quiz Generation Logic
- [x] Send extracted text to Gemini API to generate 10 MCQs
- [x] Parse Gemini JSON response and save questions to database
- [x] Create quiz generation view (generates 10 questions per quiz)
- [x] Link generated questions to their source chapter/topic

## Phase 6: Quiz Interface
- [x] Create quiz-taking page with radio buttons for answers
- [x] Implement quiz submission and scoring logic
- [x] Create quiz results page (score summary)
- [x] Create review quiz page (show correct/incorrect answers)

## Summary Feature (User Addition)
- [x] Add `summary` field to `UploadedFile` model
- [x] Add `generate_summary()` to `gemini_service.py`
- [x] Update `home` view to call `generate_summary()` and redirect to study summary page
- [x] Add `study_summary` view and URL (`summary/<id>/`)
- [x] Create `templates/quiz/summary.html` with marked.js markdown rendering
- [x] Run migration (`0002_uploadedfile_summary`)

## Phase 7: Insights & Recommendations
- [x] Implement topic performance analysis per chapter
- [x] Send quiz result data to Gemini API for intelligent topic recommendations
- [x] Create insights page (display AI-generated recommendations for weak topics)
- [x] Display chapter-wise performance breakdown

## Phase 8: Frontend & Design (Bootstrap Templates)
- [x] Design base template with navbar and footer
- [x] Style landing/upload page (Figure 4 from paper)
- [x] Style quiz interface page (Figure 5 from paper)
- [x] Style quiz results page (Figure 6 from paper)
- [x] Style review quiz page (Figure 7 from paper)
- [x] Style insights page (Figure 8 from paper)
- [x] Apply cross-page UI/UX refinements aligned with CamantoOutline.pdf (spacing, hierarchy, responsiveness, accessibility)
- [x] Add Phase 2 UX polish (question-by-question quiz navigation, charts, and long-page navigation aids)

## Phase 9: Seed Data & Testing
- [x] Seed the 5 chapters and 38 topics into the database
- [x] Test full quiz flow (upload → OCR/parse → Gemini generates MCQs → take quiz → results → AI insights)
- [ ] Test Gemini API responses and edge cases

## Phase 10: RAG Implementation & Optimization
- [x] Implement pgvector extension and vector fields
- [x] Create TextbookChunk and UploadedChunk models with embeddings
- [x] Implement chunking service (500 words, 100 overlap)
- [x] Implement embedding service (sentence-transformers/all-MiniLM-L6-v2)
- [x] Implement RAG retrieval service with cosine similarity
- [x] Wire RAG into quiz generation and summary flows
- [x] Create textbook ingestion management command
- [x] Create chapter mapping manifest for proper distribution
- [x] Update ingestion to use manifest-based chapter assignment
- [ ] Re-ingest textbooks with proper chapter distribution (run: `python manage.py ingest_all_textbooks --reset`)
- [ ] Verify all chapters have textbook chunks (not just Chapter 1)
- [ ] Test RAG retrieval returns textbook context for all chapters

## Phase 11: Deployment
- [x] Prepare Render deployment configuration (`render.yaml`)
- [x] Add production-ready Django settings for hosted deployment
