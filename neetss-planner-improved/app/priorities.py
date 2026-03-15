"""
Topic priorities and rotation order for NEET SS Surgical Group.

P1 (48%) — High-yield surgical subspecialty topics
P2 (36%) — Moderate-yield topics
P3 (16%) — Support / GIT topics
"""

PRIORITY_DISTRIBUTION = {
    "P1_HIGH": {
        "weight": 0.48,
        "buckets": {
            "Head & Neck (Surg Onc)": 0.06,
            "Plastic Surgery & Burns": 0.06,
            "Thyroid & Parathyroid": 0.06,
            "Breast": 0.06,
            "Adrenal": 0.06,
            "Cardiac Surgery": 0.06,
            "Thoracic Surgery": 0.06,
            "Vascular Surgery": 0.06,
        },
    },
    "P2_MODERATE": {
        "weight": 0.36,
        "buckets": {
            "Basic Principles": 0.05,
            "Pediatric Surgery": 0.05,
            "Perioperative Care": 0.05,
            "Trauma": 0.05,
            "Genitourinary": 0.04,
            "Basic & Liver Transplant": 0.04,
            "Neurosurgery": 0.04,
            "Renal & Pancreas Tx, Cardiac/Lung/Intestinal Tx": 0.04,
        },
    },
    "P3_SUPPORT": {
        "weight": 0.16,
        "buckets": {
            "GIT Upper": 0.04,
            "GIT Lower": 0.04,
            "GIT HPB": 0.04,
            "GIT Misc": 0.04,
        },
    },
}

# Round-robin rotation order — P1 topics appear first (more frequent in cycles)
ROTATION_ORDER = [
    # P1 — 8 topics
    "Breast", "Thyroid & Parathyroid", "Head & Neck (Surg Onc)", "Adrenal",
    "Thoracic Surgery", "Vascular Surgery", "Plastic Surgery & Burns",
    "Cardiac Surgery",
    # P2 — 8 topics
    "Basic Principles", "Pediatric Surgery", "Perioperative Care", "Trauma",
    "Genitourinary", "Basic & Liver Transplant", "Neurosurgery",
    "Renal & Pancreas Tx, Cardiac/Lung/Intestinal Tx",
    # P3 — 4 topics
    "GIT Upper", "GIT Lower", "GIT HPB", "GIT Misc",
]


def get_topic_priority(topic: str) -> str:
    """Return the priority tier for a topic."""
    for tier, data in PRIORITY_DISTRIBUTION.items():
        if topic in data["buckets"]:
            return tier
    return "P3_SUPPORT"


def get_topic_weight(topic: str) -> float:
    """Return the individual weight for a topic."""
    for data in PRIORITY_DISTRIBUTION.values():
        if topic in data["buckets"]:
            return data["buckets"][topic]
    return 0.02
