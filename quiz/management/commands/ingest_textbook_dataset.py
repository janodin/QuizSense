from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from quiz.models import Chapter, TextbookChunk
from quiz.services.chunking_service import split_text_into_chunks
from quiz.services.embedding_service import embed_texts
from quiz.services.file_processor import extract_text_from_docx, extract_text_from_pdf


class Command(BaseCommand):
    help = "Ingest pre-loaded textbook files into pgvector-backed TextbookChunk rows."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dataset-dir',
            type=str,
            default=str(settings.BASE_DIR / 'dataset' / 'textbook'),
            help='Directory containing textbook files (.txt, .md, .pdf, .docx).',
        )
        parser.add_argument(
            '--chapter',
            type=int,
            default=None,
            help='Optional chapter number override for all files.',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete existing textbook chunks before ingest.',
        )

    def handle(self, *args, **options):
        dataset_dir = Path(options['dataset_dir'])
        chapter_override = options['chapter']
        should_reset = options['reset']

        if not dataset_dir.exists():
            raise CommandError(f'Dataset directory not found: {dataset_dir}')

        if should_reset:
            deleted_count, _ = TextbookChunk.objects.all().delete()
            self.stdout.write(self.style.WARNING(f'Deleted {deleted_count} existing textbook chunks.'))

        supported_files = []
        for pattern in ('*.txt', '*.md', '*.pdf', '*.docx'):
            supported_files.extend(sorted(dataset_dir.rglob(pattern)))

        if not supported_files:
            raise CommandError('No supported files found in dataset directory.')

        created_total = 0
        for file_path in supported_files:
            chapter = self._resolve_chapter(file_path, chapter_override)
            if not chapter:
                self.stdout.write(self.style.WARNING(f'Skipped {file_path.name}: no chapter match.'))
                continue

            text = self._extract_text(file_path)
            if not text.strip():
                self.stdout.write(self.style.WARNING(f'Skipped {file_path.name}: empty text.'))
                continue

            chunks = split_text_into_chunks(text)
            embeddings = embed_texts(chunks)

            objects = [
                TextbookChunk(
                    chapter=chapter,
                    source_title=file_path.stem,
                    chunk_index=index,
                    content=chunk,
                    embedding=embeddings[index],
                )
                for index, chunk in enumerate(chunks)
            ]
            TextbookChunk.objects.bulk_create(objects)
            created_total += len(objects)
            self.stdout.write(self.style.SUCCESS(f'Ingested {len(objects)} chunks from {file_path.name}'))

        self.stdout.write(self.style.SUCCESS(f'Textbook ingest complete. Created {created_total} chunks.'))

    def _resolve_chapter(self, file_path, chapter_override):
        if chapter_override:
            return Chapter.objects.filter(number=chapter_override).first()

        name = file_path.stem.lower()
        for chapter in Chapter.objects.all():
            chapter_token = f'chapter{chapter.number}'
            chapter_token_dash = f'chapter-{chapter.number}'
            if chapter_token in name or chapter_token_dash in name:
                return chapter
        return None

    def _extract_text(self, file_path):
        suffix = file_path.suffix.lower()
        if suffix in ('.txt', '.md'):
            return file_path.read_text(encoding='utf-8', errors='ignore')

        with file_path.open('rb') as file_obj:
            if suffix == '.pdf':
                return extract_text_from_pdf(file_obj)
            if suffix == '.docx':
                return extract_text_from_docx(file_obj)

        return ''
