# QuizSense Database Seeding Guide

## Overview
This guide shows you how to populate your QuizSense database with:
1. 5 Chapters and 38 Topics (Fundamentals of Programming)
2. Textbook content from PDF files with RAG embeddings

## Prerequisites
- Virtual environment activated
- Dataset folder with textbook PDFs at: `D:\Desktop\Django Projects\QuizSense\dataset\`
- Django migrations completed

## Step-by-Step Instructions

### Step 1: Activate Virtual Environment
```powershell
cd "D:\Desktop\Django Projects\QuizSense"
.\venv\Scripts\Activate.ps1
```

### Step 2: Seed Chapters and Topics
This creates 5 chapters with 38 topics in total.

```bash
python manage.py seed_chapters_topics
```

**Expected Output:**
```
✓ Created Chapter 1: Introduction to Programming
  └─ Created topic: What is Computer Programming
  └─ Created topic: Programming Languages
  ... (8 topics total)
✓ Created Chapter 2: Basic Elements of a Program
  ... (12 topics total)
✓ Created Chapter 3: Input and Output
  ... (5 topics total)
✓ Created Chapter 4: Control Structures
  ... (9 topics total)
✓ Created Chapter 5: Arrays and Functions
  ... (6 topics total)

✓ Seeding complete!
  Chapters: 5
  Topics: 38
```

**To reset and re-seed:**
```bash
python manage.py seed_chapters_topics --reset
```

### Step 3: Ingest Textbook PDFs
This processes all PDF/DOCX files in the dataset folder and creates RAG embeddings.

```bash
python manage.py ingest_all_textbooks
```

**Expected Output:**
```
Found 30 textbook files to process...

[1/30] Processing: fundamentals-of-computer-programming.pdf
  ✓ Extracted 45823 characters
  ✓ Split into 23 chunks
  ✓ Generated 23 embeddings
  ✓ Saved 23 chunks to database

[2/30] Processing: introduction-to-programming.pdf
  ✓ Extracted 38921 characters
  ...

✓ Textbook ingestion complete!
  Successfully processed: 28/30 files
  Total chunks created: 542
  Total chunks in database: 542
```

**Options:**
```bash
# Reset existing chunks before ingesting
python manage.py ingest_all_textbooks --reset

# Process only first 5 textbooks (for testing)
python manage.py ingest_all_textbooks --limit 5

# Specify custom dataset directory
python manage.py ingest_all_textbooks --dataset-dir /path/to/dataset
```

### Step 4: Verify Database
Check if data was loaded correctly:

```bash
python manage.py shell
```

Then in the Python shell:
```python
from quiz.models import Chapter, Topic, TextbookChunk

# Check chapters
print(f"Chapters: {Chapter.objects.count()}")
for ch in Chapter.objects.all():
    print(f"  {ch}")

# Check topics
print(f"\nTopics: {Topic.objects.count()}")
for topic in Topic.objects.all()[:5]:
    print(f"  {topic}")

# Check textbook chunks
print(f"\nTextbook Chunks: {TextbookChunk.objects.count()}")
print(f"Sample chunk: {TextbookChunk.objects.first().content[:200]}...")

exit()
```

## Chapter & Topic Structure

### Chapter 1: Introduction to Programming (8 topics)
- What is Computer Programming
- Programming Languages
- Brief History of Programming
- Why Learning Programming
- Traits of a Good Programmer
- Good Programming Practices
- Qualities of a Good Program
- Program Development Life Cycle

### Chapter 2: Basic Elements of a Program (12 topics)
- Comments
- Tokens
- Separators
- Identifiers
- Keywords
- Literals
- Data Types
- Variables
- Operators
- Expressions
- Statements
- Blocks

### Chapter 3: Input and Output (5 topics)
- Creating Simple Programs
- Structure of a Program
- Displaying Outputs on Console
- Getting Input from Users
- Formatted Input and Output

### Chapter 4: Control Structures (9 topics)
- If Statement
- If-Else Statement
- Multiple Selection
- Switch Statement
- Repetition Control Structure
- While Loop
- Do-While Loop
- For Loop
- Nested Loops

### Chapter 5: Arrays and Functions (6 topics)
- Introduction to Arrays
- Declaring Arrays
- Accessing Array Elements
- Array Manipulation
- Multidimensional Arrays
- Introduction to Functions

**Total: 5 Chapters, 38 Topics**

## Troubleshooting

### Issue: "No chapters found in database"
**Solution:** Run `python manage.py seed_chapters_topics` first

### Issue: "Dataset directory not found"
**Solution:** Make sure dataset folder exists at `D:\Desktop\Django Projects\QuizSense\dataset\`

### Issue: "Insufficient text extracted"
**Solution:** Some PDFs may be image-based. Make sure Tesseract OCR is installed for scanned PDFs.

### Issue: "OperationalError: database is locked"
**Solution:** Close Django admin panel and retry

### Issue: Embeddings taking too long
**Solution:** Use `--limit 5` flag to test with fewer textbooks first

## Performance Notes

- **Seeding chapters/topics**: Very fast (< 1 second)
- **Ingesting textbooks**: Depends on number and size of PDFs
  - ~30 PDFs: 2-5 minutes
  - Embedding generation is the slowest part
  - Progress is shown for each file

## What Gets Created

1. **Chapter objects**: 5 chapters in database
2. **Topic objects**: 38 topics linked to chapters
3. **TextbookChunk objects**: Hundreds of text chunks with 384-dimensional embeddings
4. **Ready for RAG**: Semantic search is now available for quiz generation

## Next Steps

After seeding:
1. Start the Django server: `python manage.py runserver`
2. Upload a file and generate a quiz
3. The RAG system will now use textbook chunks for context
4. AI-generated summaries and questions will be enhanced with textbook knowledge

## Commands Summary

```bash
# Seed chapters and topics
python manage.py seed_chapters_topics

# Ingest textbooks (basic)
python manage.py ingest_all_textbooks

# Full reset and re-seed
python manage.py seed_chapters_topics --reset
python manage.py ingest_all_textbooks --reset

# Test with limited files
python manage.py ingest_all_textbooks --limit 5
```
