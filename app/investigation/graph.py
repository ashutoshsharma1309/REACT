"""Evidence graph: Subject -> Signal -> Evidence -> Hypothesis -> Conclusion.

Built in memory from the investigation results and stored as JSON on the investigation so that
every conclusion can be walked back to the records that produced it.
"""
from __future__ import annotations

from typing import Any

from app.investigation.evidence import EvidenceDraft
from app.investigation.signals import SignalSet


def build_graph(subject_kind: str, subject_id: str, subject_label: str, signals: SignalSet, scored: list[dict[str, Any]], evidence: dict[str, list[EvidenceDraft]], evidence_ids: dict[int, str], conclusion: dict[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    subject_node = f"{subject_kind}:{subject_id}"
    nodes.append({"id": subject_node, "kind": subject_kind, "label": subject_label})
    used_signals: set[str] = set()
    for h in scored:
        h_node = f"hypothesis:{h['type']}"
        nodes.append({"id": h_node, "kind": "hypothesis", "label": h["label"], "evidence_support_score": h["evidence_support_score"], "rank": h["rank"]})
        for d in evidence.get(h["type"], []):
            e_id = evidence_ids.get(id(d), f"{h['type']}:{d.signal}")
            e_node = f"evidence:{e_id}"
            s_node = f"signal:{d.signal}"
            if d.signal not in used_signals:
                obs = signals.get(d.signal)
                nodes.append({"id": s_node, "kind": "signal", "label": d.signal, "value": obs.value if obs else None, "unit": obs.unit if obs else None, "source_type": d.source_type, "source_count": len(d.source_ids)})
                edges.append({"from": subject_node, "to": s_node, "type": "observed"})
                for rid in d.source_ids[:10]:
                    edges.append({"from": f"record:{d.source_type}:{rid}", "to": s_node, "type": "derived_from"})
                used_signals.add(d.signal)
            nodes.append({"id": e_node, "kind": "evidence", "label": d.claim, "supports": d.supports, "evidence_support_score": round(d.score, 3), "source_type": d.source_type, "source_id": d.source_id})
            edges.append({"from": s_node, "to": e_node, "type": "evidence"})
            edges.append({"from": e_node, "to": h_node, "type": "supports" if d.supports else "contradicts", "weight": d.weight})
    c_node = "conclusion"
    nodes.append({"id": c_node, "kind": "conclusion", "label": conclusion.get("statement"), "evidence_support_score": conclusion.get("evidence_support_score"), "determined": conclusion.get("determined")})
    if conclusion.get("selected_hypothesis"):
        edges.append({"from": f"hypothesis:{conclusion['selected_hypothesis']}", "to": c_node, "type": "concludes"})
    record_nodes = sorted({e["from"] for e in edges if e["from"].startswith("record:")})
    for r in record_nodes:
        _, stype, rid = r.split(":", 2)
        nodes.append({"id": r, "kind": "record", "label": f"{stype} {rid}", "source_type": stype, "record_id": rid})
    return {"nodes": nodes, "edges": edges, "path": ["subject", "signal", "evidence", "hypothesis", "conclusion"], "counts": {"signals": len(used_signals), "evidence": sum(len(v) for v in evidence.values()), "hypotheses": len(scored), "records": len(record_nodes)}}
