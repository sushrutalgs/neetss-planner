"""
NEET SS Surgical Group — Granular Syllabus Tree with Textbook Mapping.

P1 (48%) — High-yield surgical subspecialty topics
P2 (36%) — Moderate-yield topics
P3 (16%) — Support / GIT topics

Each topic is broken into sub-chapters with textbook references.
"""
from __future__ import annotations
from typing import Dict, List, Any

# --------------------------------------------------------------------------- #
#                          SYLLABUS TREE
# --------------------------------------------------------------------------- #

SYLLABUS_TREE: Dict[str, Dict[str, Any]] = {
    # ======================== P1 HIGH YIELD ========================
    "Breast": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Breast anatomy & development", "ref": "Bailey Ch.53, Sabiston Ch.34"},
            {"name": "Benign breast disease", "ref": "Bailey Ch.53, Schwartz Ch.17"},
            {"name": "Fibroadenoma & phyllodes", "ref": "Bailey Ch.53, Sabiston Ch.34"},
            {"name": "Breast carcinoma staging & management", "ref": "Bailey Ch.53, Sabiston Ch.34"},
            {"name": "Breast conservation & mastectomy", "ref": "Sabiston Ch.34, Schwartz Ch.17"},
            {"name": "Breast reconstruction", "ref": "Sabiston Ch.34"},
            {"name": "Male breast & Paget's disease", "ref": "Bailey Ch.53"},
            {"name": "Sentinel lymph node biopsy", "ref": "Sabiston Ch.34, Schwartz Ch.17"},
        ],
    },
    "Thyroid & Parathyroid": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Thyroid anatomy & embryology", "ref": "Bailey Ch.50, Sabiston Ch.36"},
            {"name": "Thyroid function tests & investigations", "ref": "Bailey Ch.50"},
            {"name": "Thyroid nodule & FNAC (Bethesda)", "ref": "Sabiston Ch.36, Schwartz Ch.38"},
            {"name": "Thyroid malignancies (PTC, FTC, MTC, ATC)", "ref": "Bailey Ch.50, Sabiston Ch.36"},
            {"name": "Thyroidectomy & complications", "ref": "Bailey Ch.50, Schwartz Ch.38"},
            {"name": "Graves' disease & thyrotoxicosis", "ref": "Sabiston Ch.36"},
            {"name": "Parathyroid adenoma & hyperparathyroidism", "ref": "Bailey Ch.51, Sabiston Ch.37"},
            {"name": "Parathyroid surgery & localization", "ref": "Sabiston Ch.37"},
        ],
    },
    "Head & Neck (Surg Onc)": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Oral cavity tumors", "ref": "Bailey Ch.45, Sabiston Ch.33"},
            {"name": "Laryngeal & pharyngeal tumors", "ref": "Bailey Ch.46"},
            {"name": "Salivary gland tumors", "ref": "Bailey Ch.49, Sabiston Ch.33"},
            {"name": "Neck dissection types & levels", "ref": "Bailey Ch.48, Schwartz Ch.18"},
            {"name": "Lymph node management in head & neck", "ref": "Sabiston Ch.33"},
            {"name": "Reconstructive flaps for H&N", "ref": "Bailey Ch.47"},
        ],
    },
    "Adrenal": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Adrenal anatomy & physiology", "ref": "Sabiston Ch.39, Schwartz Ch.39"},
            {"name": "Cushing's syndrome & adrenal Cushing's", "ref": "Sabiston Ch.39"},
            {"name": "Pheochromocytoma & paraganglioma", "ref": "Bailey Ch.52, Sabiston Ch.39"},
            {"name": "Conn's syndrome & aldosteronoma", "ref": "Sabiston Ch.39"},
            {"name": "Adrenocortical carcinoma", "ref": "Sabiston Ch.39, Schwartz Ch.39"},
            {"name": "Adrenalectomy (open & laparoscopic)", "ref": "Sabiston Ch.39"},
            {"name": "Incidentaloma workup", "ref": "Schwartz Ch.39"},
        ],
    },
    "Cardiac Surgery": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Cardiopulmonary bypass principles", "ref": "Sabiston Ch.60"},
            {"name": "Coronary artery disease & CABG", "ref": "Sabiston Ch.60, Schwartz Ch.21"},
            {"name": "Valvular heart disease (aortic, mitral)", "ref": "Sabiston Ch.60"},
            {"name": "Congenital heart disease basics", "ref": "Sabiston Ch.59"},
            {"name": "Aortic dissection & aneurysm repair", "ref": "Sabiston Ch.61"},
            {"name": "Heart transplantation", "ref": "Sabiston Ch.64"},
        ],
    },
    "Thoracic Surgery": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Lung anatomy & physiology", "ref": "Sabiston Ch.57"},
            {"name": "Lung carcinoma staging & management", "ref": "Bailey Ch.54, Sabiston Ch.57"},
            {"name": "Pneumothorax & chest drainage", "ref": "Bailey Ch.54"},
            {"name": "Mediastinal tumors", "ref": "Sabiston Ch.58, Schwartz Ch.19"},
            {"name": "Esophageal surgery (carcinoma, achalasia)", "ref": "Sabiston Ch.42"},
            {"name": "Diaphragmatic hernias", "ref": "Sabiston Ch.44"},
            {"name": "VATS & thoracoscopy", "ref": "Schwartz Ch.19"},
        ],
    },
    "Vascular Surgery": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Peripheral arterial disease", "ref": "Bailey Ch.56, Sabiston Ch.62"},
            {"name": "Aortic aneurysm (AAA, TAA)", "ref": "Sabiston Ch.61, Schwartz Ch.23"},
            {"name": "Carotid artery disease & endarterectomy", "ref": "Sabiston Ch.62"},
            {"name": "Venous disease & DVT", "ref": "Bailey Ch.57, Sabiston Ch.63"},
            {"name": "Varicose veins & venous ulcers", "ref": "Bailey Ch.57"},
            {"name": "Vascular access for dialysis", "ref": "Sabiston Ch.62"},
            {"name": "Endovascular interventions (EVAR, stenting)", "ref": "Schwartz Ch.23"},
            {"name": "Lymphedema", "ref": "Bailey Ch.58"},
        ],
    },
    "Plastic Surgery & Burns": {
        "priority": "P1_HIGH", "weight": 0.06,
        "subtopics": [
            {"name": "Wound healing & scar management", "ref": "Bailey Ch.3, Sabiston Ch.7"},
            {"name": "Burns classification & management", "ref": "Bailey Ch.30, Schwartz Ch.8"},
            {"name": "Skin grafts (STSG, FTSG)", "ref": "Bailey Ch.28"},
            {"name": "Flaps classification & principles", "ref": "Bailey Ch.28, Schwartz Ch.45"},
            {"name": "Hand surgery basics", "ref": "Schwartz Ch.45"},
            {"name": "Cleft lip & palate", "ref": "Bailey Ch.40"},
            {"name": "Microsurgery principles", "ref": "Schwartz Ch.45"},
        ],
    },

    # ======================== P2 MODERATE ========================
    "Basic Principles": {
        "priority": "P2_MODERATE", "weight": 0.05,
        "subtopics": [
            {"name": "Surgical physiology & metabolic response", "ref": "Sabiston Ch.2, Schwartz Ch.1"},
            {"name": "Fluid & electrolyte balance", "ref": "Bailey Ch.2, Sabiston Ch.3"},
            {"name": "Hemostasis & blood transfusion", "ref": "Bailey Ch.4, Sabiston Ch.4"},
            {"name": "Shock pathophysiology & management", "ref": "Sabiston Ch.5, Schwartz Ch.5"},
            {"name": "Surgical infections & antibiotics", "ref": "Bailey Ch.5, Sabiston Ch.11"},
            {"name": "Nutrition in surgical patients", "ref": "Sabiston Ch.6, Schwartz Ch.2"},
        ],
    },
    "Pediatric Surgery": {
        "priority": "P2_MODERATE", "weight": 0.05,
        "subtopics": [
            {"name": "Pyloric stenosis", "ref": "Bailey Ch.8, Sabiston Ch.67"},
            {"name": "Intussusception", "ref": "Bailey Ch.73"},
            {"name": "Hirschsprung's disease", "ref": "Sabiston Ch.67"},
            {"name": "Anorectal malformations", "ref": "Sabiston Ch.67"},
            {"name": "Tracheo-esophageal fistula", "ref": "Bailey Ch.8, Sabiston Ch.67"},
            {"name": "Wilms' tumor & neuroblastoma", "ref": "Sabiston Ch.67"},
            {"name": "Congenital diaphragmatic hernia", "ref": "Sabiston Ch.67"},
        ],
    },
    "Perioperative Care": {
        "priority": "P2_MODERATE", "weight": 0.05,
        "subtopics": [
            {"name": "Preoperative assessment & optimization", "ref": "Bailey Ch.17, Sabiston Ch.10"},
            {"name": "Anesthesia principles for surgeons", "ref": "Schwartz Ch.47"},
            {"name": "Postoperative complications", "ref": "Bailey Ch.18, Sabiston Ch.12"},
            {"name": "Enhanced recovery (ERAS) protocols", "ref": "Sabiston Ch.12"},
            {"name": "DVT prophylaxis & anticoagulation", "ref": "Bailey Ch.18"},
            {"name": "Pain management in surgery", "ref": "Schwartz Ch.47"},
        ],
    },
    "Trauma": {
        "priority": "P2_MODERATE", "weight": 0.05,
        "subtopics": [
            {"name": "ATLS primary & secondary survey", "ref": "Bailey Ch.24, Sabiston Ch.18"},
            {"name": "Abdominal trauma (blunt & penetrating)", "ref": "Sabiston Ch.18, Schwartz Ch.7"},
            {"name": "Thoracic trauma", "ref": "Bailey Ch.25, Sabiston Ch.18"},
            {"name": "Head injury management", "ref": "Bailey Ch.26"},
            {"name": "Damage control surgery", "ref": "Schwartz Ch.7"},
            {"name": "Orthopedic trauma basics", "ref": "Bailey Ch.27"},
        ],
    },
    "Genitourinary": {
        "priority": "P2_MODERATE", "weight": 0.04,
        "subtopics": [
            {"name": "Renal tumors (RCC)", "ref": "Bailey Ch.76, Sabiston Ch.73"},
            {"name": "Bladder tumors", "ref": "Bailey Ch.76"},
            {"name": "Prostate disease (BPH, carcinoma)", "ref": "Bailey Ch.76, Sabiston Ch.73"},
            {"name": "Urolithiasis", "ref": "Bailey Ch.76"},
            {"name": "Testicular tumors", "ref": "Bailey Ch.76, Sabiston Ch.73"},
        ],
    },
    "Basic & Liver Transplant": {
        "priority": "P2_MODERATE", "weight": 0.04,
        "subtopics": [
            {"name": "Transplant immunology basics", "ref": "Sabiston Ch.26"},
            {"name": "Liver transplant indications & technique", "ref": "Sabiston Ch.27"},
            {"name": "Living donor liver transplant", "ref": "Sabiston Ch.27"},
            {"name": "Post-transplant immunosuppression", "ref": "Sabiston Ch.26"},
            {"name": "Graft rejection types", "ref": "Sabiston Ch.26, Schwartz Ch.11"},
        ],
    },
    "Neurosurgery": {
        "priority": "P2_MODERATE", "weight": 0.04,
        "subtopics": [
            {"name": "Intracranial pressure & herniation", "ref": "Sabiston Ch.68"},
            {"name": "Brain tumors classification", "ref": "Sabiston Ch.68"},
            {"name": "Spinal cord tumors & disc disease", "ref": "Sabiston Ch.68"},
            {"name": "Hydrocephalus & shunts", "ref": "Bailey Ch.43"},
            {"name": "Subarachnoid hemorrhage & aneurysms", "ref": "Sabiston Ch.68"},
            {"name": "Cranial & spinal trauma", "ref": "Bailey Ch.43"},
        ],
    },
    "Renal & Pancreas Tx, Cardiac/Lung/Intestinal Tx": {
        "priority": "P2_MODERATE", "weight": 0.04,
        "subtopics": [
            {"name": "Renal transplant technique & outcomes", "ref": "Sabiston Ch.28"},
            {"name": "Pancreas & islet transplant", "ref": "Sabiston Ch.28"},
            {"name": "Heart & lung transplant basics", "ref": "Sabiston Ch.64"},
            {"name": "Intestinal transplant indications", "ref": "Sabiston Ch.28"},
            {"name": "Organ procurement & allocation", "ref": "Sabiston Ch.26"},
        ],
    },

    # ======================== P3 SUPPORT ========================
    "GIT Upper": {
        "priority": "P3_SUPPORT", "weight": 0.04,
        "subtopics": [
            {"name": "Gastric carcinoma", "ref": "Bailey Ch.62, Sabiston Ch.49"},
            {"name": "Peptic ulcer disease & H. pylori", "ref": "Bailey Ch.61, Sabiston Ch.49"},
            {"name": "GERD & hiatus hernia", "ref": "Sabiston Ch.42"},
            {"name": "Bariatric surgery", "ref": "Sabiston Ch.48, Schwartz Ch.27"},
            {"name": "Upper GI bleeding", "ref": "Bailey Ch.61"},
        ],
    },
    "GIT Lower": {
        "priority": "P3_SUPPORT", "weight": 0.04,
        "subtopics": [
            {"name": "Colorectal carcinoma", "ref": "Bailey Ch.69, Sabiston Ch.52"},
            {"name": "Inflammatory bowel disease", "ref": "Bailey Ch.67, Sabiston Ch.52"},
            {"name": "Appendicitis", "ref": "Bailey Ch.66, Sabiston Ch.51"},
            {"name": "Intestinal obstruction", "ref": "Bailey Ch.65, Sabiston Ch.50"},
            {"name": "Anorectal disease (hemorrhoids, fistula, fissure)", "ref": "Bailey Ch.72, Sabiston Ch.53"},
        ],
    },
    "GIT HPB": {
        "priority": "P3_SUPPORT", "weight": 0.04,
        "subtopics": [
            {"name": "Gallstone disease & cholecystectomy", "ref": "Bailey Ch.63, Sabiston Ch.55"},
            {"name": "Pancreatic carcinoma", "ref": "Bailey Ch.64, Sabiston Ch.56"},
            {"name": "Acute & chronic pancreatitis", "ref": "Bailey Ch.64, Sabiston Ch.56"},
            {"name": "Liver tumors (HCC, metastases)", "ref": "Bailey Ch.63, Sabiston Ch.54"},
            {"name": "Portal hypertension & splenomegaly", "ref": "Sabiston Ch.54"},
            {"name": "Obstructive jaundice & cholangiocarcinoma", "ref": "Bailey Ch.63, Sabiston Ch.55"},
        ],
    },
    "GIT Misc": {
        "priority": "P3_SUPPORT", "weight": 0.04,
        "subtopics": [
            {"name": "Hernias (inguinal, femoral, incisional)", "ref": "Bailey Ch.60, Sabiston Ch.44"},
            {"name": "Abdominal wall & mesentery", "ref": "Sabiston Ch.44"},
            {"name": "Peritoneum & omentum", "ref": "Bailey Ch.59"},
            {"name": "Spleen & splenic disorders", "ref": "Sabiston Ch.57, Schwartz Ch.33"},
            {"name": "Small bowel tumors", "ref": "Sabiston Ch.50"},
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

# Weighted rotation: P1 topics appear 3x, P2 2x, P3 1x
ROTATION_ORDER: List[str] = []
_p1 = [t for t, d in SYLLABUS_TREE.items() if d["priority"] == "P1_HIGH"]
_p2 = [t for t, d in SYLLABUS_TREE.items() if d["priority"] == "P2_MODERATE"]
_p3 = [t for t, d in SYLLABUS_TREE.items() if d["priority"] == "P3_SUPPORT"]

# Build weighted cycle: P1 x3, P2 x2, P3 x1 — interleaved
for i in range(max(len(_p1) * 3, len(_p2) * 2, len(_p3))):
    if i < len(_p1) * 3:
        ROTATION_ORDER.append(_p1[i % len(_p1)])
    if i < len(_p2) * 2:
        ROTATION_ORDER.append(_p2[i % len(_p2)])
    if i < len(_p3):
        ROTATION_ORDER.append(_p3[i % len(_p3)])


def get_topic_priority(topic: str) -> str:
    """Return the priority tier for a topic."""
    data = SYLLABUS_TREE.get(topic)
    if data:
        return data["priority"]
    return "P3_SUPPORT"


def get_topic_weight(topic: str) -> float:
    """Return the individual weight for a topic."""
    data = SYLLABUS_TREE.get(topic)
    if data:
        return data["weight"]
    return 0.02


def get_subtopics(topic: str) -> List[Dict[str, str]]:
    """Return sub-chapters and textbook references for a topic."""
    data = SYLLABUS_TREE.get(topic)
    if data:
        return data["subtopics"]
    return []


def get_subtopic_for_day(topic: str, day_index: int) -> Dict[str, str]:
    """Return the specific sub-chapter for a given day (cycles through subtopics)."""
    subs = get_subtopics(topic)
    if not subs:
        return {"name": topic, "ref": ""}
    return subs[day_index % len(subs)]


def get_all_topics() -> List[str]:
    """Return all topic names."""
    return list(SYLLABUS_TREE.keys())


def get_topic_count() -> Dict[str, int]:
    """Return count of subtopics per topic."""
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
    """
    Simple heuristic model for predicted score range.
    Returns estimated percentile range and verdict.
    """
    # Base score from coverage and accuracy
    base = (coverage_pct * 0.4 + avg_accuracy * 0.4 + p1_accuracy * 0.2) * 100

    # Time factor: more days remaining = more room to improve
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
