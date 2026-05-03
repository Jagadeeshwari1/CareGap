"""
Management Command: import_synthea
Usage: python manage.py import_synthea --data-dir /path/to/synthea/csv/

Imports ALL Synthea patients (alive + deceased) with cohort assignment:
  chronic   — alive, age ≥ 18, has active HTN (59621000) or T2D (44054006)
  at_risk   — alive, age ≥ 18, no qualifying chronic condition
  pediatric — alive, age < 18
  deceased  — DEATHDATE present

Files expected in data-dir:
  patients.csv, conditions.csv, observations.csv, encounters.csv,
  medications.csv, organizations.csv, payers.csv, payer_transitions.csv
"""

import csv
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import transaction

from patients.models import (
    Patient, Observation, Encounter, Condition,
    Medication, Organization, UrgentCare,
)

HYPERTENSION_CODE = '59621000'
DIABETES_T2_CODE  = '44054006'


def parse_date(val):
    if not val:
        return None
    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(val.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_date_only(val):
    if not val:
        return None
    try:
        return datetime.strptime(val.strip()[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def calc_age(birthdate):
    if not birthdate:
        return None
    today = date.today()
    bd = birthdate if isinstance(birthdate, date) else birthdate.date()
    return today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))


class Command(BaseCommand):
    help = 'Import all Synthea CSV files into the CareGap database (all 4 cohorts)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--data-dir',
            type=str,
            default=str(settings.SYNTHEA_DATA_DIR),
            help='Path to directory containing Synthea CSV files',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing data before import',
        )

    def handle(self, *args, **options):
        data_dir = Path(options['data_dir'])

        if not data_dir.exists():
            raise CommandError(f"Data directory not found: {data_dir}")

        if options['clear']:
            self.stdout.write('Clearing existing data...')
            Medication.objects.all().delete()
            Observation.objects.all().delete()
            Encounter.objects.all().delete()
            Condition.objects.all().delete()
            Patient.objects.all().delete()
            Organization.objects.all().delete()

        # ── 1. Load payer map ──────────────────────────────────────
        payer_map = {}
        payers_file = data_dir / 'payers.csv'
        if payers_file.exists():
            with open(payers_file, encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    payer_map[row.get('Id', '')] = row.get('NAME', 'Unknown')
            self.stdout.write(f'  Loaded {len(payer_map)} payers')

        # ── 2. Build chronic patient set from conditions.csv ───────
        # Single pass: collect patient IDs with active HTN or T2D
        self.stdout.write('  Scanning conditions for chronic disease flags...')
        chronic_pids: set[str] = set()
        cond_file = data_dir / 'conditions.csv'
        if cond_file.exists():
            with open(cond_file, encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    if (row.get('CODE', '').strip() in (HYPERTENSION_CODE, DIABETES_T2_CODE)
                            and not row.get('STOP', '').strip()):
                        chronic_pids.add(row.get('PATIENT', '').strip())
        self.stdout.write(f'  Found {len(chronic_pids)} patients with active HTN/T2D')

        # ── 3. Import all patients with cohort assignment ──────────
        patients_file = data_dir / 'patients.csv'
        if not patients_file.exists():
            raise CommandError(f"patients.csv not found in {data_dir}")

        counts = {'chronic': 0, 'at_risk': 0, 'pediatric': 0, 'deceased': 0}
        imported_pids: set[str] = set()
        batch = []
        total_processed = 0

        with open(patients_file, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                pid = row.get('Id', '').strip()
                if not pid:
                    continue

                birthdate  = parse_date_only(row.get('BIRTHDATE'))
                age        = calc_age(birthdate)
                is_dead    = bool(row.get('DEATHDATE', '').strip())

                # Assign cohort
                if is_dead:
                    cohort = 'deceased'
                elif age is not None and age < 18:
                    cohort = 'pediatric'
                elif pid in chronic_pids:
                    cohort = 'chronic'
                else:
                    cohort = 'at_risk'

                counts[cohort] += 1
                imported_pids.add(pid)

                batch.append(Patient(
                    patient_id  = pid,
                    first       = row.get('FIRST', '').strip(),
                    last        = row.get('LAST', '').strip(),
                    birthdate   = birthdate,
                    gender      = row.get('GENDER', '').strip(),
                    race        = row.get('RACE', '').strip(),
                    ethnicity   = row.get('ETHNICITY', '').strip(),
                    city        = row.get('CITY', '').strip(),
                    state       = row.get('STATE', 'CA').strip(),
                    zip_code    = row.get('ZIP', '').strip(),
                    insurance   = payer_map.get(row.get('PAYER', ''), ''),
                    lat         = float(row['LAT']) if row.get('LAT') else None,
                    lon         = float(row['LON']) if row.get('LON') else None,
                    is_deceased = is_dead,
                    cohort      = cohort,
                ))

                total_processed += 1
                if total_processed % 1000 == 0:
                    self.stdout.write(f'    Processed {total_processed} patients...')

                if len(batch) >= 2000:
                    with transaction.atomic():
                        Patient.objects.bulk_create(batch, ignore_conflicts=True)
                    batch = []

        if batch:
            with transaction.atomic():
                Patient.objects.bulk_create(batch, ignore_conflicts=True)

        total = sum(counts.values())
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Imported {total} patients\n'
            f'    Chronic:   {counts["chronic"]}\n'
            f'    At Risk:   {counts["at_risk"]}\n'
            f'    Pediatric: {counts["pediatric"]}\n'
            f'    Deceased:  {counts["deceased"]}'
        ))

        # Build patient lookup: patient_id → Patient instance
        patient_lookup = {p.patient_id: p for p in Patient.objects.all()}

        # ── 4. Payer transitions (sorted ASC, last wins = most recent) ──
        pt_file = data_dir / 'payer_transitions.csv'
        if pt_file.exists():
            transitions: dict[str, list] = defaultdict(list)
            with open(pt_file, encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    pid   = row.get('PATIENT', '').strip()
                    pname = payer_map.get(row.get('PAYER', ''), '')
                    start = row.get('START_DATE', '') or row.get('START', '')
                    if pid in imported_pids and pname:
                        transitions[pid].append((start, pname))

            insurance_map = {}
            for pid, rows in transitions.items():
                rows.sort(key=lambda x: x[0])  # ascending → last entry = most recent
                insurance_map[pid] = rows[-1][1]

            with transaction.atomic():
                for pid, ins in insurance_map.items():
                    Patient.objects.filter(patient_id=pid).update(insurance=ins)
            self.stdout.write(f'  ✓ Updated insurance for {len(insurance_map)} patients')

        # ── 5. Organizations ───────────────────────────────────────
        org_file = data_dir / 'organizations.csv'
        if org_file.exists():
            batch = []
            with open(org_file, encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    oid = row.get('Id', '').strip()
                    if not oid:
                        continue
                    batch.append(Organization(
                        org_id   = oid,
                        name     = row.get('NAME', '').strip()[:255],
                        address  = row.get('ADDRESS', '').strip()[:255],
                        city     = row.get('CITY', '').strip()[:100],
                        state    = row.get('STATE', '').strip()[:50],
                        zip_code = row.get('ZIP', '').strip()[:20],
                        lat      = float(row['LAT']) if row.get('LAT') else None,
                        lon      = float(row['LON']) if row.get('LON') else None,
                        phone    = row.get('PHONE', '').strip()[:30],
                    ))
                    if len(batch) >= 5000:
                        with transaction.atomic():
                            Organization.objects.bulk_create(batch, ignore_conflicts=True)
                        batch = []
            if batch:
                with transaction.atomic():
                    Organization.objects.bulk_create(batch, ignore_conflicts=True)
            self.stdout.write(self.style.SUCCESS('  ✓ Imported organizations'))

        # ── 6. Observations ────────────────────────────────────────
        obs_file = data_dir / 'observations.csv'
        if obs_file.exists():
            batch = []
            skipped = 0
            with open(obs_file, encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    pid = row.get('PATIENT', '').strip()
                    p   = patient_lookup.get(pid)
                    if not p:
                        skipped += 1
                        continue
                    batch.append(Observation(
                        patient     = p,
                        date        = parse_date(row.get('DATE')),
                        code        = row.get('CODE', '').strip(),
                        description = row.get('DESCRIPTION', '').strip()[:255],
                        value       = row.get('VALUE', '').strip()[:100],
                        units       = row.get('UNITS', '').strip()[:50],
                    ))
                    if len(batch) >= 5000:
                        with transaction.atomic():
                            Observation.objects.bulk_create(batch, ignore_conflicts=True)
                        batch = []
            if batch:
                with transaction.atomic():
                    Observation.objects.bulk_create(batch, ignore_conflicts=True)
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ Imported observations (skipped {skipped} orphaned)'
            ))

        # ── 7. Encounters ──────────────────────────────────────────
        enc_file = data_dir / 'encounters.csv'
        if enc_file.exists():
            batch = []
            skipped = 0
            with open(enc_file, encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    pid = row.get('PATIENT', '').strip()
                    p   = patient_lookup.get(pid)
                    if not p:
                        skipped += 1
                        continue
                    batch.append(Encounter(
                        patient         = p,
                        encounter_id    = row.get('Id', '').strip(),
                        start           = parse_date(row.get('START')),
                        stop            = parse_date(row.get('STOP')),
                        encounter_class = row.get('ENCOUNTERCLASS', '').strip()[:100],
                        description     = row.get('DESCRIPTION', '').strip()[:255],
                    ))
                    if len(batch) >= 5000:
                        with transaction.atomic():
                            Encounter.objects.bulk_create(batch, ignore_conflicts=True)
                        batch = []
            if batch:
                with transaction.atomic():
                    Encounter.objects.bulk_create(batch, ignore_conflicts=True)
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ Imported encounters (skipped {skipped} orphaned)'
            ))

        # ── 8. Conditions ──────────────────────────────────────────
        if cond_file.exists():
            batch = []
            skipped = 0
            with open(cond_file, encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    pid = row.get('PATIENT', '').strip()
                    p   = patient_lookup.get(pid)
                    if not p:
                        skipped += 1
                        continue
                    batch.append(Condition(
                        patient     = p,
                        start       = parse_date_only(row.get('START')),
                        stop        = parse_date_only(row.get('STOP')),
                        code        = row.get('CODE', '').strip()[:50],
                        description = row.get('DESCRIPTION', '').strip()[:255],
                    ))
                    if len(batch) >= 5000:
                        with transaction.atomic():
                            Condition.objects.bulk_create(batch, ignore_conflicts=True)
                        batch = []
            if batch:
                with transaction.atomic():
                    Condition.objects.bulk_create(batch, ignore_conflicts=True)
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ Imported conditions (skipped {skipped} orphaned)'
            ))

        # ── 9. Medications (active only) ───────────────────────────
        med_file = data_dir / 'medications.csv'
        if med_file.exists():
            batch = []
            skipped = 0
            with open(med_file, encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    pid = row.get('PATIENT', '').strip()
                    p   = patient_lookup.get(pid)
                    if not p:
                        skipped += 1
                        continue
                    if row.get('STOP', '').strip():   # skip stopped medications
                        continue
                    batch.append(Medication(
                        patient            = p,
                        start              = parse_date_only(row.get('START')),
                        stop               = None,
                        code               = row.get('CODE', '').strip()[:50],
                        description        = row.get('DESCRIPTION', '').strip()[:255],
                        reason_code        = row.get('REASONCODE', '').strip()[:50],
                        reason_description = row.get('REASONDESCRIPTION', '').strip()[:255],
                    ))
                    if len(batch) >= 5000:
                        with transaction.atomic():
                            Medication.objects.bulk_create(batch, ignore_conflicts=True)
                        batch = []
            if batch:
                with transaction.atomic():
                    Medication.objects.bulk_create(batch, ignore_conflicts=True)
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ Imported active medications (skipped {skipped} orphaned)'
            ))

        # ── 10. Seed Urgent Cares ──────────────────────────────────
        self._seed_urgent_cares()

        self.stdout.write(self.style.SUCCESS('\n✅  Synthea import complete!'))
        self.stdout.write(f'    Chronic:   {counts["chronic"]}')
        self.stdout.write(f'    At Risk:   {counts["at_risk"]}')
        self.stdout.write(f'    Pediatric: {counts["pediatric"]}')
        self.stdout.write(f'    Deceased:  {counts["deceased"]}')
        self.stdout.write(f'    Total:     {sum(counts.values())}')
        self.stdout.write('Next: python manage.py runserver')

    def _seed_urgent_cares(self):
        """Seed realistic California urgent care facilities."""
        if UrgentCare.objects.exists():
            return

        facilities = [
            dict(name="CityMD West Hollywood Urgent Care", city="Los Angeles",
                 address="7257 Melrose Ave, Los Angeles, CA",
                 phone="(323) 417-5300", lat=34.0838, lon=-118.3517, rating=4.3,
                 accepts_medicaid=True, accepts_medicare=True, accepts_private=True, open_24h=False),
            dict(name="Cedars-Sinai Urgent Care", city="Los Angeles",
                 address="8635 W 3rd St, Los Angeles, CA",
                 phone="(310) 423-8100", lat=34.0724, lon=-118.3804, rating=4.5,
                 accepts_medicaid=True, accepts_medicare=True, accepts_private=True, open_24h=False),
            dict(name="GoHealth Urgent Care — Silver Lake", city="Los Angeles",
                 address="3550 W Sunset Blvd, Los Angeles, CA",
                 phone="(323) 741-9000", lat=34.0880, lon=-118.2700, rating=4.1,
                 accepts_medicaid=True, accepts_medicare=True, accepts_private=True, open_24h=False),
            dict(name="Concentra Urgent Care — Downtown LA", city="Los Angeles",
                 address="700 W 3rd St, Los Angeles, CA",
                 phone="(213) 252-7770", lat=34.0505, lon=-118.2594, rating=3.9,
                 accepts_medicaid=False, accepts_medicare=True, accepts_private=True, open_24h=False),
            dict(name="UC San Diego Health Urgent Care", city="San Diego",
                 address="9300 Campus Point Dr, San Diego, CA",
                 phone="(619) 471-0110", lat=32.8801, lon=-117.2340, rating=4.6,
                 accepts_medicaid=True, accepts_medicare=True, accepts_private=True, open_24h=True),
            dict(name="Sharp Rees-Stealy Urgent Care", city="San Diego",
                 address="2001 4th Ave, San Diego, CA",
                 phone="(619) 446-1000", lat=32.7257, lon=-117.1583, rating=4.4,
                 accepts_medicaid=True, accepts_medicare=True, accepts_private=True, open_24h=False),
            dict(name="Carbon Health Urgent Care — Mission", city="San Francisco",
                 address="3400 16th St, San Francisco, CA",
                 phone="(415) 658-5100", lat=37.7650, lon=-122.4292, rating=4.5,
                 accepts_medicaid=True, accepts_medicare=True, accepts_private=True, open_24h=False),
            dict(name="UCSF Urgent Care — Castro", city="San Francisco",
                 address="3700 California St, San Francisco, CA",
                 phone="(415) 353-2808", lat=37.7879, lon=-122.4577, rating=4.7,
                 accepts_medicaid=True, accepts_medicare=True, accepts_private=True, open_24h=False),
            dict(name="Sutter Health Urgent Care — Blossom Hill", city="San Jose",
                 address="5150 Graves Ave, San Jose, CA",
                 phone="(408) 972-6900", lat=37.2574, lon=-121.8688, rating=4.2,
                 accepts_medicaid=True, accepts_medicare=True, accepts_private=True, open_24h=False),
            dict(name="Dignity Health Urgent Care — Elk Grove", city="Sacramento",
                 address="9314 Big Horn Blvd, Elk Grove, CA",
                 phone="(916) 478-2273", lat=38.4008, lon=-121.3920, rating=4.3,
                 accepts_medicaid=True, accepts_medicare=True, accepts_private=True, open_24h=False),
            dict(name="Community Medical Centers Urgent Care", city="Fresno",
                 address="2730 W Herndon Ave, Fresno, CA",
                 phone="(559) 326-5600", lat=36.8349, lon=-119.8067, rating=4.0,
                 accepts_medicaid=True, accepts_medicare=True, accepts_private=True, open_24h=False),
            dict(name="Clinica Sierra Vista Urgent Care", city="Bakersfield",
                 address="1430 Truxtun Ave, Bakersfield, CA",
                 phone="(661) 635-3050", lat=35.3733, lon=-119.0187, rating=3.8,
                 accepts_medicaid=True, accepts_medicare=True, accepts_private=True,
                 accepts_uninsured=True, open_24h=False),
        ]

        with transaction.atomic():
            for f in facilities:
                UrgentCare.objects.get_or_create(name=f['name'], defaults={**f, 'state': 'CA'})

        self.stdout.write(f'  ✓ Seeded {len(facilities)} urgent care facilities')
