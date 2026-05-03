from django.test import TestCase
from rest_framework.test import APIClient


class ResourceForecastApiTests(TestCase):
    """Smoke tests to ensure the new triage / forecast endpoints stay healthy."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = APIClient()

    def _assert_patient_row(self, row):
        self.assertIn('patient_id', row)
        self.assertIn('probability', row)
        self.assertIn('model_available', row)
        self.assertIsInstance(row.get('model_available'), bool)
        prob = row.get('probability')
        if prob is not None:
            self.assertGreaterEqual(prob, 0.0)
            self.assertLessEqual(prob, 1.0)

    def test_triage_endpoint_returns_ml_scores(self):
        response = self.client.get('/api/patients/triage/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('emergency_patients', data)
        self.assertIn('urgent_patients', data)
        self.assertIn('high_risk_volume', data)
        self.assertIsInstance(data['high_risk_volume'], int)

        for patient in data.get('emergency_patients', []):
            self._assert_patient_row(patient)
        for patient in data.get('urgent_patients', []):
            self._assert_patient_row(patient)

    def test_resource_forecast_endpoint_shapes_output(self):
        response = self.client.get('/api/patients/resources/forecast/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('resources', data)
        self.assertIn('high_risk_volume', data)
        self.assertIn('period_days', data)
        self.assertIn('generated_at', data)
        resources = data['resources']
        for key in ('beds', 'icu', 'nurses'):
            self.assertIn(key, resources)
            self.assertIn('count', resources[key])
