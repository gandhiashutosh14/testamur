"""Ask one document a question: the evaluated pipeline (retrieval, the cited answer, the quote check, the evidence
rule) on a document's pages, for a reviewer. Passages are cut page by page, so every citation names its page."""
from __future__ import annotations

from .answer import answer
from .convert import chunk_lines
from .retrieve import Index
from .schema import Passage, Task

K = 4


def ask_pages(model, question: str, pages, k: int = K) -> dict:
    """`pages`: objects with .number and .lines (each with .text), as the engine's extraction has them."""
    passages, page_of = [], {}
    for page in pages:
        for i, text in enumerate(chunk_lines([line.text for line in page.lines])):
            pid = f"p{page.number}.{i}"
            passages.append(Passage(pid, text))
            page_of[pid] = page.number
    if not passages:
        return {"answer": "", "abstained": True, "supported": False, "citations": [], "unverified": 0,
                "reason": "the document has no text to read"}
    hits = Index(passages).bm25(question, k)
    a = answer(model, Task("ask", "service", "", "answer", question, [], []), [h.passage for h in hits])
    text, _, abstained = a.with_rule("answer")
    return {"answer": text, "abstained": abstained, "supported": a.supported,
            "citations": [{"page": page_of[c["passage"]], "quote": c["quote"]} for c in a.valid],
            "unverified": len(a.citations) - len(a.valid),
            "reason": a.error or ("" if a.supported or a.abstained else
                                  "the answer had no quote that is on the page, so it is not given")}
