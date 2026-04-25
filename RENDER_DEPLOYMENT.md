# Deploying QuizSense to Render

This guide walks you through deploying the QuizSense Django app (with PostgreSQL, pgvector, MiniMax API) to Render using the free plan.

---

## 1. Prerequisites
- GitHub repository with all code and `render.yaml` in root
- Render account (https://dashboard.render.com)
- Your MiniMax API key (for quiz/summary generation) — get one at https://www.minimaxi.chat

---

## 2. Deploy via Render Dashboard (Recommended)
1. **Push all code to GitHub**
2. Go to [Render Dashboard](https://dashboard.render.com)
3. Click **New + → Blueprint**
4. Connect your repo (Render will auto-detect `render.yaml`)
5. Click **Apply Blueprint**
6. Wait for services to build and deploy

---

## 3. Set Environment Variables
- Go to **quizsense-web** service in Render
- Click **Environment**
- Add your `MINIMAX_API_KEY` (from MiniMax)
- Click **Save Changes**
- Trigger a **Manual Deploy**

---

## 4. Post-Deployment: Ingest Textbooks (Important!)

The startup command runs migrations and seeds chapters/topics, but **textbook RAG ingestion must be done separately** the first time you deploy (or after a database reset).

This populates the `TextbookChunk` table with embedded textbook content for the RAG pipeline.

### Option A: Via Render SSH (easiest for one-time setup)
1. Go to your **quizsense-web** service → Shell
2. Run:
   ```bash
   python manage.py ingest_all_textbooks --reset
   ```
   This ingests all PDFs from the `dataset/` folder with proper chapter mapping.
3. Verify: Run `python manage.py shell` then:
   ```python
   from quiz.models import TextbookChunk
   from django.db.models import Count
   print(TextbookChunk.objects.values('chapter__number').annotate(c=Count('id')))
   ```

### Option B: Via a management command script (scheduled re-ingestion)
To re-ingest textbooks automatically on each deploy, add to `render.yaml` `startCommand`:
```bash
python manage.py ingest_all_textbooks && gunicorn ...
```
But note this will re-ingest every deploy, so prefer Option A for the initial setup.

---

## 5. Verify Deployment
- Visit your Render web service URL (e.g., `https://quizsense-web.onrender.com`)
- Upload a PDF or DOCX file, generate a summary, take a quiz, check insights
- If textbook chunks were ingested, cross-reference notes will appear in AI responses

---

## 6. CLI Deployment (Alternative)
1. Install Render CLI:
   ```powershell
   npm i -g @renderinc/cli
   render login
   render blueprint apply -f render.yaml
   ```
2. Set `MINIMAX_API_KEY` in dashboard as above

---

## 7. Notes
- PostgreSQL and pgvector are auto-provisioned (free tier)
- PostgreSQL major version 16 is used (pgvector compatibility)
- Static/media files are handled by WhiteNoise
- All production settings are pre-configured
- If you see errors, check **Logs** in Render dashboard
- The `dataset/` folder with textbook PDFs must be committed to GitHub for ingestion to work

---

## 8. Troubleshooting
- **MiniMax API errors:** Check API key and usage limits at minimaxi.chat
- **No cross-reference notes in AI responses:** Run `ingest_all_textbooks` to populate TextbookChunk table
- **Database errors:** Confirm PostgreSQL is running and `DATABASE_URL` is set
- **Static files:** Run `python manage.py collectstatic` if needed
- **OCR not working:** Scanned PDFs require poppler + tesseract-ocr system packages (not available on Render free tier — OCR will be silently skipped)

---

## 9. System Dependencies for OCR

Render's free tier does **not** include system packages for OCR. If you need scanned PDF support:
- Install poppler and tesseract-ocr via a **custom build script** (Render paid plans only)
- On the free tier, scanned/image PDFs will fall back to empty text extraction
- Text-based PDFs work fine without OCR

---

## 10. Useful Links
- [Render Docs](https://render.com/docs/blueprint-spec)
- [MiniMax API](https://www.minimaxi.chat)
- [pgvector Django](https://github.com/jp Professionals/django-pgvector)
- [QuizSense GitHub](https://github.com/outsourc-e/hermes-workspace) — if applicable

---

**Enjoy your deployed QuizSense app!**
