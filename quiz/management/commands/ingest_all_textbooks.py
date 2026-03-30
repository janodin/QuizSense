from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings
from quiz.models import Chapter, Topic, TextbookChunk
from quiz.services.file_processor import extract_text_from_pdf, extract_text_from_docx
from quiz.services.chunking_service import split_text_into_chunks
from quiz.services.embedding_service import embed_texts


class Command(BaseCommand):
    help = "Ingest all textbook PDFs from dataset folder into TextbookChunk with embeddings"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dataset-dir',
            type=str,
            default=str(settings.BASE_DIR / 'dataset'),
            help='Directory containing textbook folders with PDFs',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete existing textbook chunks before ingesting',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit number of textbooks to process (for testing)',
        )

    def handle(self, *args, **options):
        dataset_dir = Path(options['dataset_dir'])
        should_reset = options['reset']
        limit = options['limit']

        if not dataset_dir.exists():
            self.stdout.write(self.style.ERROR(f'Dataset directory not found: {dataset_dir}'))
            return

        if should_reset:
            deleted_count, _ = TextbookChunk.objects.all().delete()
            self.stdout.write(self.style.WARNING(f'Deleted {deleted_count} existing textbook chunks.'))

        # Find all PDF and DOCX files in dataset
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

        # Get all chapters for topic matching
        chapters = list(Chapter.objects.all())
        if not chapters:
            self.stdout.write(self.style.WARNING('No chapters found in database. Run seed_chapters_topics first.'))
            # Create a default chapter
            default_chapter, _ = Chapter.objects.get_or_create(
                number=1,
                defaults={'title': 'General Programming'}
            )
            chapters = [default_chapter]

        total_chunks = 0
        successful_files = 0
        failed_files = []

        for idx, file_path in enumerate(all_files, 1):
            file_name = file_path.name
            self.stdout.write(f'[{idx}/{len(all_files)}] Processing: {file_name}')

            try:
                # Extract text from file
                if file_path.suffix.lower() == '.pdf':
                    text = extract_text_from_pdf(str(file_path))
                elif file_path.suffix.lower() == '.docx':
                    text = extract_text_from_docx(str(file_path))
                else:
                    self.stdout.write(self.style.WARNING(f'  ⊗ Unsupported file type: {file_path.suffix}'))
                    continue

                if not text or len(text.strip()) < 100:
                    self.stdout.write(self.style.WARNING(f'  ⊗ Insufficient text extracted ({len(text)} chars)'))
                    failed_files.append((file_name, 'Insufficient text'))
                    continue

                self.stdout.write(f'  ✓ Extracted {len(text)} characters')

                # Determine chapter based on filename or folder
                chapter = self._match_chapter(file_path, chapters)
                
                # Split text into chunks
                chunks = split_text_into_chunks(text)
                self.stdout.write(f'  ✓ Split into {len(chunks)} chunks')

                if not chunks:
                    self.stdout.write(self.style.WARNING(f'  ⊗ No chunks created'))
                    failed_files.append((file_name, 'No chunks'))
                    continue

                # Generate embeddings
                embeddings = embed_texts(chunks)
                self.stdout.write(f'  ✓ Generated {len(embeddings)} embeddings')

                # Save to database
                textbook_chunks = []
                for idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
                    textbook_chunks.append(
                        TextbookChunk(
                            chapter=chapter,
                            topic=None,  # Can be assigned later based on topic matching
                            source_title=file_name,
                            chunk_index=idx,
                            content=chunk_text,
                            embedding=embedding,
                        )
                    )

                TextbookChunk.objects.bulk_create(textbook_chunks)
                total_chunks += len(textbook_chunks)
                successful_files += 1
                self.stdout.write(self.style.SUCCESS(f'  ✓ Saved {len(textbook_chunks)} chunks to database\n'))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Error: {str(e)}\n'))
                failed_files.append((file_name, str(e)))

        # Summary
        self.stdout.write(self.style.SUCCESS('\n' + '='*60))
        self.stdout.write(self.style.SUCCESS('✓ Textbook ingestion complete!'))
        self.stdout.write(f'  Successfully processed: {successful_files}/{len(all_files)} files')
        self.stdout.write(f'  Total chunks created: {total_chunks}')
        self.stdout.write(f'  Total chunks in database: {TextbookChunk.objects.count()}')

        if failed_files:
            self.stdout.write(self.style.WARNING(f'\n⚠ Failed files ({len(failed_files)}):'))
            for fname, reason in failed_files:
                self.stdout.write(f'  • {fname}: {reason}')

    def _match_chapter(self, file_path, chapters):
        """Try to match file to a chapter based on filename or folder name"""
        file_str = str(file_path).lower()
        
        # Try to match by chapter number
        for chapter in chapters:
            if f'chapter{chapter.number}' in file_str or f'chapter {chapter.number}' in file_str:
                return chapter
        
        # Try to match by chapter title keywords
        for chapter in chapters:
            title_keywords = chapter.title.lower().split()
            if any(keyword in file_str for keyword in title_keywords if len(keyword) > 4):
                return chapter
        
        # Default to chapter 1 if no match
        return chapters[0]
