# CareGap Analytics : Patient Risk Management System

---

## Project Overview

CareGap is a full-stack healthcare analytics dashboard built
for nurse case managers managing patients with hypertension
and Type 2 diabetes. It uses synthetic EHR data from Synthea
(California, 30,000 patients) to identify care gaps, calculate
risk scores, and surface actionable clinical insights.

---

## Architecture

```
caregap/
├── caregap/               # Django project config
│   ├── settings.py        # Set SYNTHEA_DATA_DIR here
│   ├── urls.py
│   └── wsgi.py
├── patients/              # Core patient app
│   ├── models.py          # Patient, Observation, Encounter,
│   │                      # Condition, Medication, Organization
│   ├── risk_engine.py     # Risk scoring: EMERGENCY/HIGH/MODERATE/PREVENTIVE/NORMAL
│   ├── urgent_care_matcher.py  # Clinic matching logic
│   ├── views.py           # REST API endpoints
│   ├── serializers.py
│   ├── urls.py
│   └── management/commands/
│       ├── import_synthea.py   # CSV → SQLite importer
│       ├── mark_deceased.py    # Flags inactive patients
│       └── build_rag_index.py  # FAISS index builder
├── rag/
│   └── pipeline.py        # RAG logic (FAISS + HF Inference API)
├── templates/
│   └── dashboard.html     # Full SPA frontend
├── requirements.txt
└── manage.py
```

---

## Patient Cohorts

The system imports all 33,990 Synthea patients split into
4 cohorts:

| Cohort | Description | Count |
|--------|-------------|-------|
| chronic | Adults 18-110 with HTN or T2D | ~6,267 |
| at_risk | Adults 18-110 without chronic disease | ~16,776 |
| pediatric | Patients under 18 | ~6,957 |
| deceased | Patients with death date | ~3,990 |

---

## Risk Tier Logic

| Tier | Score | Action |
|------|-------|--------|
| EMERGENCY| Crit. Vitals| IMMEDIATE. Dispatch to ER / critical care |
| HIGH     | ≥ 60  | Urgent Care outreach within 24-48 hours |
| MODERATE | ≥ 30  | Schedule follow-up within 30 days |
| PREVENTIVE| ≥ 10  | Provide personalized lifestyle guidance via RAG |
| NORMAL   | < 10  | Routine monitoring |

---

## Care Gap Rules

| Care Gap | Rule |
|----------|------|
| HbA1c Overdue | No HbA1c test in > 365 days |
| BP Follow-up Missing | SBP ≥ 160 with no encounter in 30 days |
| Missing Medication | No active medication on record |

---

## Predictive Analytics & ML Transparency

CareGap features an advanced 3-model ensemble engine (Lasso Regression, Random Forest, XGBoost) to generate clinical risk scores and 6-month trajectory forecasts. 

To bridge the gap between data science and clinical actionability, the dashboard prioritizes **ML Interpretability**:
- **Current Baseline Vitals Tracking**: Instantly displays the patient's current SBP and HbA1c alongside the raw algorithmic scores from Lasso, RF, and XGBoost.
- **Model Impact Matrix**: A horizontally scaled, clinician-friendly data table that demystifies model behaviors and methodologies in layman's terms.
- **"What-If" Clinical Simulations**: Simulates targeted clinical interventions (e.g., "What if Blood Pressure is Fixed to 120 mmHg?") to dynamically recalculate the *remaining* risk driven purely by uncontrolled factors.
- **AI Explainability**: A visual SHAP (SHapley Additive exPlanations) breakdown highlighting precisely which vitals or historical events are increasing or lowering the patient's risk profile.

---

## Setup Instructions

### 1. Clone the repository
```bash
git clone https://github.com/Mallika-434/CAREGAP_1.git
cd CAREGAP_1
```

### 2. Create virtual environment
```bash
python -m venv venv
venv\Scripts\activate   # Windows
source venv/bin/activate  # Mac/Linux
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Add Synthea CSV files
Download the Synthea California dataset and place in:
```
data/synthea_ca_seed43438_p30000/
```

Files needed:
- patients.csv
- conditions.csv
- observations.csv
- encounters.csv
- medications.csv
- organizations.csv
- payers.csv
- payer_transitions.csv

### 5. Update settings.py
Edit `caregap/settings.py`:
```python
SYNTHEA_DATA_DIR = 'data/synthea_ca_seed43438_p30000'
```

### 6. Run migrations
```bash
python manage.py migrate
```

### 7. Import all 33,990 patients
```bash
python manage.py import_synthea --clear
```

> This will take 45–90 minutes for the full dataset.

### 8. Run the server
```bash
python manage.py runserver
```

Open: http://localhost:8000

---

## API Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| GET | /api/patients/search/ | Patient search with cohort filter |
| GET | /api/patients/stats/basic/ | Fast stat cards (instant, cached) |
| GET | /api/patients/stats/ | Full population analytics (charts) |
| GET | /api/patients/triage/ | High risk triage list |
| GET | /api/patients/\<id\>/ | Full patient profile |
| GET | /api/patients/\<id\>/risk/ | Risk assessment |
| GET | /api/patients/\<id\>/urgent-care/ | Clinic recommendations |

---

## Dashboard Pages

1. **Analytics Explorer** — Population filtering & predictive insights
2. **Population Dashboard** — Population-level charts and metrics
3. **Patient Search** — Searchable directory of all 30,000 patients
4. **Action Required** — Emergency and urgent care triage queue

---

## Dataset

Synthea California synthetic dataset:
- Seed: 43438
- Total patients: 33,990
- Source: https://github.com/synthetichealth/synthea
- Note: Dataset not included in repo due to size (~5 GB compressed).
  Contact the team for access or generate using Synthea.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.14 + Django 6.0 + Django REST Framework |
| Frontend | Vanilla JS SPA + Chart.js |
| Database | SQLite (development) |
| Caching | Django file-based cache |
| RAG | FAISS + Ollama API (Local) + Gemini 1.5 Fallback |
| Predictions | scikit-learn + pandas |
| Deployment | Hugging Face Spaces + Docker |

---

## Example AI Chatbot Questions (Predictive Modeling)
*You can copy and paste these into the floating "Explain This AI Result" global chat interface:*

1. **Model Explainability:** "Can you explain why the XGBoost model assigned an 85% risk score while the Random Forest score is only 40%?"
2. **Feature Importance (SHAP):** "Which clinical vitals are the strongest predictors driving this patient's current risk assessment?"
3. **Clinical Trajectory:** "Based on the 6-month prediction timeline, what is the expected HbA1c trajectory if no interventions are made?"
4. **"What-If" Simulations:** "If we lower the patient's blood pressure to 120 mmHg, how much will their remaining ensemble risk score decrease?"
5. **Ensemble Logic:** "How does the ensemble model weight the Lasso Regression, Random Forest, and XGBoost outputs for this specific patient profile?"
6. **Risk Drivers:** "Are there any specific recent encounters or changes in medication that spiked the patient's risk profile in the predictive model?"
7. **Care Gap Impact:** "How heavily does the overdue HbA1c test impact the current predictive risk score, and what happens to the score if the test is completed tomorrow?"
8. **Clinical Guidelines:** "Does the model's prediction align with the standard clinical guidelines for a patient with a baseline HbA1c of 8.5%?"
9. **Algorithm Methodology:** "Why does the Lasso Regression model saturate at 100% on high-risk baselines, while the Random Forest evaluates more conservatively?"
10. **Intervention Prioritization:** "According to the predictive drivers, which single clinical intervention would yield the highest reduction in the patient's immediate health risk?"
