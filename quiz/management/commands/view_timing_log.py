"""
View the processing timing log.
Usage: python manage.py view_timing_log
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "View the processing timing log"

    def add_arguments(self, parser):
        parser.add_argument('--session', type=int, help='Filter by session ID')
        parser.add_argument('--tail', type=int, default=0, help='Show only last N lines')

    def handle(self, *args, **options):
        from pathlib import Path
        log_file = Path(__file__).resolve().parent.parent.parent.parent / "quizsense_processing.log"

        if not log_file.exists():
            self.stdout.write(self.style.WARNING("No timing log found yet. Upload a file first."))
            return

        lines = log_file.read_text(encoding="utf-8").splitlines()

        if options['session']:
            session_str = f"[SESSION {options['session']}]"
            lines = [l for l in lines if session_str in l]

        if options['tail']:
            lines = lines[-options['tail']:]

        if not lines:
            self.stdout.write(self.style.WARNING("No matching log entries found."))
            return

        self.stdout.write("=" * 70)
        self.stdout.write(self.style.NOTICE("QuizSense Processing Timing Log"))
        self.stdout.write("=" * 70)
        for line in lines:
            if "ERROR" in line:
                self.stdout.write(self.style.ERROR(line))
            elif "END" in line:
                self.stdout.write(self.style.SUCCESS(line))
            elif "START" in line:
                self.stdout.write(self.style.NOTICE(line))
            else:
                self.stdout.write(line)
        self.stdout.write("=" * 70)