"""
Structured Stage Reports for M1DC Pipeline.

Each pipeline phase writes a JSON report to output_dir/reports/.
The orchestrator consumes these for one-liner console summaries.

Usage:
    from pipeline.diagnostics.stage_report import StageReport, write_stage_report

    report = StageReport(
        stage="terrain_validation",
        stage_number=2,
        status="FAIL",
        inputs={"terrain_obj": "Aachen_3D.obj", "extent": [99.99, 87.47]},
        metrics={"cover_x": 0.087, "cover_y": 0.074, "min_required": 0.60},
        artifacts_created=[],
        fatal_reason="DEM too small vs CityGML",
    )
    path = write_stage_report(report, output_dir)
"""

import json
import datetime
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


@dataclass
class StageReport:
    """Structured report for a single pipeline stage."""

    stage: str                                  # e.g. "terrain_validation"
    stage_number: int                           # e.g. 2
    status: str                                 # "PASS" | "FAIL" | "SKIPPED" | "ERROR"
    inputs: dict = field(default_factory=dict)  # key input paths / values
    metrics: dict = field(default_factory=dict) # measured values
    artifacts_created: list = field(default_factory=list)  # files written
    fatal_reason: Optional[str] = None          # reason for FAIL/ERROR
    warnings: list = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())

    def one_liner(self) -> str:
        """Console-friendly one-line summary."""
        tag = f"[PIPELINE] {self.stage_number:02d}_{self.stage}: {self.status}"
        if self.fatal_reason:
            tag += f" — {self.fatal_reason}"
        if self.artifacts_created:
            tag += f" ({len(self.artifacts_created)} artifacts)"
        return tag


def _safe_serialize(obj: Any) -> Any:
    """Convert non-serializable types for JSON."""
    if isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


def write_stage_report(report: StageReport, output_dir: str | Path) -> Path:
    """
    Write a stage report JSON to output_dir/reports/NN_stage.json.

    Args:
        report: StageReport dataclass
        output_dir: Base output directory

    Returns:
        Path to written JSON file
    """
    reports_dir = Path(output_dir) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{report.stage_number:02d}_{report.stage}.json"
    target = reports_dir / filename

    data = _safe_serialize(asdict(report))
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    return target


def read_stage_report(path: str | Path) -> Optional[StageReport]:
    """Read a stage report JSON back into a StageReport."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return StageReport(**data)
    except Exception:
        return None


def summarize_reports(output_dir: str | Path) -> str:
    """
    Read all stage reports and produce a compact summary.

    Returns:
        Multi-line string with one line per stage.
    """
    reports_dir = Path(output_dir) / "reports"
    if not reports_dir.is_dir():
        return "[PIPELINE] No reports directory found"

    lines = []
    for json_file in sorted(reports_dir.glob("*.json")):
        report = read_stage_report(json_file)
        if report:
            lines.append(report.one_liner())
        else:
            lines.append(f"[PIPELINE] {json_file.name}: UNREADABLE")

    return "\n".join(lines) if lines else "[PIPELINE] No stage reports found"
