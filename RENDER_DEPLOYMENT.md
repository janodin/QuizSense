# Deploying QuizSense to Render

This guide walks you through deploying the QuizSense Django app (with PostgreSQL, pgvector, Gemini API) to Render using the free plan.

---

## 1. Prerequisites
- GitHub repository with all code and `render.yaml` in root
- Render account (https://dashboard.render.com)
- Your Gemini API key (for quiz/summary generation)

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
- Add your `GEMINI_API_KEY` (from Google Gemini)
- Click **Save Changes**
- Trigger a **Manual Deploy**

---

## 4. Verify Deployment
- Visit your Render web service URL (e.g., `https://quizsense-web.onrender.com`)
- Upload a file, generate summary, take quiz, check insights

---

## 5. CLI Deployment (Alternative)
1. Install Render CLI:
   ```powershell
   npm i -g @renderinc/cli
   render login
   render blueprint apply -f render.yaml
   ```
2. Set `GEMINI_API_KEY` in dashboard as above

---

## 6. Notes
- PostgreSQL and pgvector are auto-provisioned (free tier)
- Static/media files are handled by WhiteNoise
- All production settings are pre-configured
- If you see errors, check **Logs** in Render dashboard

---

## 7. Troubleshooting
- **Gemini errors:** Check API key and usage limits
- **Database errors:** Confirm PostgreSQL is running and `DATABASE_URL` is set
- **Static files:** Run `python manage.py collectstatic` if needed

---

## 8. Useful Links
- [Render Docs](https://render.com/docs/blueprint-spec)
- [Google Gemini API](https://aistudio.google.com/app/apikey)

---

**Enjoy your deployed QuizSense app!**
