import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'quizsense.settings')
django.setup()

from quiz.models import TextbookChunk, UploadedChunk

print("Clearing existing embeddings to allow for dimension change (384 -> 768)...")
TextbookChunk.objects.all().delete()
UploadedChunk.objects.all().delete()
print("Done.")
