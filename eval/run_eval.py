"""Değerlendirme CLI: run / report / compare."""
from __future__ import annotations

import csv
import json
import mimetypes
from pathlib import Path

import httpx
import typer

from eval.manifest import load_manifest, load_predictions
from eval.metrics import auto_approve, calibration, classification, field_accuracy, latency

app = typer.Typer(add_completion=False, help="Reçete okuyucu değerlendirme aracı")


def _pct(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


@app.command()
def run(
    manifest: Path = typer.Option(..., "--manifest"),
    url: str = typer.Option("http://localhost:8080", "--url"),
    out: Path = typer.Option(..., "--out"),
) -> None:
    """Manifestteki her görseli sırayla /v1/read'e gönderir, tahmin JSONL yazar."""
    rows = load_manifest(manifest)
    out.parent.mkdir(parents=True, exist_ok=True)
    endpoint = url.rstrip("/") + "/v1/read"
    with httpx.Client(timeout=120) as client, out.open("w", encoding="utf-8") as fh:
        for r in rows:
            response, error = None, None
            try:
                ctype = mimetypes.guess_type(r.image.name)[0] or "application/octet-stream"
                resp = client.post(
                    endpoint, files={"file": (r.image.name, r.image.read_bytes(), ctype)}
                )
                if resp.status_code == 200:
                    response = resp.json()
                else:
                    error = f"HTTP {resp.status_code}"
            except Exception as exc:  # tek hata tüm koşuyu düşürmez
                error = f"{type(exc).__name__}: {exc}"
            fh.write(
                json.dumps({"id": r.id, "response": response, "error": error}, ensure_ascii=False)
                + "\n"
            )
            fh.flush()


@app.command()
def report(
    manifest: Path = typer.Option(..., "--manifest"),
    predictions: Path = typer.Option(..., "--predictions"),
    out: Path = typer.Option(..., "--out"),
) -> None:
    """DIR/report.md ve DIR/fields.csv üretir."""
    rows = load_manifest(manifest)
    preds = load_predictions(predictions)
    out.mkdir(parents=True, exist_ok=True)

    cls_acc, confusion = classification(rows, preds)
    fields = field_accuracy(rows, preds)
    calib = calibration(rows, preds)
    aa = auto_approve(rows, preds)
    lat = latency(preds)

    ids = {p.id for p in preds}
    n_missing = sum(1 for r in rows if r.id not in ids)
    n_error = sum(1 for p in preds if p.error is not None or p.response is None)
    md = [f"# Değerlendirme raporu ({len(rows)} reçete)", ""]
    md += [f"Hatalı satır: {n_error} · Tahmini eksik satır: {n_missing}", ""]
    md += ["## Sınıflandırma", "", f"Doğruluk: **{_pct(cls_acc)}**", ""]
    md += ["| Gerçek | Tahmin | Sayı |", "|---|---|---|"]
    for (t, p), c in sorted(confusion.items()):
        md.append(f"| {t} | {p} | {c} |")
    md += ["", "## Alan doğruluğu", ""]
    gbc = fields.get("glasses.bc")
    if gbc:
        md += [
            f"**Gözlük BC:** exact {_pct(gbc['exact'])} / tolerant {_pct(gbc['tolerant'])} ({int(gbc['n'])})",
            "",
        ]
    md += ["| Alan | n | exact | tolerant |", "|---|---|---|---|"]
    for k in sorted(fields):
        v = fields[k]
        md.append(f"| {k} | {int(v['n'])} | {_pct(v['exact'])} | {_pct(v['tolerant'])} |")
    md += ["", "## Kalibrasyon", "",
           "Not: bc/dia karşılaştırması toleranslıdır (±0.2).", "",
           "| Güven aralığı | n | doğruluk |", "|---|---|---|"]
    for b in calib:
        md.append(f"| {b['bin']} | {b['n']} | {_pct(b['accuracy'])} |")
    thr = "-" if aa["threshold"] is None else f"{aa['threshold']:.3f}"
    if aa["threshold"] is None and aa["n"]:
        thr_lines = ["- Hiçbir eşik koşulu sağlamıyor (en yüksek güvenli belge yanlış)",
                     f"- Toplam yanlış: {aa['n_wrong']} / {aa['n']} belge"]
    else:
        thr_lines = [f"- Eşik: {thr}"]
    md += [
        "",
        "## Otomatik onay oranı",
        "",
        *thr_lines,
        f"- Otomatik onay oranı: **{_pct(aa['auto_approve_rate'])}**",
        *([] if aa["threshold"] is None and aa["n"] else
          [f"- Eşik üstündeki yanlış: {aa['wrong_above']} / {aa['n_wrong']} (toplam {aa['n']} belge)"]),
        "",
        "## Gecikme",
        "",
        f"- p50: {lat['p50'] if lat['p50'] is not None else '-'} ms",
        f"- p95: {lat['p95'] if lat['p95'] is not None else '-'} ms",
        "",
    ]
    (out / "report.md").write_text("\n".join(md), encoding="utf-8")

    with (out / "fields.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "n", "exact", "tolerant"])
        for k in sorted(fields):
            v = fields[k]
            w.writerow([k, int(v["n"]), v["exact"], v["tolerant"]])


@app.command()
def compare(
    predictions: list[Path] = typer.Argument(..., metavar="A.jsonl B.jsonl [C.jsonl ...]"),
    manifest: Path = typer.Option(..., "--manifest"),
) -> None:
    """İki veya daha fazla tahmin dosyasının başlık metriklerini yan yana yazdırır."""
    if len(predictions) < 2:
        raise typer.BadParameter("en az iki tahmin dosyası gerekir")
    rows = load_manifest(manifest)
    cols = []
    for path in predictions:
        preds = load_predictions(path)
        cols.append(
            (
                classification(rows, preds)[0],
                field_accuracy(rows, preds),
                auto_approve(rows, preds),
                latency(preds),
            )
        )
    lines = [
        "| Metrik | " + " | ".join(p.name for p in predictions) + " |",
        "|---|" + "---|" * len(predictions),
    ]

    def row(name, fn):
        lines.append(f"| {name} | " + " | ".join(str(fn(c)) for c in cols) + " |")

    row("Sınıflandırma doğruluğu", lambda c: _pct(c[0]))
    for f in ("sph", "cyl", "axis", "add", "bc", "dia", "color"):
        row(f"{f} (tolerant)", lambda c, f=f: _pct((c[1].get(f"all.{f}") or {}).get("tolerant")))
    row("Gözlük BC (tolerant)", lambda c: _pct((c[1].get("glasses.bc") or {}).get("tolerant")))
    row("Otomatik onay oranı", lambda c: _pct(c[2]["auto_approve_rate"]))
    row("Gecikme p50 (ms)", lambda c: "-" if c[3]["p50"] is None else c[3]["p50"])
    row("Gecikme p95 (ms)", lambda c: "-" if c[3]["p95"] is None else c[3]["p95"])
    typer.echo("\n".join(lines))


if __name__ == "__main__":
    app()
