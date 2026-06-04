"""
Smart Field — Cold Outreach Assembler
يبني رسالة الـ outreach من ملف smartfield_cold_outreach.json
حسب اسم العميل ونشاطه.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).parent.parent / "smartfield_cold_outreach.json"

# Segment ID mapping: prospecting engine segment IDs → outreach segment keys
SEGMENT_MAP: dict[str, str] = {
    "food_factories": "food_supplier",
    "pharma_distributors": "pharma",
    "cloud_kitchens": "dark_kitchen",
    "catering_companies": "catering",
    "retail_chains": "retail_chains",
    "cold_storage": "food_supplier",
    "food_suppliers": "food_supplier",
    "specialty_brands": "sweets",
    "online_food": "generic",
    "hotels_hospitality": "small_hotel",
    "meat_poultry": "meat_poultry",
    "seafood": "seafood",
    "dairy_producers": "food_supplier",
}


def load_config(path: str | Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# Module-level cached config — loaded once at import time
_CONFIG: Optional[dict] = None


def get_config() -> dict:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG


def build_outreach(name: str, segment: str, config: dict | None = None) -> str:
    """
    Build a personalised cold outreach WhatsApp message.

    name    — company or contact name (e.g. 'أبو محمد' or 'شركة الفيصل')
    segment — outreach segment key OR prospecting engine segment ID
              (auto-mapped via SEGMENT_MAP)
    """
    if config is None:
        config = get_config()

    # Accept either outreach key or prospecting engine ID
    segment = SEGMENT_MAP.get(segment, segment)

    s = config["static"]
    seg = config["segments"].get(segment, config["segments"]["generic"])

    msg = config["message_template"]
    replacements = {
        "{{greeting}}": s["greeting"],
        "{{intro}}": s["intro"],
        "{{segment_label}}": seg["label"],
        "{{problem_1}}": seg["problem_1"],
        "{{problem_2}}": seg["problem_2"],
        "{{solution_intro}}": s["solution_intro"],
        "{{solution_point_1}}": s["solution_points"][0],
        "{{solution_point_2}}": s["solution_points"][1],
        "{{fit_line}}": seg["fit_line"],
        "{{trial}}": s["trial"],
        "{{signature}}": s["signature"],
        "{{name}}": name,
    }
    for k, v in replacements.items():
        msg = msg.replace(k, v)
    return msg


def build_followup(stage: int, config: dict | None = None) -> str:
    """stage = 1 or 2"""
    if config is None:
        config = get_config()
    key = f"follow_up_{stage}"
    tmpl = config[key]["template"]
    return tmpl.replace("{{greeting}}", config["static"]["greeting"])


def build_outreach_for_prospect(prospect: dict, segment_id: str) -> str:
    """
    Convenience wrapper used by ProspectingEngine.
    prospect — raw prospect dict from Claude (has 'company', 'city', etc.)
    segment_id — PROSPECT_SEGMENTS id (e.g. 'catering_companies')
    """
    company_name = prospect.get("company", "")
    return build_outreach(company_name, segment_id)


# ─── CLI / Example ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cfg = load_config()

    print(build_outreach("أبو محمد", "catering", cfg))
    print("\n" + "═" * 50 + "\n")

    print(build_outreach("أبو خالد", "coffee_roastery", cfg))
    print("\n" + "═" * 50 + "\n")

    print(build_followup(1, cfg))
