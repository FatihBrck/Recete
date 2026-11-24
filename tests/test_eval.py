import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from eval.manifest import ManifestRow, Prediction, load_manifest, load_predictions
from eval.metrics import auto_approve, calibration, classification, field_accuracy, latency
from eval.run_eval import app


def f(value, conf=0.99):
    return {"value": value, "raw": str(value), "confidence": conf, "handwritten": False}


def resp(doc_type="glasses", dconf=0.99, right=None, left=None, lat=100):
    return {"doc_type": doc_type, "doc_type_confidence": dconf, "right": right,
            "left": left, "latency_ms": lat}


def row(i, doc_type="glasses", right=None, left=None):
    return ManifestRow(i, Path(f"{i}.jpg"), doc_type, right, left)


def pred(i, response=None, error=None):
    return Prediction(i, response, error)


def test_bc_tolerance():
    rows = [row("a", right={"bc": 8.6})]
    preds = [pred("a", resp(right={"bc": f(8.7)}))]
    acc = field_accuracy(rows, preds)
    assert acc["all.bc"]["exact"] == 0.0 and acc["all.bc"]["tolerant"] == 1.0
    assert acc["glasses.bc"]["n"] == 1


def test_absent_key_not_scored():
    rows = [row("a", right={"bc": 8.6})]
    preds = [pred("a", resp(right={"bc": f(8.6), "dia": f(14.2)}))]
    acc = field_accuracy(rows, preds)
    assert "all.dia" not in acc


def test_null_truth_and_null_eye_prediction():
    rows = [row("a", right={"add": None, "sph": -1.0})]
    preds = [pred("a", resp(right=None))]
    acc = field_accuracy(rows, preds)
    assert acc["all.add"]["exact"] == 0.0
    assert acc["all.sph"]["exact"] == 0.0
    assert auto_approve(rows, preds)["n_wrong"] == 1


def test_null_truth_correct_when_eye_present():
    rows = [row("a", right={"add": None})]
    preds = [pred("a", resp(right={"add": None}))]
    assert field_accuracy(rows, preds)["all.add"]["exact"] == 1.0


def test_null_eye_with_only_null_truth_doc_wrong():
    rows = [row("a", right={"add": None})]
    assert auto_approve(rows, [pred("a", resp(right=None))])["n_wrong"] == 1


def test_prediction_for_null_truth_eye_counts_wrong_per_field():
    rows = [row("a", right={"sph": -1.0}, left=None)]
    preds = [pred("a", resp(right={"sph": f(-1.0)},
                            left={"sph": f(-2.0, .7), "cyl": f(-0.5, .6), "add": f(None)}))]
    acc = field_accuracy(rows, preds)
    assert acc["glasses.sph"]["n"] == 2 and acc["glasses.sph"]["exact"] == 0.5
    assert acc["all.cyl"] == {"n": 1, "exact": 0.0, "tolerant": 0.0}
    assert "all.add" not in acc  # null prediction on a null eye is correct, not an item
    r = auto_approve(rows, preds)
    assert r["n_wrong"] == 1 and r["threshold"] is None


def test_null_truth_eye_null_prediction_is_fine():
    rows = [row("a", right={"sph": -1.0}, left=None)]
    preds = [pred("a", resp(right={"sph": f(-1.0)}, left=None))]
    assert field_accuracy(rows, preds)["all.sph"]["n"] == 1
    assert auto_approve(rows, preds)["n_wrong"] == 0


def test_spurious_eye_lowers_document_confidence():
    rows = [row("a", right={"sph": -1.0}, left=None)]
    preds = [pred("a", resp(right={"sph": f(-1.0, .99)}, left={"sph": f(-2.0, .4)}))]
    from eval.metrics import _doc_result
    conf, ok = _doc_result(rows[0], preds[0])
    assert ok is False and conf == 0.4


def test_no_qualifying_threshold():
    rows = [row("a", right={"sph": -1.0}), row("b", right={"sph": -1.0})]
    preds = [pred("a", resp(dconf=.99, right={"sph": f(-2.0, .99)})),
             pred("b", resp(dconf=.5, right={"sph": f(-1.0, .5)}))]
    r = auto_approve(rows, preds)
    assert r["n_wrong"] == 1 and r["threshold"] is None and r["auto_approve_rate"] == 0.0


def test_calibration_null_and_out_of_range_confidence():
    rows = [row(str(i), right={"sph": -1.0}) for i in range(3)]
    preds = [pred("0", resp(right={"sph": {"value": -1.0, "confidence": None}})),
             pred("1", resp(right={"sph": f(-1.0, 1.5)})),
             pred("2", resp(right={"sph": f(-2.0, -0.1)}))]
    bins = {b["bin"]: b for b in calibration(rows, preds)}
    assert bins["[0, 0.5)"]["n"] == 1
    assert bins["aralık dışı"]["n"] == 2 and bins["aralık dışı"]["accuracy"] == 0.5
    assert auto_approve(rows, preds)["n"] == 3


def test_color_case_insensitive():
    rows = [row("a", doc_type="lens_printed", right={"color": "Mavi"})]
    preds = [pred("a", resp("lens_printed", right={"color": f(" mavi ")}))]
    assert field_accuracy(rows, preds)["all.color"]["exact"] == 1.0


def _aa_fixture():
    spec = [(.99, True), (.97, True), (.95, False), (.90, True), (.50, False)]
    rows, preds = [], []
    for i, (c, ok) in enumerate(spec):
        rows.append(row(str(i), right={"sph": -1.0}))
        preds.append(pred(str(i), resp(dconf=c, right={"sph": f(-1.0 if ok else -2.0, c)})))
    return rows, preds


def test_auto_approve_threshold():
    rows, preds = _aa_fixture()
    r = auto_approve(rows, preds)
    assert r["threshold"] == pytest.approx(.97) and r["auto_approve_rate"] == pytest.approx(.4)
    assert r["wrong_above"] == 0 and r["n_wrong"] == 2


def test_error_rows_count_wrong_and_low_confidence():
    rows = [row("a", right={"sph": -1.0}), row("b", right={"sph": -1.0})]
    preds = [pred("a", resp(right={"sph": f(-1.0)})), pred("b", error="HTTP 503")]
    r = auto_approve(rows, preds)
    assert r["n_wrong"] == 1 and r["threshold"] == pytest.approx(.99)
    assert r["auto_approve_rate"] == pytest.approx(.5)
    assert field_accuracy(rows, preds)["all.sph"]["exact"] == 0.5


def test_other_rows_score_classification_only():
    rows = [row("a", doc_type="other")]
    preds = [pred("a", resp("other", dconf=0.9))]
    assert field_accuracy(rows, preds) == {}
    r = auto_approve(rows, preds)
    assert r["n_wrong"] == 0 and r["threshold"] == pytest.approx(.9)


def test_classification_confusion():
    rows = [row("a"), row("b"), row("c", doc_type="other")]
    preds = [pred("a", resp("glasses")), pred("b", resp("lens_printed")), pred("c", error="x")]
    acc, conf = classification(rows, preds)
    assert acc == pytest.approx(1 / 3)
    assert conf == {("glasses", "glasses"): 1, ("glasses", "lens_printed"): 1,
                    ("other", "error"): 1}


def test_calibration_bins():
    rows = [row(str(i), right={"sph": -1.0}) for i in range(3)]
    preds = [pred("0", resp(right={"sph": f(-1.0, 1.0)})),
             pred("1", resp(right={"sph": f(-2.0, 0.95)})),
             pred("2", resp(right={"sph": f(-1.0, 0.5)}))]
    bins = {b["bin"]: b for b in calibration(rows, preds)}
    assert bins["[0.99, 1.0]"]["n"] == 1 and bins["[0.99, 1.0]"]["accuracy"] == 1.0
    assert bins["[0.95, 0.99)"]["n"] == 1 and bins["[0.95, 0.99)"]["accuracy"] == 0.0
    assert bins["[0.9, 0.95)"]["n"] == 0
    assert bins["[0.5, 0.8)"]["n"] == 1


def test_latency():
    preds = [pred(str(i), resp(lat=l)) for i, l in enumerate([100, 200, 300, 400])]
    assert latency(preds) == {"p50": 200.0, "p95": 400.0}


def _write_manifest(tmp_path):
    (tmp_path / "images").mkdir()
    lines = []
    for i in ("r1", "r2"):
        (tmp_path / "images" / f"{i}.jpg").write_bytes(b"\xff\xd8fake")
        lines.append(json.dumps({"id": i, "image": f"images/{i}.jpg", "doc_type": "glasses",
                                 "right": {"sph": -1.0, "bc": 8.6}}))
    m = tmp_path / "manifest.jsonl"
    m.write_text("\n".join(lines) + "\n")
    return m


def test_load_manifest_relative(tmp_path):
    m = _write_manifest(tmp_path)
    rows = load_manifest(m)
    assert rows[0].image == tmp_path / "images/r1.jpg" and rows[0].left is None


@respx.mock
def test_run_cli_posts_images(tmp_path):
    m = _write_manifest(tmp_path)
    calls = iter([httpx.Response(200, json=resp(right={"sph": f(-1.0)})),
                  httpx.Response(503)])
    route = respx.post("http://svc/v1/read").mock(side_effect=lambda req: next(calls))
    out = tmp_path / "p.jsonl"
    r = CliRunner().invoke(app, ["run", "--manifest", str(m), "--url", "http://svc",
                                 "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert route.call_count == 2
    preds = load_predictions(out)
    assert len(preds) == 2 and preds[0].response and preds[1].error == "HTTP 503"


def test_report_cli_writes_markdown(tmp_path):
    m = _write_manifest(tmp_path)
    p = tmp_path / "p.jsonl"
    p.write_text(json.dumps({"id": "r1", "response": resp(right={"sph": f(-1.0), "bc": f(8.7)}),
                             "error": None}) + "\n"
                 + json.dumps({"id": "r2", "response": None, "error": "boom"}) + "\n")
    r = CliRunner().invoke(app, ["report", "--manifest", str(m), "--predictions", str(p),
                                 "--out", str(tmp_path / "out")])
    assert r.exit_code == 0, r.output
    text = (tmp_path / "out/report.md").read_text()
    for h in ("Sınıflandırma", "Alan doğruluğu", "Kalibrasyon", "Otomatik onay oranı", "Gecikme",
              "**Gözlük BC:**"):
        assert h in text
    assert "Hatalı satır: 1" in text and "bc/dia karşılaştırması toleranslıdır" in text
    assert (tmp_path / "out/fields.csv").read_text().startswith("field,n,exact,tolerant")


def test_compare_cli(tmp_path):
    m = _write_manifest(tmp_path)
    p = tmp_path / "p.jsonl"
    p.write_text(json.dumps({"id": "r1", "response": resp(right={"sph": f(-1.0)}),
                             "error": None}) + "\n")
    r = CliRunner().invoke(app, ["compare", "--manifest", str(m), str(p), str(p)])
    assert r.exit_code == 0 and "Otomatik onay oranı" in r.output


def test_report_no_threshold_line(tmp_path):
    m = _write_manifest(tmp_path)
    p = tmp_path / "p.jsonl"
    p.write_text(json.dumps({"id": "r1", "response": resp(right={"sph": f(-9.0)}),
                             "error": None}) + "\n")
    r = CliRunner().invoke(app, ["report", "--manifest", str(m), "--predictions", str(p),
                                 "--out", str(tmp_path / "o")])
    text = (tmp_path / "o/report.md").read_text()
    assert r.exit_code == 0 and "Hiçbir eşik koşulu sağlamıyor" in text
    assert "Tahmini eksik satır: 1" in text


def _one_pred(tmp_path, name):
    p = tmp_path / name
    p.write_text(json.dumps({"id": "r1", "response": resp(right={"sph": f(-1.0)}),
                             "error": None}) + "\n")
    return str(p)


def test_compare_cli_three_files(tmp_path):
    m = _write_manifest(tmp_path)
    names = [_one_pred(tmp_path, n) for n in ("qwen.jsonl", "gemma.jsonl", "q35.jsonl")]
    r = CliRunner().invoke(app, ["compare", "--manifest", str(m), *names])
    assert r.exit_code == 0, r.output
    header, sep = r.output.splitlines()[:2]
    assert "qwen.jsonl" in header and "gemma.jsonl" in header and "q35.jsonl" in header
    assert sep.count("---") == 4


def test_compare_needs_two_files(tmp_path):
    m = _write_manifest(tmp_path)
    r = CliRunner().invoke(app, ["compare", "--manifest", str(m), _one_pred(tmp_path, "a.jsonl")])
    assert r.exit_code != 0
