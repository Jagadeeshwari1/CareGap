import datetime
from django.core.management.base import BaseCommand
from django.db.models import Max
from django.utils import timezone
from patients.models import Patient


class Command(BaseCommand):
    help = 'Marks inactive patients as deceased if their last encounter/observation is older than 5 years'

    def handle(self, *args, **options):
        five_years_ago = timezone.now() - datetime.timedelta(days=5 * 365)

        marked_count = (
            Patient.objects
            .filter(is_deceased=False)
            .annotate(
                last_enc=Max('encounters__start'),
                last_obs=Max('observations__date'),
            )
            .filter(last_enc__lt=five_years_ago, last_obs__lt=five_years_ago)
            .update(is_deceased=True)
        )

        self.stdout.write(self.style.SUCCESS(
            f'Successfully marked {marked_count} patients as deceased.'
        ))
