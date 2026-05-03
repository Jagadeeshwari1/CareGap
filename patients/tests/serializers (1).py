from rest_framework import serializers
from .models import Patient, Observation, Encounter, Condition, Medication, UrgentCare


class ObservationSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Observation
        fields = ['id', 'date', 'code', 'description', 'value', 'units']


class EncounterSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Encounter
        fields = ['id', 'encounter_id', 'start', 'stop', 'encounter_class', 'description']


class ConditionSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Condition
        fields = ['id', 'start', 'stop', 'code', 'description']


class MedicationSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Medication
        fields = ['id', 'start', 'stop', 'code', 'description', 'reason_description']


class PatientListSerializer(serializers.ModelSerializer):
    """Lightweight — used for search results list"""
    full_name = serializers.SerializerMethodField()

    class Meta:
        model  = Patient
        fields = [
            'id', 'patient_id', 'full_name', 'first', 'last',
            'age', 'gender', 'race', 'city', 'insurance', 'cohort',
        ]

    def get_full_name(self, obj):
        return obj.full_name()


class PatientDetailSerializer(serializers.ModelSerializer):
    """Full detail — includes all related data"""
    full_name    = serializers.SerializerMethodField()
    observations = ObservationSerializer(many=True, read_only=True)
    encounters   = EncounterSerializer(many=True, read_only=True)
    conditions   = ConditionSerializer(many=True, read_only=True)
    medications  = MedicationSerializer(many=True, read_only=True)

    class Meta:
        model  = Patient
        fields = [
            'id', 'patient_id', 'full_name', 'first', 'last',
            'birthdate', 'age', 'gender', 'race', 'ethnicity',
            'city', 'state', 'zip_code', 'insurance',
            'lat', 'lon',
            'is_deceased', 'cohort',
            'observations', 'encounters', 'conditions', 'medications',
        ]

    def get_full_name(self, obj):
        return obj.full_name()


class UrgentCareSerializer(serializers.ModelSerializer):
    class Meta:
        model  = UrgentCare
        fields = '__all__'
