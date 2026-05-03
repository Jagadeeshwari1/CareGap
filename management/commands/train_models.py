"""
Management command: train_models
────────────────────────────────
Trains a LogisticRegression risk-progression model on all chronic patients
and saves it to models/risk_predictor.pkl.

Usage:
    python manage.py train_models

Re-run whenever data is refreshed (after import_synthea).
Training on 6 267 chronic patients typically takes < 30 s.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Train ML risk-progression model and save to models/risk_predictor.pkl'

    def handle(self, *args, **options):
        import numpy as np
        import joblib
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import (accuracy_score, precision_score,
                                     recall_score, f1_score,
                                     classification_report)
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import Pipeline

        from patients.models import Patient
        from patients.risk_engine import assess_risk
        from patients.ml_models import extract_features, MODELS_DIR, RISK_MODEL_PATH

        # ── 1. Load patients ──────────────────────────────────────────
        self.stdout.write('Loading chronic patients…')
        patients = list(
            Patient.objects.filter(cohort='chronic')
                           .prefetch_related('observations', 'conditions')
        )
        self.stdout.write(f'  {len(patients):,} patients loaded')

        # ── 2. Extract features + labels ─────────────────────────────
        self.stdout.write('Extracting features…')
        X_rows, y_rows = [], []
        skipped = 0

        for i, patient in enumerate(patients):
            if i > 0 and i % 1000 == 0:
                self.stdout.write(f'  {i:,}/{len(patients):,}…')

            obs   = list(patient.observations.all())
            conds = list(patient.conditions.all())

            try:
                result           = assess_risk(patient, obs, conds)
                label            = 1 if result.score >= 60 else 0
                _, feature_arr   = extract_features(patient, obs, conds)
                X_rows.append(feature_arr)
                y_rows.append(label)
            except Exception as exc:
                skipped += 1
                if skipped <= 5:
                    self.stdout.write(
                        self.style.WARNING(f'  skip {patient.patient_id[:8]}: {exc}')
                    )

        if not X_rows:
            self.stdout.write(self.style.ERROR('No samples — aborting.'))
            return

        X = np.array(X_rows)
        y = np.array(y_rows)

        pos = int(y.sum())
        neg = len(y) - pos
        self.stdout.write(
            f'  Samples: {len(y):,}  (HIGH={pos:,} {100*pos/len(y):.1f}%,'
            f'  LOW={neg:,} {100*neg/len(y):.1f}%)'
        )
        if skipped:
            self.stdout.write(self.style.WARNING(f'  Skipped: {skipped}'))

        # ── 3. Train / test split ─────────────────────────────────────
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y,
        )
        self.stdout.write(
            f'  Train: {len(X_train):,}  Test: {len(X_test):,}'
        )

        # ── 4. Train and Evaluate Models ─────────────────────────────
        import xgboost as xgb
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.linear_model import LogisticRegression
        import pandas as pd
        from pathlib import Path
        import os

        # Save CSV to output directory
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        
        # Feature names based on ml_models.FEATURE_NAMES + target
        from patients.ml_models import FEATURE_NAMES
        df = pd.DataFrame(X, columns=FEATURE_NAMES)
        df['target_high_risk'] = y
        output_csv_path = output_dir / "patient_risk_dataset.csv"
        df.to_csv(output_csv_path, index=False)
        self.stdout.write(f'Saved dataset to {output_csv_path}')

        models_to_train = {
            'Lasso Logistic Regression': Pipeline([
                ('scaler', StandardScaler()),
                ('clf', LogisticRegression(penalty='l1', solver='liblinear', class_weight='balanced', random_state=42, max_iter=1000))
            ]),
            'Random Forest': Pipeline([
                # RF doesn't strictly need scaling, but Pipeline is fine
                ('scaler', StandardScaler()),
                ('clf', RandomForestClassifier(n_estimators=100, class_weight='balanced', random_state=42))
            ]),
            'XGBoost': Pipeline([
                ('scaler', StandardScaler()),
                ('clf', xgb.XGBClassifier(n_estimators=100, scale_pos_weight=max(1.0, float(neg/pos)) if pos > 0 else 1.0, random_state=42, use_label_encoder=False, eval_metric='logloss'))
            ])
        }

        MODELS_DIR.mkdir(parents=True, exist_ok=True)

        for name, pipeline in models_to_train.items():
            self.stdout.write(f'\nTraining {name}…')
            pipeline.fit(X_train, y_train)
            
            y_pred = pipeline.predict(X_test)
            acc  = accuracy_score(y_test, y_pred)
            prec = precision_score(y_test, y_pred, zero_division=0)
            rec  = recall_score(y_test, y_pred, zero_division=0)
            f1   = f1_score(y_test, y_pred, zero_division=0)

            self.stdout.write(self.style.SUCCESS(f'{name} Test Metrics:'))
            self.stdout.write(f'  Accuracy : {acc:.3f}')
            self.stdout.write(f'  Precision: {prec:.3f}')
            self.stdout.write(f'  Recall   : {rec:.3f}')
            self.stdout.write(f'  F1 Score : {f1:.3f}')

            # Save the model
            filename = name.lower().replace(' ', '_') + '.pkl'
            model_path = MODELS_DIR / filename
            joblib.dump(pipeline, model_path)
            self.stdout.write(self.style.SUCCESS(f'Model saved → {model_path}'))

            # For backward compatibility with the baseline, save the first one as risk_predictor.pkl
            # Or save the best one as risk_predictor.pkl. Here we just set Lasso as the default risk_predictor because it's standard and interpretable
            if name == 'Lasso Logistic Regression':
                joblib.dump(pipeline, RISK_MODEL_PATH)

        self.stdout.write('\nPredictive models trained successfully.')
