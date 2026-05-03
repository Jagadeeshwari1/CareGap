from datetime import date
from django.db import models


class Organization(models.Model):
    """Healthcare organizations — maps to organizations.csv"""
    org_id      = models.CharField(max_length=100, unique=True, db_index=True)
    name        = models.CharField(max_length=255)
    address     = models.CharField(max_length=255, blank=True)
    city        = models.CharField(max_length=100, blank=True)
    state       = models.CharField(max_length=50, blank=True)
    zip_code    = models.CharField(max_length=20, blank=True)
    lat         = models.FloatField(null=True, blank=True)
    lon         = models.FloatField(null=True, blank=True)
    phone       = models.CharField(max_length=30, blank=True)

    def __str__(self):
        return f"{self.name} ({self.city})"

    class Meta:
        ordering = ['name']


class Patient(models.Model):
    """Core patient demographics — maps to patients.csv"""
    COHORT_CHOICES = [
        ('chronic',   'Chronic Disease'),
        ('at_risk',   'At Risk'),
        ('pediatric', 'Pediatric'),
        ('deceased',  'Deceased'),
    ]

    patient_id  = models.CharField(max_length=100, unique=True, db_index=True)
    first       = models.CharField(max_length=100)
    last        = models.CharField(max_length=100)
    birthdate   = models.DateField(null=True, blank=True)
    gender      = models.CharField(max_length=10)
    race        = models.CharField(max_length=50)
    ethnicity   = models.CharField(max_length=50, blank=True)
    city        = models.CharField(max_length=100)
    state       = models.CharField(max_length=50, blank=True)
    zip_code    = models.CharField(max_length=20, blank=True)
    insurance   = models.CharField(max_length=100, blank=True)  # derived from payers.csv
    lat         = models.FloatField(null=True, blank=True)
    lon         = models.FloatField(null=True, blank=True)
    is_deceased = models.BooleanField(default=False, db_index=True)
    cohort      = models.CharField(
        max_length=20,
        choices=COHORT_CHOICES,
        default='chronic',
        db_index=True,
    )

    @property
    def age(self):
        if not self.birthdate:
            return None
        today = date.today()
        bd = self.birthdate if isinstance(self.birthdate, date) else self.birthdate.date()
        return today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))

    def full_name(self):
        return f"{self.first} {self.last}"

    def __str__(self):
        return f"{self.full_name()} ({self.patient_id[:8]}…)"

    class Meta:
        ordering = ['last', 'first']


class Observation(models.Model):
    """Labs and vitals — maps to observations.csv"""
    patient     = models.ForeignKey(Patient, on_delete=models.CASCADE,
                                    related_name='observations', to_field='patient_id')
    date        = models.DateTimeField(null=True, blank=True, db_index=True)
    code        = models.CharField(max_length=50, db_index=True)
    description = models.CharField(max_length=255)
    value       = models.CharField(max_length=100)
    units       = models.CharField(max_length=50, blank=True)

    LOINC_HBA1C = '4548-4'
    LOINC_SBP   = '8480-6'

    class Meta:
        ordering = ['-date']


class Encounter(models.Model):
    """Patient visits — maps to encounters.csv"""
    patient         = models.ForeignKey(Patient, on_delete=models.CASCADE,
                                         related_name='encounters', to_field='patient_id')
    encounter_id    = models.CharField(max_length=100, unique=True)
    start           = models.DateTimeField(null=True, blank=True)
    stop            = models.DateTimeField(null=True, blank=True)
    encounter_class = models.CharField(max_length=100, blank=True)
    description     = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-start']


class Condition(models.Model):
    """Active conditions — maps to conditions.csv"""
    patient     = models.ForeignKey(Patient, on_delete=models.CASCADE,
                                     related_name='conditions', to_field='patient_id')
    start       = models.DateField(null=True, blank=True)
    stop        = models.DateField(null=True, blank=True)
    code        = models.CharField(max_length=50, db_index=True)
    description = models.CharField(max_length=255)

    DIABETES_CODES     = ['44054006', '73211009']
    HYPERTENSION_CODES = ['59621000', '38341003']

    class Meta:
        ordering = ['-start']


class Medication(models.Model):
    """Active medications — maps to medications.csv"""
    patient            = models.ForeignKey(Patient, on_delete=models.CASCADE,
                                            related_name='medications', to_field='patient_id')
    start              = models.DateField(null=True, blank=True)
    stop               = models.DateField(null=True, blank=True)
    code               = models.CharField(max_length=50)
    description        = models.CharField(max_length=255)
    reason_code        = models.CharField(max_length=50, blank=True)
    reason_description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-start']


class UrgentCare(models.Model):
    """
    Simulated urgent care facilities.
    Populated by management command or seeded via seed_urgent_cares.py
    """
    name        = models.CharField(max_length=200)
    city        = models.CharField(max_length=100, db_index=True)
    state       = models.CharField(max_length=50, default='CA')
    address     = models.CharField(max_length=255)
    phone       = models.CharField(max_length=30, blank=True)
    lat         = models.FloatField(null=True, blank=True)
    lon         = models.FloatField(null=True, blank=True)
    accepts_medicaid  = models.BooleanField(default=True)
    accepts_medicare  = models.BooleanField(default=True)
    accepts_private   = models.BooleanField(default=True)
    accepts_uninsured = models.BooleanField(default=False)
    rating      = models.FloatField(default=4.0)
    open_24h    = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} — {self.city}"
