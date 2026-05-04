"""
Management command to pre-compute textbook embeddings.

Scans the dataset/ directory for textbook PDFs/DOCXs, chunks them,
generates embeddings, and saves them to TextbookChunk with a version number.
Skips files whose embeddings already exist and are up-to-date.

Usage:
    python manage.py precompute_textbook_embeddings
    python manage.py precompute_textbook_embeddings --reset
    python manage.py precompute_textbook_embeddings --limit 5
    python manage.py precompute_textbook_embeddings --dry-run
"""

import hashlib
import json
import time
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import transaction

from quiz.models import Chapter, TextbookChunk
from quiz.services.chunking_service import split_text_into_chunks
from quiz.services.embedding_service import embed_texts_batched
from quiz.services.file_processor import extract_text_from_pdf, extract_text_from_docx

# Increment this when the chunking strategy or embedding model changes.
EMBEDDING_VERSION = 1


class Command(BaseCommand):
    help = "Pre-compute textbook embeddings from dataset/ directory"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dataset-dir',
            type=str,
            default=str(settings.BASE_DIR / 'dataset'),
            help='Directory containing textbook files',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete all existing textbook chunks before ingesting',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit number of textbooks to process (for testing)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be processed without actually doing it',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=32,
            help='Batch size for embedding generation (default: 32)',
        )

    def handle(self, *args, **options):
        dataset_dir = Path(options['dataset_dir'])
        should_reset = options['reset']
        limit = options['limit']
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        if not dataset_dir.exists():
            self.stdout.write(self.style.ERROR(f'Dataset directory not found: {dataset_dir}'))
            return

        chapter_mapping = self._load_chapter_mapping(dataset_dir / 'chapter_mapping.json')

        if should_reset and not dry_run:
            deleted_count, _ = TextbookChunk.objects.all().delete()
            self.stdout.write(self.style.WARNING(f'Deleted {deleted_count} existing textbook chunks.'))

        # Find all PDF and DOCX files
        pdf_files = sorted(dataset_dir.rglob('*.pdf'))
        docx_files = sorted(dataset_dir.rglob('*.docx'))
        all_files = pdf_files + docx_files

        if not all_files:
            self.stdout.write(self.style.ERROR(f'No PDF or DOCX files found in {dataset_dir}'))
            return

        if limit:
            all_files = all_files[:limit]
            self.stdout.write(self.style.WARNING(f'Processing only {limit} textbooks (--limit flag)'))

        self.stdout.write(f'Found {len(all_files)} textbook files to process...\n')

        chapters = list(Chapter.objects.all())
        if not chapters:
            self.stdout.write(self.style.WARNING('No chapters found. Run seed_chapters_topics first.'))
            default_chapter, _ = Chapter.objects.get_or_create(
                number=1, defaults={'title': 'General Programming'}
            )
            chapters = [default_chapter]

        chapters_by_number = {ch.number: ch for ch in chapters}

        total_chunks = 0
        skipped_files = 0
        successful_files = 0
        failed_files = []
        chapter_distribution = {ch.number: 0 for ch in chapters}

        for idx, file_path in enumerate(all_files, 1):
            file_name = file_path.name
            self.stdout.write(f'[{idx}/{len(all_files)}] Processing: {file_name}')

            # Compute a content fingerprint to detect changes
            file_hash = self._compute_file_hash(file_path)
            source_key = f"{file_name}:{file_hash}:v{EMBEDDING_VERSION}"

            # Check if embeddings already exist and are up-to-date
            if not should_reset and TextbookChunk.objects.filter(
                source_title=file_name,
                embedding_version=EMBEDDING_VERSION,
                source_hash=file_hash,
            ).exists():
                existing_count = TextbookChunk.objects.filter(
                    source_title=file_name,
                    embedding_version=EMBEDDING_VERSION,
                    source_hash=file_hash,
                ).count()
                self.stdout.write(self.style.SUCCESS(f'  ⊗ Already embedded ({existing_count} chunks) — skipping'))
                skipped_files += 1
                continue

            if dry_run:
                self.stdout.write(f'  [DRY RUN] Would process {file_name}')
                continue

            try:
                # Extract text
                if file_path.suffix.lower() == '.pdf':
                    with file_path.open('rb') as file_obj:
                        text = extract_text_from_pdf(file_obj)
                elif file_path.suffix.lower() == '.docx':
                    with file_path.open('rb') as file_obj:
                        text = extract_text_from_docx(file_obj)
                else:
                    self.stdout.write(self.style.WARNING(f'  ⊗ Unsupported file type: {file_path.suffix}'))
                    continue

                text = (text or '').replace('\x00', ' ')

                if not text or len(text.strip()) < 100:
                    self.stdout.write(self.style.WARNING(f'  ⊗ Insufficient text extracted ({len(text)} chars)'))
                    failed_files.append((file_name, 'Insufficient text'))
                    continue

                self.stdout.write(f'  ✓ Extracted {len(text)} characters')

                # Match chapter
                chapter = self._match_chapter(file_path, chapters, chapters_by_number, chapter_mapping)

                # Delete old embeddings for this file if they exist
                if TextbookChunk.objects.filter(source_title=file_name).exists():
                    old_count, _ = TextbookChunk.objects.filter(source_title=file_name).delete()
                    self.stdout.write(f'  ✓ Removed {old_count} old embeddings for this file')

                # Chunk text
                chunks = split_text_into_chunks(text)
                self.stdout.write(f'  ✓ Split into {len(chunks)} chunks')

                if not chunks:
                    self.stdout.write(self.style.WARNING(f'  ⊗ No chunks created'))
                    failed_files.append((file_name, 'No chunks'))
                    continue

                # Generate embeddings in batches
                embeddings = embed_texts_batched(chunks, batch_size=batch_size)
                self.stdout.write(f'  ✓ Generated {len(embeddings)} embeddings')

                # Save to database
                textbook_chunks = []
                for chunk_idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
                    cleaned_chunk = chunk_text.replace('\x00', ' ')
                    textbook_chunks.append(
                        TextbookChunk(
                            chapter=chapter,
                            topic=None,
                            source_title=file_name,
                            chunk_index=chunk_idx,
                            content=cleaned_chunk,
                            embedding=embedding,
                            embedding_version=EMBEDDING_VERSION,
                            source_hash=file_hash,
                        )
                    )

                with transaction.atomic():
                    TextbookChunk.objects.bulk_create(textbook_chunks, batch_size=500)

                total_chunks += len(textbook_chunks)
                successful_files += 1
                chapter_distribution[chapter.number] += len(textbook_chunks)
                self.stdout.write(self.style.SUCCESS(
                    f'  ✓ Saved {len(textbook_chunks)} chunks (Chapter {chapter.number})\n'
                ))

                # GC between files
                import gc
                gc.collect()

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Error: {str(e)}\n'))
                failed_files.append((file_name, str(e)))

        # Summary
        self.stdout.write(self.style.SUCCESS('\n' + '=' * 60))
        self.stdout.write(self.style.SUCCESS('Pre-computation complete!'))
        self.stdout.write(f'  Successfully processed: {successful_files} files')
        self.stdout.write(f'  Skipped (up-to-date):  {skipped_files} files')
        self.stdout.write(f'  Failed:                {len(failed_files)} files')
        self.stdout.write(f'  Total chunks created:  {total_chunks}')
        self.stdout.write(f'  Total chunks in DB:    {TextbookChunk.objects.count()}')
        self.stdout.write(f'  Embedding version:     {EMBEDDING_VERSION}')

        if chapter_distribution:
            self.stdout.write(self.style.SUCCESS('\nChapter Distribution:'))
            for ch in chapters:
                count = chapter_distribution[ch.number]
                percentage = (count / total_chunks * 100) if total_chunks > 0 else 0
                self.stdout.write(f'  Chapter {ch.number} ({ch.title}): {count} chunks ({percentage:.1f}%)')

        if failed_files:
            self.stdout.write(self.style.WARNING(f'\nFailed files ({len(failed_files)}):'))
            for fname, reason in failed_files:
                self.stdout.write(f'  - {fname}: {reason}')

    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute MD5 hash of file content for change detection."""
        hasher = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _load_chapter_mapping(self, mapping_file: Path):
        if not mapping_file.exists():
            return None
        try:
            with open(mapping_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'Failed to load mapping file: {e}'))
            return None

    def _match_chapter(self, file_path, chapters, chapters_by_number, chapter_mapping):
        file_name = file_path.name
        file_stem = file_path.stem.lower()

        if chapter_mapping:
            for chapter_num, chapter_data in chapter_mapping.get('chapters', {}).items():
                if chapter_num == 'unmapped':
                    continue
                try:
                    chapter_num_int = int(chapter_num)
                except ValueError:
                    continue
                if file_name in chapter_data.get('files', []):
                    if chapter_num_int in chapters_by_number:
                        return chapters_by_number[chapter_num_int]
                for mapped_file in chapter_data.get('files', []):
                    if Path(mapped_file).stem == file_path.stem:
                        if chapter_num_int in chapters_by_number:
                            return chapters_by_number[chapter_num_int]

            for chapter_num, chapter_data in chapter_mapping.get('chapters', {}).items():
                if chapter_num == 'unmapped':
                    continue
                try:
                    chapter_num_int = int(chapter_num)
                except ValueError:
                    continue
                keywords = chapter_data.get('keywords', [])
                if any(keyword in file_stem for keyword in keywords):
                    if chapter_num_int in chapters_by_number:
                        return chapters_by_number[chapter_num_int]

        return self._match_chapter_fallback(file_path, chapters)

    def _match_chapter_fallback(self, file_path, chapters):
        file_str = str(file_path).lower()
        for chapter in chapters:
            if f'chapter{chapter.number}' in file_str or f'chapter {chapter.number}' in file_str:
                return chapter
        for chapter in chapters:
            title_keywords = chapter.title.lower().split()
            if any(keyword in file_str for keyword in title_keywords if len(keyword) > 4):
                return chapter
        return chapters[0]
