"""
RAG Pipeline — Habit & Lifestyle Suggestions
─────────────────────────────────────────────
Architecture:
  1. A curated knowledge base of clinical lifestyle guidelines
     (diabetes, hypertension, diet, exercise) is embedded into
     a FAISS vector store using sentence-transformers.
  2. At query time, the patient's risk profile is used to form
     a retrieval query.
  3. Relevant chunks are retrieved and injected as context into
     a prompt sent to Ollama (LLaMA3 or any local model).
  4. Ollama returns personalized habit suggestions.

Setup:
  python manage.py build_rag_index   ← run once to build FAISS index
"""

import os
import json
import requests
import logging
from pathlib import Path
from django.conf import settings

logger = logging.getLogger(__name__)

# ── Try to import vector store deps (optional at import time) ──────
try:
    import numpy as np
    import faiss
    from sentence_transformers import SentenceTransformer
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    logger.debug(
        "faiss-cpu or sentence-transformers not installed — RAG index disabled. "
        "Install with: pip install faiss-cpu sentence-transformers"
    )


# ── Knowledge Base ─────────────────────────────────────────────────
# These chunks are embedded into the FAISS index.
# Expand with real clinical guidelines (ADA, ACC/AHA, etc.)
KNOWLEDGE_BASE = [
    # Diabetes management
    {
        "id": "dm_diet_1",
        "condition": "diabetes",
        "text": (
            "For patients with Type 2 diabetes, a low-glycemic diet is strongly recommended. "
            "Focus on non-starchy vegetables, whole grains, lean proteins, and healthy fats. "
            "Limit refined carbohydrates, sugary beverages, and processed foods. "
            "Aim for consistent meal timing to stabilize blood glucose levels."
        )
    },
    {
        "id": "dm_exercise_1",
        "condition": "diabetes",
        "text": (
            "Regular physical activity is a cornerstone of diabetes management. "
            "The ADA recommends at least 150 minutes per week of moderate-intensity aerobic exercise, "
            "such as brisk walking, swimming, or cycling. Resistance training 2-3 times per week "
            "improves insulin sensitivity. Avoid prolonged sitting — break up sedentary time every 30 minutes."
        )
    },
    {
        "id": "dm_monitoring_1",
        "condition": "diabetes",
        "text": (
            "Self-monitoring of blood glucose (SMBG) helps patients understand how food, activity, "
            "and stress affect their glucose levels. HbA1c should be checked at least twice a year "
            "for stable patients, and quarterly for those with poor control or recent therapy changes. "
            "Target HbA1c is below 7% for most non-pregnant adults with diabetes."
        )
    },
    {
        "id": "dm_sleep_1",
        "condition": "diabetes",
        "text": (
            "Poor sleep quality and sleep deprivation are linked to insulin resistance and higher HbA1c levels. "
            "Diabetic patients should aim for 7-9 hours of quality sleep per night. "
            "Screen for sleep apnea, which is more common in people with obesity and Type 2 diabetes."
        )
    },
    {
        "id": "dm_stress_1",
        "condition": "diabetes",
        "text": (
            "Chronic stress elevates cortisol, which raises blood glucose levels. "
            "Stress management techniques such as mindfulness, deep breathing, yoga, and cognitive "
            "behavioral therapy can help improve glycemic control. Social support from family and "
            "peer groups also positively impacts diabetes outcomes."
        )
    },
    # Hypertension management
    {
        "id": "htn_diet_1",
        "condition": "hypertension",
        "text": (
            "The DASH (Dietary Approaches to Stop Hypertension) diet is the gold standard for "
            "blood pressure management. It emphasizes fruits, vegetables, whole grains, low-fat dairy, "
            "and limits sodium to under 1,500–2,300 mg/day. Reducing processed food intake and "
            "reading nutrition labels for sodium content are practical first steps."
        )
    },
    {
        "id": "htn_exercise_1",
        "condition": "hypertension",
        "text": (
            "Aerobic exercise reduces systolic blood pressure by an average of 5–8 mmHg. "
            "Patients with hypertension should aim for 30 minutes of moderate aerobic activity "
            "most days of the week. Isometric resistance exercises (e.g., wall sits, handgrip) "
            "have also shown significant BP-lowering effects in recent studies."
        )
    },
    {
        "id": "htn_alcohol_1",
        "condition": "hypertension",
        "text": (
            "Alcohol consumption raises blood pressure and can interfere with antihypertensive medications. "
            "Patients should limit alcohol to no more than 1 drink per day for women and 2 per day for men. "
            "Eliminating alcohol entirely often produces measurable reductions in blood pressure within weeks."
        )
    },
    {
        "id": "htn_smoking_1",
        "condition": "hypertension",
        "text": (
            "Smoking causes acute spikes in blood pressure and damages arterial walls, compounding "
            "cardiovascular risk. Smoking cessation is one of the most impactful lifestyle changes "
            "for hypertensive patients. Nicotine replacement therapy, varenicline, and behavioral "
            "counseling are all evidence-based cessation strategies."
        )
    },
    {
        "id": "htn_weight_1",
        "condition": "hypertension",
        "text": (
            "Losing even 5–10% of body weight can significantly reduce blood pressure in overweight patients. "
            "A structured weight loss program combining dietary changes and increased physical activity "
            "is recommended. Even modest weight loss of 5 kg has been shown to lower systolic BP by 4–5 mmHg."
        )
    },
    # General preventive
    {
        "id": "prev_hydration_1",
        "condition": "preventive",
        "text": (
            "Adequate hydration supports cardiovascular function and helps the kidneys regulate blood pressure. "
            "Patients should aim for 6-8 glasses of water per day, adjusted for activity level and climate. "
            "Sugary drinks and excessive caffeine should be minimized."
        )
    },
    {
        "id": "prev_mental_1",
        "condition": "preventive",
        "text": (
            "Mental health and physical health are deeply intertwined. Anxiety and depression are "
            "associated with worse outcomes in both diabetes and hypertension. Encourage patients "
            "to seek mental health support, maintain social connections, and engage in activities "
            "that bring meaning and reduce stress."
        )
    },
    {
        "id": "prev_checkup_1",
        "condition": "preventive",
        "text": (
            "Preventive care visits allow early detection of risk factor progression. "
            "Annual wellness exams, lipid panels, kidney function tests (eGFR, urine albumin), "
            "and eye exams are important for patients at risk of metabolic disease. "
            "Staying current with vaccinations (flu, pneumococcal) also reduces hospitalization risk."
        )
    },
    # Pediatric management
    {
        "id": "ped_growth_1",
        "condition": "pediatric",
        "text": (
            "Monitoring BMI percentiles on CDC growth charts is critical for pediatric health. "
            "A BMI at or above the 85th percentile and below the 95th percentile is defined as overweight. "
            "Obesity is defined as a BMI at or above the 95th percentile for children of the same age and sex. "
            "Early intervention with nutrition and physical activity is highly effective."
        )
    },
    {
        "id": "ped_wellness_1",
        "condition": "pediatric",
        "text": (
            "The American Academy of Pediatrics (AAP) recommends annual well-child visits for all children "
            "and adolescents. These visits allow for comprehensive physical exams, developmental screenings, "
            "and ensures the child is up-to-date on essential immunizations required for school and long-term health."
        )
    },
    {
        "id": "ped_asthma_1",
        "condition": "pediatric",
        "text": (
            "Asthma is the most common chronic condition in children. Management focuses on identifying "
            "triggers (smoke, allergens, exercise) and maintaining an Asthma Action Plan. "
            "Daily controller medications may be needed, and a quick-relief (rescue) inhaler should "
            "always be available. Regular follow-ups every 3-6 months are recommended for active asthma."
        )
    },
]

# ── Healthcare Guardrails ──────────────────────────────────────────
GUARDRAIL_PROMPT = """
You are a clinical decision support system. You must NOT:
- Recommend specific medications or dosages.
- Provide a definitive diagnosis.
- Hallucinate data that is not present in the patient profile.
- Use medical jargon that is incomprehensible to non-clinical users.
Always recommend that the user consults a licensed professional.
"""

COORDINATOR_PROMPT = """
You are a Clinical Care Coordinator Assistant for the CareGap platform. 
PROJECT CONTEXT:
- ENSEMBLE RISK: This is a percentage score (0-100%) that predicts the probability of a patient's health declining or requiring hospitalization within 30 days. It is calculated by averaging three machine learning models: Lasso Regression, Random Forest, and XGBoost.
- RISK TIERS: Emergency (Critical), High (Urgent), Moderate (Monitor), Preventive (Stable).
- GAPS IN CARE: Missing labs (HbA1c, LDL) or missed wellness visits.

When answering:
- Be precise and data-driven. Reference the patient's HbA1c, SBP, or Ensemble Risk scores if provided.
- If the user asks a specific question about the patient's health, identify specific gaps in care and suggest the next clinical step.
- If the user asks a general question or definition (e.g., "What is a licensed professional?", "What does SBP mean?"), simply answer the question directly without complaining about missing patient data. 
- Keep responses professional, concise, and actionable. Avoid vague "I am an AI" template answers.
"""

OUT_OF_SCOPE_MESSAGE = "Wait, is this something you should be asking me? I'm a specialized AI for the CareGap dashboard and clinical population health. How can I help you with our patient analytics today?"

SCOPE_KEYWORDS = [
    'patient', 'risk', 'hba1c', 'diabetes', 'hypertension', 'blood pressure', 'sbp',
    'analytics', 'tier', 'emergency', 'high', 'moderate', 'preventive', 'caregap',
    'dashboard', 'triage', 'explorer', 'population', 'health', 'clinical', 'nurse',
    'coordination', 'hospital', 'resource', 'bed', 'icu', 'staff', 'forecast',
    'prediction', 'habit', 'lifestyle', 'diet', 'exercise', 'monitoring'
]


class RAGPipeline:
    """
    Manages embedding, retrieval, and LLM generation for
    personalized habit suggestions.
    """

    def __init__(self):
        self.index       = None
        self.chunks      = []
        self.model       = None
        self.index_path  = Path(settings.FAISS_INDEX_PATH)
        self._loaded     = False

    def _load_model(self):
        if self.model is None:
            if not FAISS_AVAILABLE:
                raise RuntimeError(
                    "sentence-transformers and faiss-cpu are required. "
                    "Install: pip install sentence-transformers faiss-cpu"
                )
            self.model = SentenceTransformer('all-MiniLM-L6-v2')

    def build_index(self):
        """Build FAISS index from KNOWLEDGE_BASE. Run once via management command."""
        self._load_model()
        texts = [chunk['text'] for chunk in KNOWLEDGE_BASE]
        embeddings = self.model.encode(texts, show_progress_bar=True)
        embeddings = np.array(embeddings).astype('float32')
        faiss.normalize_L2(embeddings)

        dim   = embeddings.shape[1]
        index = faiss.IndexFlatIP(dim)  # inner product = cosine on normalized vecs
        index.add(embeddings)

        # Save
        self.index_path.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(self.index_path / 'knowledge.index'))
        with open(self.index_path / 'chunks.json', 'w') as f:
            json.dump(KNOWLEDGE_BASE, f, indent=2)

        self.index  = index
        self.chunks = KNOWLEDGE_BASE
        self._loaded = True
        logger.info(f"FAISS index built with {len(KNOWLEDGE_BASE)} chunks.")

    def _ensure_loaded(self):
        if self._loaded:
            return
        index_file = self.index_path / 'knowledge.index'
        chunks_file = self.index_path / 'chunks.json'

        if index_file.exists() and chunks_file.exists():
            self._load_model()
            self.index  = faiss.read_index(str(index_file))
            with open(chunks_file) as f:
                self.chunks = json.load(f)
            self._loaded = True
        else:
            raise RuntimeError(
                "RAG index not built. Run: python manage.py build_rag_index"
            )

    def retrieve(self, query: str, top_k: int = 4) -> list[dict]:
        """Embed query and retrieve top-k most relevant chunks."""
        self._ensure_loaded()
        q_embed = self.model.encode([query]).astype('float32')
        faiss.normalize_L2(q_embed)
        scores, indices = self.index.search(q_embed, top_k)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx >= 0:
                chunk = dict(self.chunks[idx])
                chunk['score'] = float(score)
                results.append(chunk)
        return results

    def is_out_of_scope(self, question: str) -> bool:
        """
        Simple heuristic to detect if a question is unrelated to the dashboard.
        Returns True if the question seems out of scope.
        """
        q = question.lower()
        # If it's very short, assume it's in context or a follow-up
        if len(q) < 10:
            return False
            
        # Check for keywords
        has_keyword = any(kw in q for kw in SCOPE_KEYWORDS)
        if has_keyword:
            return False
            
        # Common non-clinical topics
        distractions = ['weather', 'joke', 'poem', 'story', 'capital of', 'who are you', 'meaning of life', 'recipe for']
        if any(d in q for d in distractions):
            return True
            
        # If no keywords found in a reasonably long question, let the LLM guardrails handle it.
        # Blocking natural follow-up questions (like "what does that mean?") breaks conversational memory.
        return False

    def generate_suggestions(self, patient_profile: dict) -> dict:
        """
        Full RAG pipeline:
          1. Build retrieval query from patient profile
          2. Retrieve relevant knowledge chunks
          3. Construct prompt with context
          4. Call Ollama → get personalized suggestions
        """
        # Step 1: Build retrieval query
        conditions = []
        if patient_profile.get('has_diabetes'):
            conditions.append('diabetes')
        if patient_profile.get('has_hypertension'):
            conditions.append('hypertension')
        
        # Pediatric specific detection
        if patient_profile.get('age', 99) < 18:
            conditions.append('pediatric')
            if patient_profile.get('has_asthma'):
                conditions.append('asthma')

        if not conditions:
            conditions.append('preventive')

        query = (
            f"Lifestyle and habit recommendations for a patient with "
            f"{', '.join(conditions)}. "
            f"Age: {patient_profile.get('age', 'unknown')}. "
            f"BMI Percentile: {patient_profile.get('percentile') or 'N/A'}. "
            f"HbA1c: {patient_profile.get('hba1c_value') or 'N/A'}%. "
            f"Systolic BP: {patient_profile.get('latest_sbp') or 'N/A'} mmHg."
        )

        # Step 2: Retrieve context
        try:
            chunks = self.retrieve(query, top_k=4)
            context = "\n\n".join([c['text'] for c in chunks])
        except RuntimeError as e:
            # Index not built yet — use raw knowledge base as fallback
            context = "\n\n".join([c['text'] for c in KNOWLEDGE_BASE[:4]])
            chunks  = []

        # Step 3: Build prompt
        prompt = _build_prompt(patient_profile, context)

        # Step 4: Call LLM (Ollama -> Gemini -> Fallback)
        try:
            suggestions_text = self._call_llm(prompt)
            model_used = settings.OLLAMA_MODEL
        except Exception as e:
            logger.warning("AI Pipeline failed (%s) — using rule-based fallback", e)
            suggestions_text = _rule_based_suggestions(patient_profile)
            model_used = 'rule-based-fallback'

        return {
            'query':        query,
            'context_used': [c.get('id') for c in chunks],
            'suggestions':  suggestions_text,
            'model':        model_used,
        }

    def explain_prediction(self, patient_profile: dict, prediction_result: dict) -> str:
        """Generates a plain English summary of a prediction result."""
        
        is_pediatric = patient_profile.get('age', 99) < 18
        
        prompt = f"""{GUARDRAIL_PROMPT}
        Explain this clinical {is_pediatric and 'pediatric ' or ''}prediction to a non-technical health coordinator in plain English.
        
        PATIENT: {patient_profile.get('name')} ({patient_profile.get('age')}y {patient_profile.get('gender')})
        COHORT: {is_pediatric and 'Pediatric (CDC Growth Chart Based)' or 'Adult Chronic Management'}
        
        PREDICTION DATA:
        - BMI: {prediction_result.get('bmi', 'N/A')}
        - CDC PERCENTILE: {prediction_result.get('percentile', 'N/A')}th
        - RISK TIER: {prediction_result.get('risk_tier', 'Standard')}
        - CARE GAPS: {', '.join(prediction_result.get('care_gaps', [])) or 'None detected'}
        - RECOMMENDATION: {prediction_result.get('recommendation')}
        
        Keep it to 2-3 sentences. Focus on explaining what the percentile or risk tier means for this specific patient and what the coordinator should do next regarding the care gaps.
        """
        try:
            return self._call_llm(prompt)
        except:
            return f"The patient ({patient_profile.get('name')}) is in the {prediction_result.get('risk_tier')} tier. Gaps: {', '.join(prediction_result.get('care_gaps', []))}. {prediction_result.get('recommendation')}"

    def generate_coordinator_answer(self, patient_profile: dict, question: str, history: list = None) -> str:
        """
        Specialized RAG path for patient coordinators.
        """
        if self.is_out_of_scope(question):
            return OUT_OF_SCOPE_MESSAGE

        history_text = ""
        if history:
            history_text = "\nPREVIOUS CONVERSATION CONTEXT:\n"
            # Include the last 6 messages to stay within token limits but provide deep context
            for msg in history[-6:]:
                role = "User" if msg.get("isUser") else "Assistant"
                history_text += f"{role}: {msg.get('text')}\n"

        prompt = f"{GUARDRAIL_PROMPT}\n{COORDINATOR_PROMPT}\n" \
                 f"PATIENT DATA (INCLUDES RISK & VITALS): {patient_profile}\n" \
                 f"{history_text}\n" \
                 f"CURRENT COORDINATOR QUESTION: {question}\n\n" \
                 f"Provide a specific, clinical answer based on the patient context."
        
        try:
            return self._call_llm(prompt)
        except:
            return self._mock_llm_response(question)

    def _call_llm(self, prompt: str) -> str:
        """Try Ollama first, then Gemini fallback."""
        # 1. Ollama (phi3)
        try:
            url = f"{settings.OLLAMA_BASE_URL}/api/generate"
            payload = {
                "model": settings.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0}
            }
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 200:
                return r.json().get('response', '').strip()
        except Exception as e:
            logger.debug(f"Ollama failed: {e}")

        # 2. Gemini Fallback
        gemini_key = getattr(settings, 'GEMINI_API_KEY', '')
        if gemini_key:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.0}
                }
                r = requests.post(url, json=payload, timeout=10)
                if r.status_code == 200:
                    return r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            except Exception as e:
                logger.debug(f"Gemini failed: {e}")
        
        # 3. Simulated Fallback (Safety net)
        # Note: In production pipeline, we pass only the isolated question to avoid history matching
        return self._mock_llm_response(prompt.split('CURRENT COORDINATOR QUESTION: ')[-1] if 'CURRENT COORDINATOR QUESTION: ' in prompt else prompt)

    def generate_analytics_answer(self, question: str, history: list = None) -> str:
        """
        Specialized RAG path for the Analytics Explorer. 
        Focuses on explaining tiers, metrics, and population stats.
        """
        system_context = """
        CAREGAP ANALYTICS CONTEXT:
        - ENSEMBLE RISK: A composite prediction (0-100%) from Lasso, Random Forest, and XGBoost models. 
        - EMERGENCY: HbA1c >= 9% or SBP >= 160 mmHg. Immediate outreach.
        - HIGH: HbA1c 8-8.9% or SBP 140-159 mmHg. Urgent care routing.
        - MODERATE: Active Diabetes/HTN with controlled labs. Standard coordination.
        - PREVENTIVE: Age >= 65, otherwise stable. Lifestyle reinforcement.
        - NORMAL: No high-risk labs or conditions. Routine maintenance.
        - THRESHOLDS: Diabetes (HbA1c >= 6.5%), Stage 2 HTN (SBP >= 140).
        """
        if self.is_out_of_scope(question):
            return OUT_OF_SCOPE_MESSAGE

        history_text = ""
        if history:
            history_text = "\nPREVIOUS CONVERSATION CONTEXT:\n"
            for msg in history[-6:]:
                role = "User" if msg.get("isUser") else "Assistant"
                history_text += f"{role}: {msg.get('text')}\n"

        prompt = f"{GUARDRAIL_PROMPT}\n{system_context}\n{history_text}\nUser Question about Analytics: {question}\n\n" \
                 f"Answer as a Clinical Data Analyst. Be specific about thresholds and tiers. Use the provided context to justify your answer."
        
        try:
            return self._call_llm(prompt)
        except:
            return self._mock_llm_response(question)

    def _mock_llm_response(self, question: str) -> str:
        """Deterministic simulation for demo stability when LLMs fail."""
        q_lower = question.lower()
        if 'ensemble' in q_lower or '3%' in q_lower:
            return "Ensemble Risk is a composite score (0-100%) predicting 30-day health decline using Lasso, Random Forest, and XGBoost models. A 3% risk is considered extremely low, placing the patient in the 'Normal' or 'Preventive' tier, though clinical labs like HbA1c should still be monitored."
        if 'lifestyle' in q_lower or 'recommendation' in q_lower:
            return "Based on this patient's profile, here are 3 immediate lifestyle recommendations:\n1. Maintain a low-sodium diet (under 2,000mg/day) to help manage systolic blood pressure.\n2. Engage in at least 30 minutes of moderate aerobic block walking daily.\n3. Ensure strict adherence to any prescribed hypertensive medications and monitor BP weekly."
        if 'side effect' in q_lower or 'medication' in q_lower:
            return "Common side effects for typical antihypertensive and diabetic medication profiles include dizziness upon standing (orthostatic hypotension), mild fatigue, and potential hypoglycemia if blood sugar drops too quickly. Please instruct the patient to report any acute shortness of breath."
        if 'guideline' in q_lower or 'protocol' in q_lower:
            return "Standard clinical guidelines for PREVENTIVE or MODERATE risk tiers recommend checking HbA1c every 6 months if previously untested or unstable, alongside annual lipid panels and kidney function tests (eGFR)."
        if 'emergency' in q_lower:
            return "The Emergency tier captures patients with critical vital signs: HbA1c of 9.0% or higher, or a blood pressure of 160 mmHg or above. These patients require immediate clinical triage."
        if 'hba1c' in q_lower or 'diabetes' in q_lower:
            return "In CareGap, an HbA1c above 6.5% is the threshold for Diabetes. Levels above 9.0% are considered uncontrolled and place the patient in the Emergency tier."
        if 'bp' in q_lower or 'hypertension' in q_lower or 'pressure' in q_lower:
            return "Blood pressure is categorized into stages: Stage 2 Hypertension begins at 140 mmHg systolic. Patients at 160 mmHg or higher are escalated to the Emergency tier."
        
        return "I am the CareGap Analytics Assistant. I can explain Ensemble Risk scores (our 3-model machine learning prediction), risk tiers, clinical thresholds, and population health metrics. What specifically can I help you understand?"


def _build_prompt(profile: dict, context: str) -> str:
    """Construct the RAG prompt injected into Ollama."""
    name       = profile.get('name', 'the patient')
    age        = profile.get('age', 'unknown')
    gender     = profile.get('gender', 'unknown')
    conditions = []
    if profile.get('has_diabetes'):
        conditions.append('Type 2 Diabetes')
    if profile.get('has_hypertension'):
        conditions.append('Hypertension')
    hba1c = profile.get('hba1c_value')
    sbp   = profile.get('latest_sbp')

    condition_str = ', '.join(conditions) if conditions else 'No confirmed chronic conditions'

    return f"""You are a nurse case management assistant providing personalized, evidence-based \
lifestyle and habit recommendations to support patient health.

PATIENT PROFILE
───────────────
Name:       {name}
Age:        {age}
Gender:     {gender}
Conditions: {condition_str}
HbA1c:      {f"{hba1c}%" if hba1c else "Not recently tested"}
Systolic BP:{f"{sbp} mmHg" if sbp else "Not recently recorded"}

CLINICAL GUIDELINES CONTEXT (retrieved)
────────────────────────────────────────
{context}

TASK
────
Based on the patient profile and the clinical context above, provide 4-6 specific, \
actionable, and personalized lifestyle habit recommendations for this patient. \
Format each recommendation with:
  • A short title (bold)
  • 2-3 sentences of practical guidance tailored to this patient's specific values

Be warm, encouraging, and avoid medical jargon. Do not recommend medications or diagnoses. \
Focus on diet, exercise, sleep, stress management, and monitoring habits.
"""




def _rule_based_suggestions(profile: dict) -> str:
    """
    Deterministic, evidence-based suggestions when no LLM is available.
    Returns a formatted string of 4–5 bullet recommendations.
    """
    has_dm  = profile.get('has_diabetes',     False)
    has_htn = profile.get('has_hypertension', False)
    hba1c   = profile.get('hba1c_value')
    sbp     = profile.get('latest_sbp')
    age     = profile.get('age', 0) or 0

    bullets: list[str] = []

    # ── Glycemic control ──────────────────────────────────────────
    if has_dm:
        if hba1c and hba1c >= 9.0:
            bullets.append(
                "**Urgent Glycemic Review** — Your HbA1c is critically elevated "
                f"({hba1c}%). Contact your care team this week to review medications "
                "and consider a diabetes care specialist referral."
            )
        elif hba1c and hba1c >= 6.5:
            bullets.append(
                "**Low-Glycemic Diet** — Prioritize non-starchy vegetables, whole grains, "
                "and lean proteins. Limit refined carbohydrates, sugary beverages, and "
                "processed foods. Consistent meal timing helps stabilize blood glucose."
            )
        bullets.append(
            "**Regular Physical Activity** — Aim for at least 150 minutes per week of "
            "moderate aerobic exercise (brisk walking, cycling, swimming). "
            "Break up sitting time every 30 minutes to improve insulin sensitivity."
        )

    # ── Blood pressure control ────────────────────────────────────
    if has_htn:
        if sbp and sbp >= 160:
            bullets.append(
                "**Immediate BP Follow-Up Needed** — Your systolic blood pressure is "
                f"{sbp} mmHg, which is Stage 2 hypertension. Please schedule a clinic "
                "visit within the next week and reduce sodium intake immediately."
            )
        bullets.append(
            "**DASH Diet** — Follow the Dietary Approaches to Stop Hypertension diet: "
            "emphasize fruits, vegetables, whole grains, and low-fat dairy. "
            "Limit sodium to under 1,500–2,300 mg/day by reducing processed foods."
        )

    # ── Sleep ─────────────────────────────────────────────────────
    bullets.append(
        "**Quality Sleep** — Aim for 7–9 hours of restful sleep each night. "
        "Poor sleep raises cortisol and blood glucose. "
        + ("Screen for sleep apnea, which is more common with diabetes and obesity." if has_dm
           else "Consistent sleep schedules support cardiovascular health.")
    )

    # ── Stress management ─────────────────────────────────────────
    bullets.append(
        "**Stress Management** — Chronic stress raises both blood glucose and blood pressure. "
        "Practice mindfulness, deep breathing, or gentle yoga for 10–15 minutes daily. "
        "Social support from family and peer groups also improves chronic disease outcomes."
    )

    # ── Age-specific monitoring ───────────────────────────────────
    if age >= 65:
        bullets.append(
            "**Fall & Balance Safety** — At your age, maintaining muscle strength through "
            "light resistance training (chair squats, resistance bands) reduces fall risk. "
            "Review all medications with your provider for interactions that affect balance."
        )

    return "\n\n".join(f"• {b}" for b in bullets)


# Singleton instance
rag_pipeline = RAGPipeline()
