"""
Management command: setup_demo
──────────────────────────────
One-time initialisation for a fresh deployment:
  1. Train the ML risk-progression model  (models/risk_predictor.pkl)
  2. Warm the dashboard stats cache       (cache/)

Safe to re-run — both sub-commands are idempotent.

Usage:
    python manage.py setup_demo
"""

from django.core.management.base import BaseCommand
from django.core.management import call_command


class Command(BaseCommand):
    help = 'Train ML model and warm cache for a fresh deployment.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO(
            '\n══════════════════════════════════════\n'
            '  CareGap — setup_demo\n'
            '══════════════════════════════════════'
        ))

        # ── 1. Train ML model ─────────────────────────────────────
        self.stdout.write('\n[1/2] Training risk-progression model…')
        try:
            call_command('train_models', verbosity=1,
                         stdout=self.stdout, stderr=self.stderr)
        except Exception as exc:
            self.stdout.write(self.style.WARNING(
                f'  train_models skipped: {exc}'
            ))

        # ── 2. Warm dashboard cache ───────────────────────────────
        self.stdout.write('\n[2/2] Warming dashboard cache…')
        try:
            call_command('warm_cache', verbosity=1,
                         stdout=self.stdout, stderr=self.stderr)
        except Exception as exc:
            self.stdout.write(self.style.WARNING(
                f'  warm_cache skipped: {exc}'
            ))

        self.stdout.write(self.style.SUCCESS(
            '\n══════════════════════════════════════\n'
            '  Setup complete — CareGap is ready.\n'
            '══════════════════════════════════════\n'
        ))
