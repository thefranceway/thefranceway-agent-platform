"""
Research Platform — HTML Report Renderer
Compiles all 9 project steps into a standalone HTML document.
"""

from datetime import datetime, timezone


VERDICT_STYLE = {
    "yes":          ("Supported",    "#22c55e", "#052e16"),
    "no":           ("Rejected",     "#ef4444", "#2d0a0a"),
    "partial":      ("Partially Supported", "#f59e0b", "#2d1a00"),
    "inconclusive": ("Inconclusive", "#6b7280", "#1a1a1a"),
}


def render_report(project: dict) -> str:
    s       = project["steps"]
    title   = project["title"]
    pid     = project["id"]
    created = project["created_at"][:10]
    now     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rq   = s.get("research_question", {}).get("data", {})
    hyp  = s.get("hypothesis", {}).get("data", {})
    dc   = s.get("data_collection", {}).get("data", {})
    dp   = s.get("data_profile", {}).get("data", {})
    am   = s.get("analysis_method", {}).get("data", {})
    aa   = s.get("agent_analysis", {}).get("data", {})
    fi   = s.get("findings", {}).get("data", {})
    co   = s.get("conclusions", {}).get("data", {})

    verdict_key  = co.get("hypothesis_supported", "inconclusive")
    verdict_label, verdict_color, verdict_bg = VERDICT_STYLE.get(verdict_key, VERDICT_STYLE["inconclusive"])

    findings_html = ""
    raw_findings  = fi.get("key_findings", "")
    if raw_findings:
        items = [f.strip() for f in raw_findings.strip().splitlines() if f.strip()]
        findings_html = "\n".join(f"<li>{item}</li>" for item in items)

    agent_output = aa.get("raw_output", "").strip()
    charts_html  = ""
    for chart_path in aa.get("charts", []):
        charts_html += f'<p style="color:#888;font-size:12px">Chart saved: <code>{chart_path}</code></p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — Research Report</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: Georgia, 'Times New Roman', serif; background: #fafaf8; color: #1a1a1a; line-height: 1.7; }}
  .page {{ max-width: 820px; margin: 0 auto; padding: 60px 40px; }}
  h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 6px; }}
  h2 {{ font-size: 18px; font-weight: 700; margin: 40px 0 12px; padding-bottom: 6px; border-bottom: 2px solid #e5e5e5; color: #111; }}
  h3 {{ font-size: 14px; font-weight: 700; color: #555; margin: 18px 0 6px; text-transform: uppercase; letter-spacing: 0.05em; }}
  p {{ margin-bottom: 12px; color: #333; }}
  .meta {{ color: #888; font-size: 13px; font-family: monospace; margin-bottom: 40px; }}
  .verdict {{ display: inline-block; padding: 6px 16px; border-radius: 20px; font-weight: 700; font-family: monospace; font-size: 14px; color: {verdict_color}; background: {verdict_bg}; border: 1px solid {verdict_color}; margin: 8px 0; }}
  .agent-block {{ background: #111; color: #e5e5e5; font-family: 'Courier New', monospace; font-size: 13px; line-height: 1.6; padding: 20px; border-radius: 6px; white-space: pre-wrap; overflow-x: auto; margin: 12px 0; }}
  .data-block {{ background: #f5f5f0; font-family: monospace; font-size: 12px; padding: 16px; border-radius: 4px; white-space: pre-wrap; overflow-x: auto; max-height: 300px; overflow-y: auto; color: #444; border: 1px solid #e0e0e0; }}
  ul.findings {{ padding-left: 20px; }}
  ul.findings li {{ margin-bottom: 8px; color: #1a1a1a; }}
  .field-row {{ margin-bottom: 16px; }}
  .divider {{ border: none; border-top: 1px solid #e5e5e5; margin: 32px 0; }}
  .footer {{ margin-top: 60px; padding-top: 20px; border-top: 1px solid #e5e5e5; color: #aaa; font-size: 12px; font-family: monospace; }}
  @media print {{
    body {{ background: white; }}
    .agent-block {{ border: 1px solid #ccc; }}
    .data-block {{ max-height: none; }}
  }}
</style>
</head>
<body>
<div class="page">

  <!-- Cover -->
  <h1>{title}</h1>
  <div class="meta">Project ID: {pid} &nbsp;|&nbsp; Created: {created} &nbsp;|&nbsp; Report generated: {now}</div>

  <!-- 1. Research Question -->
  <h2>1. Research Question</h2>
  <p>{rq.get('question', '—')}</p>
  {f'<h3>Background</h3><p>{rq["background"]}</p>' if rq.get("background") else ''}

  <!-- 2. Hypothesis -->
  <h2>2. Hypothesis</h2>
  <div class="field-row">
    <h3>H1 — Hypothesis</h3>
    <p>{hyp.get('hypothesis', '—')}</p>
  </div>
  {f'<div class="field-row"><h3>H0 — Null Hypothesis</h3><p>{hyp["null_hypothesis"]}</p></div>' if hyp.get("null_hypothesis") else ''}

  <!-- 3. Data -->
  <h2>3. Dataset</h2>
  <div class="field-row">
    <h3>Format / Source</h3>
    <p>{dc.get('format', '—').upper()} · {dc.get('source_type', '—')}{' · ' + dc['url'] if dc.get('url') else ''}</p>
    {f'<p>{dc["description"]}</p>' if dc.get('description') else ''}
  </div>
  {f'<h3>Data Preview</h3><div class="data-block">{_escape(dc.get("raw", "")[:1500])}{"..." if len(dc.get("raw",""))>1500 else ""}</div>' if dc.get("raw") else ''}

  <!-- 4. Data Profile -->
  <h2>4. Data Profile</h2>
  <p>{dp.get('summary', '—')}</p>
  {f'<h3>Columns</h3><p><code>{dp["columns"]}</code></p>' if dp.get('columns') else ''}
  {f'<h3>Row Count</h3><p>~{dp["row_count"]}</p>' if dp.get('row_count') else ''}
  {f'<h3>Known Gaps</h3><p>{dp["missing_values"]}</p>' if dp.get('missing_values') else ''}
  {f'<h3>Quality Notes</h3><p>{dp["notes"]}</p>' if dp.get('notes') else ''}

  <!-- 5. Analysis Method -->
  <h2>5. Analysis Method</h2>
  <div class="field-row">
    <h3>Method</h3>
    <p><strong>{am.get('method', '—')}</strong></p>
    <p>{am.get('rationale', '')}</p>
  </div>
  {f'<h3>Variables</h3><p>Independent: <code>{am["independent_var"]}</code> &nbsp;|&nbsp; Dependent: <code>{am["dependent_var"]}</code>{(" | Controls: <code>" + am["controls"] + "</code>") if am.get("controls") else ""}</p>' if am.get('independent_var') or am.get('dependent_var') else ''}

  <!-- 6. Agent Analysis -->
  <h2>6. Agent Analysis</h2>
  {f'<div class="agent-block">{_escape(agent_output)}</div>' if agent_output else '<p style="color:#888">No agent analysis run.</p>'}
  {charts_html}
  {f'<p style="color:#888;font-size:12px;font-family:monospace">Run ID: {aa["run_id"]} &nbsp;|&nbsp; Tool calls: {aa.get("tool_calls","?")} &nbsp;|&nbsp; Iterations: {aa.get("iterations","?")}</p>' if aa.get('run_id') else ''}

  <!-- 7. Findings -->
  <h2>7. Key Findings</h2>
  {f'<ul class="findings">{findings_html}</ul>' if findings_html else '<p style="color:#888">No findings recorded.</p>'}
  {f'<h3>Statistical Results</h3><p>{fi["statistical_results"]}</p>' if fi.get('statistical_results') else ''}
  {f'<h3>Anomalies</h3><p>{fi["anomalies"]}</p>' if fi.get('anomalies') else ''}

  <!-- 8. Conclusions -->
  <h2>8. Conclusions</h2>
  <h3>Hypothesis verdict</h3>
  <div class="verdict">{verdict_label}</div>
  <p style="margin-top:12px">{co.get('conclusion', '—')}</p>
  {f'<h3>Limitations</h3><p>{co["limitations"]}</p>' if co.get('limitations') else ''}
  {f'<h3>Next Steps</h3><p>{co["next_steps"]}</p>' if co.get('next_steps') else ''}

  <div class="footer">
    <p>Generated by Agent Platform Research Module &nbsp;|&nbsp; thefranceway &nbsp;|&nbsp; {now}</p>
    <p>Project: {pid} &nbsp;|&nbsp; Status: {project.get('status','').upper()}</p>
  </div>

</div>
</body>
</html>"""


def _escape(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))
