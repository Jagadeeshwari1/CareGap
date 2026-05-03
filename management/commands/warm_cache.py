"""
Management command: warm_cache
──────────────────────────────
Pre-computes both stats endpoints and stores results in the Django
file-based cache so the first dashboard load is instant.

Usage:
    python manage.py warm_cache

Run once after the server has started (or after import_synthea).
The server does NOT need to be running — this command calls the view
logic directly in-process via Django's test client.
"""

import time
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Pre-warms the dashboard stats cache so the first load is instant.'

    def handle(self, *args, **options):
        from django.test import Client
        from django.core.cache import cache

        client = Client()

        # ── Clear stale cache entries first ───────────────────────
        self.stdout.write('Clearing old cache entries…')
        cache.delete('dashboard_stats_basic')
        cache.delete('dashboard_stats')

        # ── Warm stats/basic/ (should be < 2 s) ───────────────────
        self.stdout.write('Warming stats/basic/…', ending=' ')
        self.stdout.flush()
        t0 = time.monotonic()
        resp = client.get('/api/patients/stats/basic/')
        elapsed = time.monotonic() - t0
        if resp.status_code == 200:
            self.stdout.write(
                self.style.SUCCESS(f'done ({elapsed:.1f}s)')
            )
        else:
            self.stdout.write(
                self.style.ERROR(f'FAILED (HTTP {resp.status_code})')
            )
            return

        # ── Warm stats/ (may take 5–30 s on first cold run) ───────
        self.stdout.write(
            'Warming stats/ (this may take up to 30 s on first run)…',
            ending=' ',
        )
        self.stdout.flush()
        t0 = time.monotonic()
        resp = client.get('/api/patients/stats/')
        elapsed = time.monotonic() - t0
        if resp.status_code == 200:
            data = resp.json()
            self.stdout.write(
                self.style.SUCCESS(f'done ({elapsed:.1f}s)')
            )
            # Print a brief summary so you can verify the numbers
            cc = data.get('cohort_counts', {})
            self.stdout.write(
                f"  chronic={cc.get('chronic',0):,}  "
                f"at_risk={cc.get('at_risk',0):,}  "
                f"pediatric={cc.get('pediatric',0):,}  "
                f"deceased={cc.get('deceased',0):,}"
            )
            h = data.get('hba1c_dist', {})
            self.stdout.write(
                f"  HbA1c → normal={h.get('normal',0):,}  "
                f"prediabetes={h.get('prediabetes',0):,}  "
                f"diabetes={h.get('diabetes',0):,}"
            )
            b = data.get('bp_dist', {})
            self.stdout.write(
                f"  BP    → normal={b.get('normal',0):,}  "
                f"elevated={b.get('elevated',0):,}  "
                f"stage1={b.get('stage1',0):,}  "
                f"stage2={b.get('stage2',0):,}"
            )
        else:
            self.stdout.write(
                self.style.ERROR(f'FAILED (HTTP {resp.status_code})')
            )
            return

        self.stdout.write(self.style.SUCCESS(
            '\nCache warmed. Dashboard charts will now load instantly.'
        ))
