import os
import pickle
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from patients.views import _get_triage_payload
from patients.forecaster import forecast_resources

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Pre-computes the hospital resource forecast and saves it to a pickle file for instant dashboard loading.'

    def handle(self, *args, **options):
        self.stdout.write("Starting resource forecast pre-computation...")
        
        try:
            # 1. Clear existing cache to ensure fresh re-computation
            from django.core.cache import cache
            cache.clear()
            
            # 2. Extract triage payload (this triggers the scoring for thousands of chronic patients)
            start_time = timezone.now()
            triage_payload = _get_triage_payload()
            risk_breakdown = triage_payload.get('risk_breakdown', {})
            
            # 3. Generate resources based on the newly expanded breakdown
            result = forecast_resources(risk_breakdown)
            
            # 4. Save to Pickle Files
            data_dir = os.path.join('patients', 'data')
            if not os.path.exists(data_dir):
                os.makedirs(data_dir)
                
            # Save Forecast Cache
            forecast_path = os.path.join(data_dir, 'forecast_cache.pkl')
            with open(forecast_path, 'wb') as f:
                pickle.dump(result, f)
            
            # Save Triage Cache
            triage_path = os.path.join(data_dir, 'triage_cache.pkl')
            with open(triage_path, 'wb') as f:
                pickle.dump(triage_payload, f)
                
            elapsed = (timezone.now() - start_time).total_seconds()
            self.stdout.write(self.style.SUCCESS(
                f"Successfully pre-computed forecast and triage lists in {elapsed:.1f}s. "
                f"Saved to {data_dir}"
            ))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Pre-computation failed: {str(e)}"))
            logger.exception("Forecast pre-computation failed")
