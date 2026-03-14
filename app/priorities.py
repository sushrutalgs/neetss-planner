"""
NEET SS Surgical Group — Granular Syllabus Tree with Textbook Mapping.

Textbook editions:
  - Sabiston Textbook of Surgery, 22nd Edition (2025)
  - Schwartz's Principles of Surgery, 11th Edition
  - Bailey & Love's Short Practice of Surgery, 28th Edition

P1 (48%) — High-yield surgical subspecialty topics
P2 (36%) — Moderate-yield topics
P3 (16%) — Support / GIT topics
"""
from __future__ import annotations
from typing import Dict, List, Any

# --------------------------------------------------------------------------- #
#                          SYLLABUS TREE
# --------------------------------------------------------------------------- #

SYLLABUS_TREE: Dict[str, Dict[str, Any]] = {

    # ======================== P1 HIGH YIELD (48%) ========================

    "Breast": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Breast anatomy, development & ANDI", "ref": "Sabiston Ch.68, Schwartz Ch.17, Bailey Ch.58"},
            {"name": "Benign breast disease & nipple discharge", "ref": "Sabiston Ch.68, Schwartz Ch.17, Bailey Ch.58"},
            {"name": "Fibroadenoma & phyllodes tumor", "ref": "Sabiston Ch.68, Schwartz Ch.17, Bailey Ch.58"},
            {"name": "Breast carcinoma — staging, TNM & management", "ref": "Sabiston Ch.68, Schwartz Ch.17, Bailey Ch.58"},
            {"name": "Breast conservation surgery & mastectomy", "ref": "Sabiston Ch.68, Schwartz Ch.17, Bailey Ch.58"},
            {"name": "Breast reconstruction & oncoplastic surgery", "ref": "Sabiston Ch.68-69, Schwartz Ch.45, Bailey Ch.47"},
            {"name": "Male breast disease & Paget's disease", "ref": "Sabiston Ch.68, Schwartz Ch.17"},
            {"name": "Sentinel lymph node biopsy & axillary management", "ref": "Sabiston Ch.68, Schwartz Ch.17, Bailey Ch.58"},
        ],
    },

    "Thyroid & Parathyroid": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Thyroid anatomy & embryology", "ref": "Sabiston Ch.73, Schwartz Ch.38, Bailey Ch.55"},
            {"name": "Thyroid function tests & investigations", "ref": "Sabiston Ch.73, Bailey Ch.55"},
            {"name": "Thyroid nodule — FNAC & Bethesda classification", "ref": "Sabiston Ch.73, Schwartz Ch.38, Bailey Ch.55"},
            {"name": "Thyroid malignancies (PTC, FTC, MTC, ATC)", "ref": "Sabiston Ch.73, Schwartz Ch.38, Bailey Ch.55"},
            {"name": "Thyroidectomy — technique & complications", "ref": "Sabiston Ch.73, Schwartz Ch.38, Bailey Ch.55"},
            {"name": "Graves' disease & thyrotoxicosis", "ref": "Sabiston Ch.73, Schwartz Ch.38, Bailey Ch.55"},
            {"name": "Parathyroid adenoma & hyperparathyroidism", "ref": "Sabiston Ch.74, Schwartz Ch.38, Bailey Ch.56"},
            {"name": "Parathyroid surgery & localization techniques", "ref": "Sabiston Ch.74, Schwartz Ch.38, Bailey Ch.56"},
        ],
    },

    "Head & Neck (Surg Onc)": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Oral cavity tumors", "ref": "Sabiston Ch.67, Schwartz Ch.18, Bailey Ch.53"},
            {"name": "Laryngeal & pharyngeal tumors", "ref": "Sabiston Ch.67, Schwartz Ch.18, Bailey Ch.52"},
            {"name": "Salivary gland tumors", "ref": "Sabiston Ch.66, Schwartz Ch.18, Bailey Ch.54"},
            {"name": "Neck dissection — types & levels", "ref": "Sabiston Ch.67, Schwartz Ch.18, Bailey Ch.52"},
            {"name": "Lymph node management in head & neck cancer", "ref": "Sabiston Ch.67-71, Schwartz Ch.18"},
            {"name": "Reconstructive flaps for head & neck", "ref": "Sabiston Ch.69, Schwartz Ch.45, Bailey Ch.47"},
        ],
    },

    "Adrenal": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Adrenal anatomy & physiology", "ref": "Sabiston Ch.75, Schwartz Ch.38, Bailey Ch.57"},
            {"name": "Cushing's syndrome & adrenal Cushing's", "ref": "Sabiston Ch.75, Schwartz Ch.38, Bailey Ch.57"},
            {"name": "Pheochromocytoma & paraganglioma", "ref": "Sabiston Ch.75, Schwartz Ch.38, Bailey Ch.57"},
            {"name": "Conn's syndrome & aldosteronoma", "ref": "Sabiston Ch.75, Schwartz Ch.38, Bailey Ch.57"},
            {"name": "Adrenocortical carcinoma", "ref": "Sabiston Ch.75, Schwartz Ch.38, Bailey Ch.57"},
            {"name": "Adrenalectomy — open & laparoscopic", "ref": "Sabiston Ch.75, Schwartz Ch.38, Bailey Ch.57"},
            {"name": "Incidentaloma workup", "ref": "Sabiston Ch.75, Schwartz Ch.38"},
        ],
    },

    "Cardiac Surgery": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Cardiopulmonary bypass principles", "ref": "Sabiston Ch.111-112, Schwartz Ch.21, Bailey Ch.59"},
            {"name": "Coronary artery disease & CABG", "ref": "Sabiston Ch.111, Schwartz Ch.21, Bailey Ch.59"},
            {"name": "Valvular heart disease — aortic & mitral", "ref": "Sabiston Ch.112, Schwartz Ch.21, Bailey Ch.59"},
            {"name": "Congenital heart disease basics", "ref": "Sabiston Ch.113, Schwartz Ch.20, Bailey Ch.59"},
            {"name": "Aortic dissection & aneurysm repair", "ref": "Sabiston Ch.102, Schwartz Ch.22, Bailey Ch.59"},
            {"name": "Heart transplantation", "ref": "Sabiston Ch.57, Schwartz Ch.11, Bailey Ch.92"},
        ],
    },

    "Thoracic Surgery": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Lung anatomy & pulmonary physiology", "ref": "Sabiston Ch.110, Schwartz Ch.19, Bailey Ch.60"},
            {"name": "Lung carcinoma — staging & management", "ref": "Sabiston Ch.110, Schwartz Ch.19, Bailey Ch.60"},
            {"name": "Pneumothorax & chest drainage", "ref": "Sabiston Ch.110, Schwartz Ch.19, Bailey Ch.60"},
            {"name": "Mediastinal tumors", "ref": "Sabiston Ch.110, Schwartz Ch.19, Bailey Ch.60"},
            {"name": "Esophageal cancer", "ref": "Sabiston Ch.84, Schwartz Ch.25, Bailey Ch.66"},
            {"name": "Benign esophageal disorders & achalasia", "ref": "Sabiston Ch.83, Schwartz Ch.25, Bailey Ch.66"},
            {"name": "Diaphragmatic hernias", "ref": "Sabiston Ch.83, Schwartz Ch.25"},
        ],
    },

    "Vascular Surgery": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Peripheral arterial disease", "ref": "Sabiston Ch.103, Schwartz Ch.23, Bailey Ch.61"},
            {"name": "Aortic aneurysm — AAA & TAA", "ref": "Sabiston Ch.102, Schwartz Ch.22-23, Bailey Ch.61"},
            {"name": "Carotid artery disease & endarterectomy", "ref": "Sabiston Ch.101, Schwartz Ch.23, Bailey Ch.61"},
            {"name": "Venous disease & DVT", "ref": "Sabiston Ch.108, Schwartz Ch.24, Bailey Ch.62"},
            {"name": "Varicose veins & venous ulcers", "ref": "Sabiston Ch.108, Schwartz Ch.24, Bailey Ch.62"},
            {"name": "Vascular access for dialysis", "ref": "Sabiston Ch.107, Schwartz Ch.23"},
            {"name": "Endovascular interventions — EVAR & stenting", "ref": "Sabiston Ch.102-103, Schwartz Ch.23"},
            {"name": "Lymphedema", "ref": "Sabiston Ch.109, Schwartz Ch.24, Bailey Ch.62A"},
        ],
    },

    "Plastic Surgery & Burns": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Wound healing & scar management", "ref": "Sabiston Ch.23, Schwartz Ch.9, Bailey Ch.3"},
            {"name": "Burns — classification & management", "ref": "Sabiston Ch.43, Schwartz Ch.8, Bailey Ch.46"},
            {"name": "Skin grafts — STSG & FTSG", "ref": "Sabiston Ch.69, Schwartz Ch.45, Bailey Ch.47"},
            {"name": "Flaps — classification & principles", "ref": "Sabiston Ch.69, Schwartz Ch.45, Bailey Ch.47"},
            {"name": "Hand surgery basics", "ref": "Sabiston Ch.119, Schwartz Ch.44, Bailey Ch.47"},
            {"name": "Cleft lip & palate", "ref": "Schwartz Ch.45, Bailey Ch.50"},
            {"name": "Microsurgery principles", "ref": "Sabiston Ch.69, Schwartz Ch.45, Bailey Ch.47"},
        ],
    },

    # ======================== P2 MODERATE (36%) ========================

    "Basic Principles": {
        "priority": "P2_MODERATE", "weight": 0.05,
        "subtopics": [
            {"name": "Metabolic response to injury & surgical physiology", "ref": "Sabiston Ch.33, Schwartz Ch.2, Bailey Ch.1"},
            {"name": "Fluid & electrolyte balance", "ref": "Sabiston Ch.33, Schwartz Ch.3, Bailey Ch.25"},
            {"name": "Hemostasis & blood transfusion", "ref": "Sabiston Ch.100, Schwartz Ch.4, Bailey Ch.2"},
            {"name": "Shock — pathophysiology & management", "ref": "Sabiston Ch.33, Schwartz Ch.5, Bailey Ch.2"},
            {"name": "Surgical infections & antibiotics", "ref": "Sabiston Ch.25, Schwartz Ch.6, Bailey Ch.5"},
            {"name": "Nutrition in surgical patients", "ref": "Sabiston Ch.34, Schwartz Ch.2, Bailey Ch.25"},
        ],
    },

    "Pediatric Surgery": {
        "priority": "P2_MODERATE", "weight": 0.05,
        "subtopics": [
            {"name": "Pyloric stenosis", "ref": "Sabiston Ch.117, Schwartz Ch.39, Bailey Ch.17"},
            {"name": "Intussusception", "ref": "Sabiston Ch.117, Schwartz Ch.39, Bailey Ch.17"},
            {"name": "Hirschsprung's disease", "ref": "Sabiston Ch.117, Schwartz Ch.39, Bailey Ch.17"},
            {"name": "Anorectal malformations", "ref": "Sabiston Ch.117, Schwartz Ch.39, Bailey Ch.17"},
            {"name": "Tracheo-esophageal fistula", "ref": "Sabiston Ch.117, Schwartz Ch.39, Bailey Ch.18"},
            {"name": "Wilms' tumor & neuroblastoma", "ref": "Sabiston Ch.117, Schwartz Ch.39, Bailey Ch.17"},
            {"name": "Congenital diaphragmatic hernia", "ref": "Sabiston Ch.117, Schwartz Ch.39, Bailey Ch.18"},
        ],
    },

    "Perioperative Care": {
        "priority": "P2_MODERATE", "weight": 0.05,
        "subtopics": [
            {"name": "Preoperative assessment & optimization", "ref": "Sabiston Ch.19, Schwartz Ch.12, Bailey Ch.21"},
            {"name": "Anesthesia principles for surgeons", "ref": "Sabiston Ch.20, Schwartz Ch.46, Bailey Ch.23"},
            {"name": "Postoperative complications", "ref": "Sabiston Ch.26, Schwartz Ch.12, Bailey Ch.24"},
            {"name": "Enhanced recovery (ERAS) protocols", "ref": "Sabiston Ch.22, Schwartz Ch.50, Bailey Ch.24"},
            {"name": "DVT prophylaxis & anticoagulation", "ref": "Sabiston Ch.100-114, Schwartz Ch.24, Bailey Ch.24"},
            {"name": "Pain management in surgery", "ref": "Sabiston Ch.20, Schwartz Ch.46, Bailey Ch.23"},
        ],
    },

    "Trauma": {
        "priority": "P2_MODERATE", "weight": 0.05,
        "subtopics": [
            {"name": "ATLS — primary & secondary survey", "ref": "Sabiston Ch.36, Schwartz Ch.7, Bailey Ch.27"},
            {"name": "Abdominal trauma — blunt & penetrating", "ref": "Sabiston Ch.36, Schwartz Ch.7, Bailey Ch.29"},
            {"name": "Thoracic trauma", "ref": "Sabiston Ch.36, Schwartz Ch.7, Bailey Ch.29"},
            {"name": "Head injury management", "ref": "Sabiston Ch.41, Schwartz Ch.42, Bailey Ch.28"},
            {"name": "Damage control surgery", "ref": "Sabiston Ch.36-37, Schwartz Ch.7"},
            {"name": "Vascular & extremity trauma", "ref": "Sabiston Ch.38-40, Schwartz Ch.7, Bailey Ch.32"},
        ],
    },

    "Genitourinary": {
        "priority": "P2_MODERATE", "weight": 0.04,
        "subtopics": [
            {"name": "Renal tumors — RCC", "ref": "Sabiston Ch.121, Schwartz Ch.40, Bailey Ch.82"},
            {"name": "Bladder tumors", "ref": "Sabiston Ch.121, Schwartz Ch.40, Bailey Ch.83"},
            {"name": "Prostate disease — BPH & carcinoma", "ref": "Sabiston Ch.121, Schwartz Ch.40, Bailey Ch.84"},
            {"name": "Urolithiasis", "ref": "Sabiston Ch.121, Schwartz Ch.40, Bailey Ch.82"},
            {"name": "Testicular tumors", "ref": "Sabiston Ch.121, Schwartz Ch.40, Bailey Ch.86"},
        ],
    },

    "Basic & Liver Transplant": {
        "priority": "P2_MODERATE", "weight": 0.04,
        "subtopics": [
            {"name": "Transplant immunology basics", "ref": "Sabiston Ch.49, Schwartz Ch.11, Bailey Ch.88"},
            {"name": "Liver transplant — indications & technique", "ref": "Sabiston Ch.53, Schwartz Ch.11, Bailey Ch.89"},
            {"name": "Living donor liver transplant", "ref": "Sabiston Ch.53, Schwartz Ch.11, Bailey Ch.89"},
            {"name": "Post-transplant immunosuppression", "ref": "Sabiston Ch.49, Schwartz Ch.11, Bailey Ch.88"},
            {"name": "Graft rejection types", "ref": "Sabiston Ch.49, Schwartz Ch.11, Bailey Ch.88"},
        ],
    },

    "Neurosurgery": {
        "priority": "P2_MODERATE", "weight": 0.04,
        "subtopics": [
            {"name": "Intracranial pressure & herniation", "ref": "Sabiston Ch.41, Schwartz Ch.42, Bailey Ch.48"},
            {"name": "Brain tumors — classification", "ref": "Schwartz Ch.42, Bailey Ch.48"},
            {"name": "Spinal cord tumors & disc disease", "ref": "Schwartz Ch.42, Bailey Ch.37"},
            {"name": "Hydrocephalus & shunts", "ref": "Schwartz Ch.42, Bailey Ch.48"},
            {"name": "Subarachnoid hemorrhage & aneurysms", "ref": "Schwartz Ch.42, Bailey Ch.48"},
            {"name": "Cranial & spinal trauma", "ref": "Sabiston Ch.41, Schwartz Ch.42, Bailey Ch.28-30"},
        ],
    },

    "Renal & Pancreas Tx, Cardiac/Lung/Intestinal Tx": {
        "priority": "P2_MODERATE", "weight": 0.04,
        "subtopics": [
            {"name": "Renal transplant — technique & outcomes", "ref": "Sabiston Ch.52, Schwartz Ch.11, Bailey Ch.88"},
            {"name": "Pancreas & islet transplant", "ref": "Sabiston Ch.54-55, Schwartz Ch.11, Bailey Ch.90"},
            {"name": "Heart & lung transplant basics", "ref": "Sabiston Ch.57-58, Schwartz Ch.11, Bailey Ch.92"},
            {"name": "Intestinal transplant indications", "ref": "Sabiston Ch.56, Schwartz Ch.11, Bailey Ch.91"},
            {"name": "Organ procurement & allocation", "ref": "Sabiston Ch.49, Schwartz Ch.11, Bailey Ch.88"},
        ],
    },

    # ======================== P3 SUPPORT (16%) ========================

    "GIT Upper": {
        "priority": "P3_SUPPORT", "weight": 0.04,
        "subtopics": [
            {"name": "Gastric carcinoma", "ref": "Sabiston Ch.87, Schwartz Ch.26, Bailey Ch.67"},
            {"name": "Peptic ulcer disease & H. pylori", "ref": "Sabiston Ch.86, Schwartz Ch.26, Bailey Ch.67"},
            {"name": "GERD & hiatus hernia", "ref": "Sabiston Ch.83, Schwartz Ch.25, Bailey Ch.66"},
            {"name": "Bariatric surgery", "ref": "Sabiston Ch.99, Schwartz Ch.27, Bailey Ch.68"},
            {"name": "Upper GI bleeding", "ref": "Sabiston Ch.98, Schwartz Ch.26, Bailey Ch.67"},
        ],
    },

    "GIT Lower": {
        "priority": "P3_SUPPORT", "weight": 0.04,
        "subtopics": [
            {"name": "Colorectal carcinoma", "ref": "Sabiston Ch.96, Schwartz Ch.29, Bailey Ch.77-79"},
            {"name": "Inflammatory bowel disease", "ref": "Sabiston Ch.95, Schwartz Ch.29, Bailey Ch.75"},
            {"name": "Appendicitis", "ref": "Sabiston Ch.94, Schwartz Ch.30, Bailey Ch.76"},
            {"name": "Intestinal obstruction", "ref": "Sabiston Ch.91, Schwartz Ch.28, Bailey Ch.78"},
            {"name": "Anorectal disease — hemorrhoids, fistula, fissure", "ref": "Sabiston Ch.97, Schwartz Ch.29, Bailey Ch.80"},
        ],
    },

    "GIT HPB": {
        "priority": "P3_SUPPORT", "weight": 0.04,
        "subtopics": [
            {"name": "Gallstone disease & cholecystectomy", "ref": "Sabiston Ch.88, Schwartz Ch.32, Bailey Ch.71"},
            {"name": "Pancreatic carcinoma", "ref": "Sabiston Ch.93, Schwartz Ch.33, Bailey Ch.72"},
            {"name": "Acute & chronic pancreatitis", "ref": "Sabiston Ch.92, Schwartz Ch.33, Bailey Ch.72"},
            {"name": "Liver tumors — HCC & metastases", "ref": "Sabiston Ch.89-90, Schwartz Ch.31, Bailey Ch.69"},
            {"name": "Portal hypertension & splenomegaly", "ref": "Sabiston Ch.89, Schwartz Ch.31, Bailey Ch.69-70"},
            {"name": "Obstructive jaundice & cholangiocarcinoma", "ref": "Sabiston Ch.88, Schwartz Ch.32, Bailey Ch.71"},
        ],
    },

    "GIT Misc": {
        "priority": "P3_SUPPORT", "weight": 0.04,
        "subtopics": [
            {"name": "Hernias — inguinal, femoral, incisional", "ref": "Sabiston Ch.79-82, Schwartz Ch.37, Bailey Ch.64"},
            {"name": "Abdominal wall & mesentery", "ref": "Sabiston Ch.37, Schwartz Ch.35, Bailey Ch.64-65"},
            {"name": "Peritoneum & omentum", "ref": "Sabiston Ch.85, Schwartz Ch.35, Bailey Ch.65"},
            {"name": "Spleen & splenic disorders", "ref": "Sabiston Ch.72, Schwartz Ch.34, Bailey Ch.70"},
            {"name": "Small bowel tumors & diseases", "ref": "Sabiston Ch.91, Schwartz Ch.28, Bailey Ch.74"},
        ],
    },
}

# --------------------------------------------------------------------------- #
#                       PRIORITY DISTRIBUTION
# --------------------------------------------------------------------------- #

PRIORITY_DISTRIBUTION = {
    "P1_HIGH": {"weight": 0.48, "buckets": {}},
    "P2_MODERATE": {"weight": 0.36, "buckets": {}},
    "P3_SUPPORT": {"weight": 0.16, "buckets": {}},
}
for topic, data in SYLLABUS_TREE.items():
    tier = data["priority"]
    PRIORITY_DISTRIBUTION[tier]["buckets"][topic] = data["weight"]

# Weighted rotation: P1 topics appear 3x, P2 2x, P3 1x — interleaved
ROTATION_ORDER: List[str] = []
_p1 = [t for t, d in SYLLABUS_TREE.items() if d["priority"] == "P1_HIGH"]
_p2 = [t for t, d in SYLLABUS_TREE.items() if d["priority"] == "P2_MODERATE"]
_p3 = [t for t, d in SYLLABUS_TREE.items() if d["priority"] == "P3_SUPPORT"]

for i in range(max(len(_p1) * 3, len(_p2) * 2, len(_p3))):
    if i < len(_p1) * 3:
        ROTATION_ORDER.append(_p1[i % len(_p1)])
    if i < len(_p2) * 2:
        ROTATION_ORDER.append(_p2[i % len(_p2)])
    if i < len(_p3):
        ROTATION_ORDER.append(_p3[i % len(_p3)])


def get_topic_priority(topic: str) -> str:
    data = SYLLABUS_TREE.get(topic)
    if data:
        return data["priority"]
    return "P3_SUPPORT"


def get_topic_weight(topic: str) -> float:
    data = SYLLABUS_TREE.get(topic)
    if data:
        return data["weight"]
    return 0.02


def get_subtopics(topic: str) -> List[Dict[str, str]]:
    data = SYLLABUS_TREE.get(topic)
    if data:
        return data["subtopics"]
    return []


def get_subtopic_for_day(topic: str, day_index: int) -> Dict[str, str]:
    subs = get_subtopics(topic)
    if not subs:
        return {"name": topic, "ref": ""}
    return subs[day_index % len(subs)]


def get_all_topics() -> List[str]:
    return list(SYLLABUS_TREE.keys())


def get_topic_count() -> Dict[str, int]:
    return {t: len(d["subtopics"]) for t, d in SYLLABUS_TREE.items()}


# --------------------------------------------------------------------------- #
#                       SM-2 SPACED REPETITION
# --------------------------------------------------------------------------- #

def sm2_next_interval(quality: int, repetitions: int, prev_interval: float, prev_ef: float):
    """
    SM-2 algorithm for spaced repetition.
    quality: 0-5 (0-2 = fail, 3 = hard, 4 = good, 5 = easy)
    Returns: (next_interval_days, new_ef, new_repetitions)
    """
    if quality < 3:
        return 1, max(1.3, prev_ef - 0.2), 0

    if repetitions == 0:
        interval = 1
    elif repetitions == 1:
        interval = 3
    else:
        interval = round(prev_interval * prev_ef)

    new_ef = prev_ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    new_ef = max(1.3, new_ef)

    return interval, new_ef, repetitions + 1


# --------------------------------------------------------------------------- #
#                       PREDICTED SCORE MODEL
# --------------------------------------------------------------------------- #

def predict_score_range(
    coverage_pct: float,
    avg_accuracy: float,
    p1_accuracy: float,
    days_remaining: int,
    total_days: int,
) -> Dict[str, Any]:
    base = (coverage_pct * 0.4 + avg_accuracy * 0.4 + p1_accuracy * 0.2) * 100
    time_factor = min(1.0, days_remaining / max(1, total_days * 0.5))

    low = max(0, base - 15 - time_factor * 10)
    high = min(100, base + 10 + time_factor * 15)

    if high >= 80:
        verdict = "Strong — on track for a competitive score"
    elif high >= 60:
        verdict = "Moderate — increase MCQ intensity and recall compliance"
    elif high >= 40:
        verdict = "Needs attention — focus on P1 topics and daily consistency"
    else:
        verdict = "At risk — consider extending preparation or restructuring plan"

    return {
        "estimated_low": round(low, 1),
        "estimated_high": round(high, 1),
        "verdict": verdict,
        "factors": {
            "coverage_pct": round(coverage_pct * 100, 1),
            "avg_accuracy": round(avg_accuracy * 100, 1),
            "p1_accuracy": round(p1_accuracy * 100, 1),
            "days_remaining": days_remaining,
        },
    }
