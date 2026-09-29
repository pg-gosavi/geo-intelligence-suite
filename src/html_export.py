"""
html_export.py — a single self-contained .html report: scorecard, top
sources, priority content gaps, citation-authenticity, and (when available)
video intelligence. No server, no external assets — all CSS/JS inline so it
opens directly in a browser and can be emailed/shared.
"""
from __future__ import annotations

import html as html_lib
import json
import re


def _esc(s):
    return html_lib.escape(str(s)) if s is not None else ""


def _render_markdown_lite(md: str) -> str:
    """Minimal, dependency-free renderer for the small markdown subset
    executive_summary.py actually produces (# / ## headers, - bullets,
    **bold**, blank-line paragraphs). Not a general markdown parser —
    just enough to display that one artifact inline without adding a
    markdown-parsing dependency."""
    if not md:
        return ""
    lines = md.strip().splitlines()
    out = []
    in_list = False
    for line in lines:
        line = line.rstrip()
        if not line:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        bolded_source = html_lib.escape(line)
        bolded = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", bolded_source)
        if line.startswith("## "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h4>{bolded[3:]}</h4>")
        elif line.startswith("# "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h3>{bolded[2:]}</h3>")
        elif line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{bolded[2:]}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{bolded}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def build_html_report(cfg, gevs_result, citation_intel_rows, content_gaps_result, rank_recs_result,
                        execution_rows: list = None, video_results: dict = None,
                        executive_summary_markdown: str = None, kpi_data: dict = None) -> str:
    brand = cfg.brand
    main = gevs_result["main_table"]
    brand_stats = next((s for s in main if s["entity"] == brand), None)
    leader = main[0] if main else None

    scorecard_rows = "".join(
        f"<tr class='{ 'brand-row' if s['entity']==brand else '' }'>"
        f"<td>{_esc(s['rank'])}</td><td>{_esc(s['entity'])}</td>"
        f"<td>{_esc(s['gevs_overall'])}%</td><td>{_esc(s['gevs_top3'])}%</td>"
        f"<td>{_esc(s['avg_position'])}</td><td>{_esc(s['vs_gap'])}</td>"
        f"<td>{_esc(s['status'])}</td></tr>"
        for s in main
    )

    top_sources = sorted(citation_intel_rows, key=lambda r: r["rank"])[:10]
    sources_rows = "".join(
        f"<tr><td>{_esc(r['rank'])}</td><td>{_esc(r['domain'])}</td>"
        f"<td>{_esc(r['source_quality'])}</td>"
        f"<td>{'✅ LLM-cited' if r.get('llm_cited') else '🔍 Search-evidence only'}</td>"
        f"<td>{_esc(r['total_citations'])}</td></tr>"
        for r in top_sources
    )

    # --- Citation Authenticity: what fraction of "evidence" is real GEO
    # signal (an active LLM actually named the domain) versus SEO signal
    # (found only via the independent search-evidence pass, i.e. what
    # already ranks on Google for this query, not proof any tested model
    # trusts it). This is the single most important honesty check in the
    # whole report when running a non-browsing model (e.g. gpt-oss via
    # Groq), which can never itself produce a real citation URL.
    total_domains = len(citation_intel_rows)
    llm_cited_domains = sum(1 for r in citation_intel_rows if r.get("llm_cited"))
    authenticity_pct = round(llm_cited_domains / total_domains * 100, 1) if total_domains else None

    gap_rows = "".join(
        f"<tr><td>{_esc(g['priority'])}</td><td>{_esc(g['issue'])}</td>"
        f"<td>{_esc(g['content_type'])}</td><td>{_esc(g['timeline'])}</td></tr>"
        for g in content_gaps_result["gap_rows"]
    )

    chart_data = json.dumps([{"name": s["entity"], "value": s["gevs_top3"]} for s in main])

    # --- Optional Video Intelligence section (Agent 4 / YouTube) ---------
    video_section = ""
    if video_results and execution_rows:
        brand_lower = brand.lower()
        video_rows_html = []
        prompts_with_brand_video = 0
        prompts_with_any_video = 0
        for row in execution_rows:
            videos = (video_results.get(row["num"]) or {}).get("videos", [])
            if not videos:
                continue
            prompts_with_any_video += 1
            top = videos[0]
            has_brand = brand_lower in (top.get("title", "") or "").lower()
            if has_brand:
                prompts_with_brand_video += 1
            video_rows_html.append(
                f"<tr><td>{_esc(row['conversational_prompt'])}</td>"
                f"<td><a href='{_esc(top.get('url',''))}' target='_blank' rel='noopener'>{_esc(top.get('title',''))}</a></td>"
                f"<td>{'✅' if has_brand else '—'}</td></tr>"
            )
        if video_rows_html:
            video_presence_pct = round(prompts_with_brand_video / prompts_with_any_video * 100, 1) if prompts_with_any_video else 0
            video_section = f"""
  <section>
    <h2>Video Intelligence (bonus — Agent 4)</h2>
    <p style="color:var(--muted); font-size:13px; margin-top:-6px;">
      Top YouTube result per tested prompt. {brand} appears in the top video's title for
      <strong>{video_presence_pct}%</strong> of prompts with video results ({prompts_with_brand_video}/{prompts_with_any_video}).
    </p>
    <table>
      <thead><tr><th>Prompt</th><th>Top Video</th><th>{_esc(brand)} in Title?</th></tr></thead>
      <tbody>{''.join(video_rows_html)}</tbody>
    </table>
  </section>
"""

    authenticity_card = ""
    if authenticity_pct is not None:
        authenticity_card = f"""
    <div class="card"><div class="label">Real LLM Citation Rate</div>
      <div class="value">{_esc(authenticity_pct)}%</div>
      <div style="font-size:11px; color:var(--muted); margin-top:2px;">{llm_cited_domains}/{total_domains} sources actually cited by a tested LLM (rest: search-evidence only)</div></div>"""

    kpi_cards = ""
    if kpi_data:
        ca = kpi_data.get("citation_accuracy") or {}
        pr = kpi_data.get("prompt_relevance") or {}
        ps = kpi_data.get("processing_speed") or {}
        ca_val = f"{ca.get('accuracy_pct')}%" if ca.get("accuracy_pct") is not None else "—"
        pr_val = f"{pr.get('relevance_pct')}%" if pr.get("relevance_pct") is not None else "not rated"
        ps_val = f"{int(ps.get('total_elapsed_s', 0) // 60)}m {int(ps.get('total_elapsed_s', 0) % 60)}s" if ps.get("total_elapsed_s") else "—"
        kpi_cards = f"""
    <div class="card"><div class="label">Citation Accuracy</div><div class="value">{_esc(ca_val)}</div>
      <div style="font-size:11px; color:var(--muted); margin-top:2px;">target: {_esc(ca.get('target_pct'))}%</div></div>
    <div class="card"><div class="label">Prompt Relevance</div><div class="value">{_esc(pr_val)}</div>
      <div style="font-size:11px; color:var(--muted); margin-top:2px;">{_esc(pr.get('rated_count', 0))}/{_esc(pr.get('total_prompts', 0))} prompts rated</div></div>
    <div class="card"><div class="label">Processing Speed</div><div class="value">{_esc(ps_val)}</div>
      <div style="font-size:11px; color:var(--muted); margin-top:2px;">target: &lt;3 min (free-tier rate limits usually prevent this — see README)</div></div>"""

    executive_summary_section = ""
    if executive_summary_markdown:
        executive_summary_section = f"""
  <section>
    {_render_markdown_lite(executive_summary_markdown)}
  </section>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>GEO Intelligence Report — {_esc(brand)}</title>
<style>
  :root {{ --ink:#1a1d29; --muted:#5b6270; --accent:#3457d5; --bg:#f7f8fb; --card:#ffffff; --border:#e5e7ee; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
          background: var(--bg); color: var(--ink); margin: 0; padding: 0 0 60px; }}
  header {{ background: linear-gradient(135deg,#1a1d29,#3457d5); color: #fff; padding: 36px 24px; }}
  header h1 {{ margin: 0 0 6px; font-size: 26px; }}
  header p {{ margin: 0; opacity: .85; font-size: 14px; }}
  .wrap {{ max-width: 980px; margin: -28px auto 0; padding: 0 24px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(200px,1fr)); gap: 16px; margin-bottom: 28px; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 18px 20px;
           box-shadow: 0 1px 3px rgba(0,0,0,.04); }}
  .card .label {{ font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }}
  .card .value {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
  section {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 22px 24px; margin-bottom: 22px; }}
  section h2 {{ margin-top: 0; font-size: 17px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13.5px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; }}
  tr.brand-row {{ background: #eef1ff; font-weight: 600; }}
  .pill {{ display:inline-block; padding: 2px 10px; border-radius: 999px; background:#eef1ff; color:var(--accent); font-size:12px; }}
  #barChart {{ width: 100%; height: 220px; }}
  footer {{ text-align:center; color: var(--muted); font-size: 12px; margin-top: 30px; }}
</style>
</head>
<body>
<header>
  <h1>GEO Intelligence Report — {_esc(brand)}</h1>
  <p>{_esc(cfg.sector)} · {_esc(cfg.region)} · Run {_esc(cfg.run_id)} · Generated {_esc(cfg.generated)}</p>
</header>
<div class="wrap">

  <div class="cards">
    <div class="card"><div class="label">{_esc(brand)} GEVS (Top-3)</div>
      <div class="value">{_esc(brand_stats['gevs_top3'] if brand_stats else '—')}%</div></div>
    <div class="card"><div class="label">Category Leader</div>
      <div class="value" style="font-size:20px;">{_esc(leader['entity'] if leader else '—')}
        <span class="pill">{_esc(leader['gevs_top3'] if leader else '—')}%</span></div></div>
    <div class="card"><div class="label">Top Referenced Domain</div>
      <div class="value" style="font-size:20px;">{_esc(top_sources[0]['domain'] if top_sources else '—')}</div></div>
    <div class="card"><div class="label">Priority Content Gaps</div>
      <div class="value">{_esc(len(content_gaps_result['gap_rows']))}</div></div>{authenticity_card}{kpi_cards}
  </div>
{executive_summary_section}
  <section>
    <h2>Scorecard — Brand vs Competitors</h2>
    <canvas id="barChart"></canvas>
    <table>
      <thead><tr><th>Rank</th><th>Brand</th><th>GEVS Overall</th><th>GEVS Top-3</th>
        <th>Avg Position</th><th>vs Benchmark</th><th>Status</th></tr></thead>
      <tbody>{scorecard_rows}</tbody>
    </table>
  </section>

  <section>
    <h2>Top Cited Sources</h2>
    <p style="color:var(--muted); font-size:13px; margin-top:-6px;">
      "LLM-cited" means a tested model's own answer named this domain — a real GEO signal.
      "Search-evidence only" means it only showed up in the independent live-search check —
      an SEO signal about what already ranks on Google, not proof any tested model trusts it.
    </p>
    <table>
      <thead><tr><th>Rank</th><th>Domain</th><th>Source Quality</th><th>Evidence Type</th><th>Total Citations</th></tr></thead>
      <tbody>{sources_rows}</tbody>
    </table>
  </section>

  <section>
    <h2>Priority Content Gaps</h2>
    <table>
      <thead><tr><th>Priority</th><th>Issue</th><th>Content Type</th><th>Timeline</th></tr></thead>
      <tbody>{gap_rows}</tbody>
    </table>
  </section>
{video_section}
  <footer>Generated by GEO Intelligence Suite (free-tier build) · Not real-time grounded — verify citations before client delivery.</footer>
</div>

<script>
(function() {{
  var data = {chart_data};
  var canvas = document.getElementById('barChart');
  var ctx = canvas.getContext('2d');
  var dpr = window.devicePixelRatio || 1;
  var w = canvas.clientWidth || 900, h = 220;
  canvas.width = w * dpr; canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  var max = Math.max.apply(null, data.map(function(d){{ return d.value; }}).concat([1]));
  var barW = w / data.length * 0.6, gap = w / data.length;
  ctx.font = '12px -apple-system, sans-serif';
  data.forEach(function(d, i) {{
    var barH = (d.value / max) * (h - 50);
    var x = i * gap + (gap - barW) / 2;
    var y = h - 30 - barH;
    ctx.fillStyle = d.name === {json.dumps(brand)} ? '#3457d5' : '#c7cdea';
    ctx.fillRect(x, y, barW, barH);
    ctx.fillStyle = '#1a1d29';
    ctx.textAlign = 'center';
    ctx.fillText(d.value + '%', x + barW/2, y - 6);
    ctx.fillText(d.name.length > 14 ? d.name.slice(0,13)+'…' : d.name, x + barW/2, h - 12);
  }});
}})();
</script>
</body>
</html>
"""
