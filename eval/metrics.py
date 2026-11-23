"""Değerlendirme metrikleri (saf fonksiyonlar, düz JSON sözlükleri üzerinde)."""
from __future__ import annotations

import math
from collections import Counter
from decimal import Decimal, InvalidOperation

from eval.manifest import ManifestRow, Prediction

SCORED = ("sph", "cyl", "axis", "add", "bc", "dia", "color")
TOLERANCE = {"bc": Decimal("0.2"), "dia": Decimal("0.2")}
DEFAULT_BINS = (0, 0.5, 0.8, 0.9, 0.95, 0.99, 1.0)


def _pred_for(rows, preds) -> list[tuple[ManifestRow, Prediction]]:
    by_id = {p.id: p for p in preds}
    return [
        (r, by_id.get(r.id) or Prediction(r.id, None, "missing prediction"))
        for r in rows
    ]


def _pred_doc_type(p: Prediction) -> str:
    if p.error is not None or p.response is None:
        return "error"
    return p.response.get("doc_type") or "error"


def _scored(truth_eye: dict | None):
    return [k for k in SCORED if truth_eye and k in truth_eye]


def _pred_field(p: Prediction, eye: str, key: str) -> dict | None:
    """Tahmin alanı ({value, confidence, ...}) ya da None."""
    if p.error is not None or not p.response:
        return None
    eye_d = p.response.get(eye)
    if not eye_d:
        return None
    return eye_d.get(key)


def _value(field: dict | None):
    return None if field is None else field.get("value")


def _conf(field: dict | None) -> float:
    """Güven değeri; null/eksik/sayı olmayan -> 0.0."""
    try:
        return float((field or {}).get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _num(x) -> Decimal | None:
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return None


def _equal(key: str, truth, pred, tolerant: bool) -> bool:
    if truth is None or pred is None:
        return truth is None and pred is None
    if key == "color":
        return str(truth).strip().lower() == str(pred).strip().lower()
    t, q = _num(truth), _num(pred)
    if t is None or q is None:
        return False
    if tolerant and key in TOLERANCE:
        return abs(t - q) <= TOLERANCE[key]
    return t == q


def classification(rows, preds):
    pairs = _pred_for(rows, preds)
    confusion: Counter = Counter()
    correct = 0
    for r, p in pairs:
        pd = _pred_doc_type(p)
        confusion[(r.doc_type, pd)] += 1
        correct += pd == r.doc_type
    return (correct / len(pairs) if pairs else 0.0), dict(confusion)


def _iter_fields(pairs):
    """(doc_type, eye, key, truth, pred_field, errored) — 'other' satırları atlanır."""
    for r, p in pairs:
        if r.doc_type == "other":
            continue
        row_errored = p.error is not None or p.response is None
        for eye in ("right", "left"):
            truth_eye = getattr(r, eye)
            # Gerçekte var olan göz tahminde yoksa (null) tüm anahtarlar yanlış.
            eye_missing = bool(truth_eye) and not (p.response or {}).get(eye)
            for key in _scored(truth_eye):
                yield (r.doc_type, eye, key, truth_eye[key],
                       _pred_field(p, eye, key), row_errored or eye_missing)


def field_accuracy(rows, preds) -> dict[str, dict[str, float]]:
    # Hata satırları: tahmin yok, tüm puanlanan alanlar yanlış sayılır.
    acc: dict[str, list[int]] = {}
    for dt, _eye, key, truth, pf, errored in _iter_fields(_pred_for(rows, preds)):
        pv = _value(pf)
        exact = (not errored) and _equal(key, truth, pv, False)
        tol = (not errored) and _equal(key, truth, pv, True)
        for name in (f"{dt}.{key}", f"all.{key}"):
            c = acc.setdefault(name, [0, 0, 0])
            c[0] += 1
            c[1] += exact
            c[2] += tol
    return {
        name: {"n": n, "exact": e / n, "tolerant": t / n}
        for name, (n, e, t) in acc.items()
    }


def calibration(rows, preds, bins=DEFAULT_BINS) -> list[dict]:
    nb = len(bins) - 1
    counts = [[0, 0] for _ in range(nb)]
    out_of_range = [0, 0]
    for _dt, _eye, key, truth, pf, errored in _iter_fields(_pred_for(rows, preds)):
        if errored or pf is None or pf.get("value") is None:
            continue
        conf = _conf(pf)
        idx = None
        if conf < bins[0] or conf > bins[-1]:
            out_of_range[0] += 1
            out_of_range[1] += _equal(key, truth, pf["value"], True)
            continue
        for i in range(nb):
            last = i == nb - 1
            if bins[i] <= conf < bins[i + 1] or (last and conf == bins[-1]):
                idx = i
                break
        counts[idx][0] += 1
        counts[idx][1] += _equal(key, truth, pf["value"], True)
    out = []
    for i, (n, ok) in enumerate(counts):
        close = "]" if i == nb - 1 else ")"
        out.append(
            {
                "bin": f"[{bins[i]}, {bins[i + 1]}{close}",
                "n": n,
                "accuracy": ok / n if n else None,
            }
        )
    n, ok = out_of_range
    out.append({"bin": "aralık dışı", "n": n, "accuracy": ok / n if n else None})
    return out


def _doc_result(r: ManifestRow, p: Prediction) -> tuple[float, bool]:
    """(belge güveni, belge doğru mu)."""
    if p.error is not None or p.response is None:
        return 0.0, False
    resp = p.response
    ok = resp.get("doc_type") == r.doc_type
    conf = _conf({"confidence": resp.get("doc_type_confidence")})
    if r.doc_type == "other":
        return conf, ok
    for eye in ("right", "left"):
        truth_eye = getattr(r, eye)
        if _scored(truth_eye) and not resp.get(eye):
            ok = False
            continue
        for key in _scored(truth_eye):
            pf = _pred_field(p, eye, key)
            pv = _value(pf)
            if pv is not None:
                conf = min(conf, _conf(pf))
            if not _equal(key, truth_eye[key], pv, True):
                ok = False
    return conf, ok


def auto_approve(rows, preds, max_wrong_share: float = 0.01) -> dict:
    results = [_doc_result(r, p) for r, p in _pred_for(rows, preds)]
    n = len(results)
    n_wrong = sum(not ok for _c, ok in results)
    threshold = None
    wrong_above = 0
    if n:
        for t in sorted({c for c, _ in results}):
            wa = sum(1 for c, ok in results if c >= t and not ok)
            if n_wrong == 0 or wa / n_wrong < max_wrong_share:
                threshold, wrong_above = t, wa
                break
    approved = 0 if threshold is None else sum(1 for c, _ in results if c >= threshold)
    return {
        "threshold": threshold,
        "auto_approve_rate": approved / n if n else 0.0,
        "wrong_above": wrong_above,
        "n_wrong": n_wrong,
        "n": n,
    }


def _percentile(sorted_vals: list[float], q: float) -> float:
    k = max(1, math.ceil(q * len(sorted_vals)))
    return sorted_vals[k - 1]


def latency(preds) -> dict:
    vals = sorted(
        float(p.response["latency_ms"])
        for p in preds
        if p.response and p.response.get("latency_ms") is not None
    )
    if not vals:
        return {"p50": None, "p95": None}
    return {"p50": _percentile(vals, 0.5), "p95": _percentile(vals, 0.95)}
