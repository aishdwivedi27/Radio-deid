"""Clinical allowlist protected from name/place redaction (SPEC §6.2 layer 4): eponyms, anatomy, months,
degrees, common complaints. Port of ``reference/deid_prototype/text_ner.CLINICAL_ALLOW``, extended.
Centre-extendable via ``extra``."""

from __future__ import annotations

_WORDS = """
Findings Impression Impressions Opinion Advice Report Clinical History Indication Technique Comparison
Conclusion Right Left Bilateral Upper Lower Middle Mid Zone Zones Lobe Lobes Lung Lungs Chest Thorax Heart
Cardiac Abdomen Pelvis Liver Kidney Kidneys Spleen Bladder Gall Pancreas Uterus Ovary Prostate Spine Knee
Hip Shoulder Tibia Fibula Femur Normal Abnormal Mild Moderate Severe Grade Stage Type No Nil Not Seen Noted
Suggest Suggested Advised Correlation Fleischner Kellgren Lawrence Hounsfield Bosniak Salter Harris Weber
Garden Schatzker Pott Colles Smith Barton Monteggia Galeazzi Jones Bennett Rolando Segond Hill Sachs Bankart
Kerley Swyer James Kartagener Mallory Weiss Crohn Hodgkin Wilms Ewing Paget Perthes Osgood Schlatter Baker
Morton Hoffa Chilaiditi Rigler PA AP Lateral Erect Supine Portable CT MRI USG Xray X Ray Plain Contrast Dr MD
DNB DMRD MBBS Radiology Radiologist Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February
March April May June July August September October November December India Fever Cough Pain Trauma Fall
Swelling Known Case Diabetic Hypertensive Headache Vomiting Breathlessness Dyspnoea Dyspnea Giddiness Injury
Weakness Burning Fracture Lump Mass Loss Weight Appetite Chronic Acute Since Days Weeks Months Years Old
Follow Up Review Post Operative Pre
"""
CLINICAL_ALLOW = frozenset(w.lower() for w in _WORDS.split())


def is_clinical(word: str, extra: frozenset[str] = frozenset()) -> bool:
    w = word.lower()
    return w in CLINICAL_ALLOW or w in extra
