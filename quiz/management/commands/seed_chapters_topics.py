from django.core.management.base import BaseCommand
from quiz.models import Chapter, Topic


class Command(BaseCommand):
    help = "Seed the database with 5 chapters and 38 topics for Fundamentals of Programming"

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete existing chapters and topics before seeding',
        )

    def handle(self, *args, **options):
        if options['reset']:
            Topic.objects.all().delete()
            Chapter.objects.all().delete()
            self.stdout.write(self.style.WARNING('Deleted all existing chapters and topics.'))

        # Define 5 chapters with their topics (38 total topics)
        chapters_data = [
            {
                'number': 1,
                'title': 'Introduction to Programming',
                'topics': [
                    'What is Computer Programming',
                    'Programming Languages',
                    'Brief History of Programming',
                    'Why Learning Programming',
                    'Traits of a Good Programmer',
                    'Good Programming Practices',
                    'Qualities of a Good Program',
                    'Program Development Life Cycle',
                ]
            },
            {
                'number': 2,
                'title': 'Basic Elements of a Program',
                'topics': [
                    'Comments',
                    'Tokens',
                    'Separators',
                    'Identifiers',
                    'Keywords',
                    'Literals',
                    'Data Types',
                    'Variables',
                    'Operators',
                    'Expressions',
                    'Statements',
                    'Blocks',
                ]
            },
            {
                'number': 3,
                'title': 'Input and Output',
                'topics': [
                    'Creating Simple Programs',
                    'Structure of a Program',
                    'Displaying Outputs on Console',
                    'Getting Input from Users',
                    'Formatted Input and Output',
                ]
            },
            {
                'number': 4,
                'title': 'Control Structures',
                'topics': [
                    'If Statement',
                    'If-Else Statement',
                    'Multiple Selection',
                    'Switch Statement',
                    'Repetition Control Structure',
                    'While Loop',
                    'Do-While Loop',
                    'For Loop',
                    'Nested Loops',
                ]
            },
            {
                'number': 5,
                'title': 'Arrays and Functions',
                'topics': [
                    'Introduction to Arrays',
                    'Declaring Arrays',
                    'Accessing Array Elements',
                    'Array Manipulation',
                    'Multidimensional Arrays',
                    'Introduction to Functions',
                ]
            },
        ]

        # Create chapters and topics
        total_topics = 0
        for chapter_data in chapters_data:
            chapter, created = Chapter.objects.get_or_create(
                number=chapter_data['number'],
                defaults={'title': chapter_data['title']}
            )
            
            if created:
                self.stdout.write(self.style.SUCCESS(f'✓ Created Chapter {chapter.number}: {chapter.title}'))
            else:
                self.stdout.write(self.style.WARNING(f'○ Chapter {chapter.number} already exists: {chapter.title}'))

            # Create topics for this chapter
            for topic_title in chapter_data['topics']:
                topic, topic_created = Topic.objects.get_or_create(
                    chapter=chapter,
                    title=topic_title
                )
                if topic_created:
                    total_topics += 1
                    self.stdout.write(f'  └─ Created topic: {topic_title}')
                else:
                    self.stdout.write(f'  └─ Topic already exists: {topic_title}')

        self.stdout.write(self.style.SUCCESS(f'\n✓ Seeding complete!'))
        self.stdout.write(f'  Chapters: {Chapter.objects.count()}')
        self.stdout.write(f'  Topics: {Topic.objects.count()}')
