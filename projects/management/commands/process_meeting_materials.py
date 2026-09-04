from django.core.management.base import BaseCommand

from projects.meeting_processing import process_all_queued_meeting_materials


class Command(BaseCommand):
    help = "Process queued meeting material transcription and extraction drafts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of queued meeting material records to process.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit is not None and limit <= 0:
            self.stderr.write("--limit must be a positive integer.")
            return

        results = process_all_queued_meeting_materials(limit=limit)
        if not results:
            self.stdout.write("No queued meeting material records found.")
            return

        for result in results:
            self.stdout.write(
                f"MeetingMaterial {result.material_id}: {result.status}"
            )
