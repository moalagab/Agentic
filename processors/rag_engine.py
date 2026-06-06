"""
RAG Engine — Knowledge Base Search + AI Response
يبحث في قاعدة المعرفة (Markdown) ويجيب بـ Gemini.
Simple keyword-based retrieval — no embeddings needed for this scale.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional


KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"

_DOCS: dict[str, str] = {}


def _load_knowledge() -> None:
    """Load all markdown files from knowledge/ into memory."""
    global _DOCS
    if _DOCS:
        return
    for md in KNOWLEDGE_DIR.glob("*.md"):
        _DOCS[md.stem] = md.read_text(encoding="utf-8")


def _score_doc(doc: str, query: str) -> float:
    """Simple TF-style scoring — count query word hits in doc."""
    words = re.findall(r'\w+', query.lower())
    doc_lower = doc.lower()
    hits = sum(doc_lower.count(w) for w in words if len(w) >= 3)
    return hits / max(len(words), 1)


def retrieve_context(query: str, top_k: int = 3) -> str:
    """
    Retrieve the most relevant knowledge base sections for a query.
    Returns concatenated text of top_k docs (truncated for context window).
    """
    _load_knowledge()
    if not _DOCS:
        return ""

    scored = [(name, _score_doc(doc, query), doc) for name, doc in _DOCS.items()]
    scored.sort(key=lambda x: -x[1])
    top = scored[:top_k]

    parts = []
    for name, score, doc in top:
        if score > 0:
            parts.append(f"## {name}\n{doc[:1500]}")

    return "\n\n---\n\n".join(parts) if parts else ""


async def rag_answer(query: str, gemini_api_key: str = "") -> str:
    """
    Answer a question using knowledge base + Gemini.
    يجيب على سؤال باستخدام قاعدة المعرفة + Gemini.
    """
    context = retrieve_context(query)
    if not context:
        return "لا تتوفر معلومات كافية في قاعدة المعرفة. تواصل معنا: wa.me/966561167169"

    key = gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return context[:800]

    prompt = f"""أنت مساعد Smart Field للنقل المبرد في الرياض.
اجب على السؤال بشكل مختصر وواضح باللغة العربية، بناءً على المعلومات التالية فقط.
إذا لم تجد الإجابة في المعلومات، قل ذلك بوضوح.

--- قاعدة المعرفة ---
{context}
--- نهاية قاعدة المعرفة ---

السؤال: {query}

الإجابة:"""

    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception:
        try:
            from google import genai as ga
            client = ga.Client(api_key=key)
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            return resp.text.strip()
        except Exception as exc:
            return f"خطأ في الإجابة: {exc}. السياق: {context[:400]}"


def list_topics() -> list[str]:
    """List all available knowledge base topics."""
    _load_knowledge()
    return list(_DOCS.keys())
