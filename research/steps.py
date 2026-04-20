"""
Research Protocol — Step Definitions
9-step data analyst workflow from question to final report.
"""

STEPS = [
    {
        "num": 1,
        "key": "research_question",
        "label": "Research Question",
        "icon": "?",
        "description": "Define what you are trying to find out.",
        "ui_hint": "Be specific. A good research question is narrow, answerable, and connected to observable data.",
        "fields": [
            {"key": "question",   "label": "Research question",  "type": "textarea", "required": True,  "placeholder": "e.g. Does sleep duration affect LDL cholesterol in adults over 40?"},
            {"key": "background", "label": "Background context", "type": "textarea", "required": False, "placeholder": "Optional: why does this matter? What prompted the question?"},
        ],
    },
    {
        "num": 2,
        "key": "hypothesis",
        "label": "Hypothesis",
        "icon": "H",
        "description": "State your expected answer and the null alternative.",
        "ui_hint": "Phrase as a directional statement. The null is the default: no effect or no difference.",
        "fields": [
            {"key": "hypothesis",      "label": "Hypothesis (H1)",       "type": "textarea", "required": True,  "placeholder": "e.g. Adults sleeping fewer than 6 hours will show elevated LDL vs. those sleeping 7–9 hours."},
            {"key": "null_hypothesis", "label": "Null hypothesis (H0)",   "type": "textarea", "required": False, "placeholder": "e.g. There is no significant difference in LDL across sleep duration groups."},
        ],
    },
    {
        "num": 3,
        "key": "data_collection",
        "label": "Data Collection",
        "icon": "D",
        "description": "Submit your dataset. Supported: CSV, JSON, plain text, or URL.",
        "ui_hint": "CSV paste is fastest. For URLs the agent will fetch content at analysis time. Max ~200 rows recommended for in-prompt analysis.",
        "fields": [
            {"key": "format",      "label": "Data format", "type": "select",   "required": True,  "options": ["csv", "json", "text", "url"]},
            {"key": "source_type", "label": "Source",      "type": "select",   "required": True,  "options": ["paste", "url"]},
            {"key": "raw",         "label": "Paste data",  "type": "textarea", "required": False, "placeholder": "Paste CSV, JSON, or plain text here…"},
            {"key": "url",         "label": "Data URL",    "type": "text",     "required": False, "placeholder": "https://…"},
            {"key": "description", "label": "What is this data?", "type": "text", "required": False, "placeholder": "e.g. Wearable sleep tracker export + lab results, n=120"},
        ],
        "special": "data_input",
    },
    {
        "num": 4,
        "key": "data_profile",
        "label": "Data Profile",
        "icon": "P",
        "description": "Describe your data structure and quality.",
        "ui_hint": "Fill this based on what you know about your dataset. This context helps the agent interpret the data correctly.",
        "fields": [
            {"key": "summary",        "label": "Dataset summary",              "type": "textarea", "required": True,  "placeholder": "e.g. 120 rows, 8 columns. Participant sleep and blood panel data collected over 3 months."},
            {"key": "columns",        "label": "Key columns (comma-separated)", "type": "text",     "required": False, "placeholder": "e.g. participant_id, age, sleep_hours, ldl_mg_dl"},
            {"key": "row_count",      "label": "Approximate row count",        "type": "number",   "required": False, "placeholder": "e.g. 120"},
            {"key": "missing_values", "label": "Known gaps or missing data",   "type": "textarea", "required": False, "placeholder": "e.g. 8 participants missing LDL readings (excluded)"},
            {"key": "notes",          "label": "Data quality notes",           "type": "textarea", "required": False, "placeholder": "e.g. Self-reported sleep hours — possible recall bias"},
        ],
    },
    {
        "num": 5,
        "key": "analysis_method",
        "label": "Analysis Method",
        "icon": "M",
        "description": "Choose your analytical approach.",
        "ui_hint": "The agent will use your stated method as the primary instruction. Be as specific as you can — the more context, the better the analysis.",
        "fields": [
            {"key": "method",          "label": "Analysis method",           "type": "text",     "required": True,  "placeholder": "e.g. Pearson correlation, linear regression, chi-square, descriptive statistics"},
            {"key": "rationale",       "label": "Why this method?",          "type": "textarea", "required": True,  "placeholder": "e.g. Pearson correlation to measure the linear relationship between continuous variables (sleep hours and LDL)."},
            {"key": "independent_var", "label": "Independent variable(s)",   "type": "text",     "required": False, "placeholder": "e.g. sleep_hours"},
            {"key": "dependent_var",   "label": "Dependent variable(s)",     "type": "text",     "required": False, "placeholder": "e.g. ldl_mg_dl"},
            {"key": "controls",        "label": "Control variables",         "type": "text",     "required": False, "placeholder": "e.g. age, sex, BMI"},
        ],
    },
    {
        "num": 6,
        "key": "agent_analysis",
        "label": "Agent Analysis",
        "icon": "A",
        "description": "DataAnalyticsAgent runs on your data and method.",
        "ui_hint": "Click Run Analysis. The agent receives your research question, data, and method — and returns statistical findings. This requires Anthropic API credits.",
        "fields": [],
        "special": "run_analysis",
    },
    {
        "num": 7,
        "key": "findings",
        "label": "Findings",
        "icon": "F",
        "description": "Document what the analysis revealed.",
        "ui_hint": "Pull from the agent output above. These are your findings — edit, reframe, and annotate as needed.",
        "fields": [
            {"key": "key_findings",        "label": "Key findings (one per line)",       "type": "textarea", "required": True,  "placeholder": "e.g.\nSleep duration negatively correlates with LDL (r = -0.42, p < 0.01)\nEffect strongest in 45–55 age group"},
            {"key": "statistical_results", "label": "Statistical results",               "type": "textarea", "required": False, "placeholder": "e.g. r = -0.42, p = 0.003, n = 112"},
            {"key": "anomalies",           "label": "Anomalies or unexpected results",   "type": "textarea", "required": False, "placeholder": "e.g. Participants sleeping >9 hours also showed elevated LDL — unexpected."},
        ],
    },
    {
        "num": 8,
        "key": "conclusions",
        "label": "Conclusions",
        "icon": "C",
        "description": "Interpret your findings in the context of your hypothesis.",
        "ui_hint": "Be honest about what the data can and cannot support. Correlation ≠ causation.",
        "fields": [
            {"key": "conclusion",           "label": "Conclusion",             "type": "textarea", "required": True,  "placeholder": "e.g. Data supports a moderate negative correlation between sleep duration and LDL in adults over 40."},
            {"key": "hypothesis_supported", "label": "Hypothesis supported?",  "type": "select",   "required": True,  "options": ["yes", "no", "partial", "inconclusive"]},
            {"key": "limitations",          "label": "Limitations",            "type": "textarea", "required": False, "placeholder": "e.g. Self-reported sleep, no causal mechanism confirmed, sample size limited."},
            {"key": "next_steps",           "label": "Recommended next steps", "type": "textarea", "required": False, "placeholder": "e.g. Replicate with actigraphy data. Control for medication use."},
        ],
    },
    {
        "num": 9,
        "key": "final_report",
        "label": "Final Report",
        "icon": "R",
        "description": "Generate and export your complete research report.",
        "ui_hint": "Compiles all nine steps into a standalone HTML report. Download it, share it, or archive it.",
        "fields": [],
        "special": "generate_report",
    },
]

STEP_BY_KEY = {s["key"]: s for s in STEPS}
STEP_BY_NUM = {s["num"]: s for s in STEPS}
STEP_KEYS   = [s["key"] for s in STEPS]


def build_analysis_prompt(project: dict) -> str:
    """Assemble the DataAnalyticsAgent prompt from project step data."""
    s = project["steps"]

    rq   = s.get("research_question", {}).get("data", {})
    hyp  = s.get("hypothesis", {}).get("data", {})
    dc   = s.get("data_collection", {}).get("data", {})
    dp   = s.get("data_profile", {}).get("data", {})
    am   = s.get("analysis_method", {}).get("data", {})

    raw_data = dc.get("raw", "") or ""
    if len(raw_data) > 4000:
        # Truncate to first ~200 rows to keep prompt manageable
        lines = raw_data.splitlines()
        raw_data = "\n".join(lines[:201]) + f"\n... [{len(lines) - 201} more rows truncated]"

    parts = [
        f"RESEARCH QUESTION:\n{rq.get('question', '(not set)')}",
    ]
    if rq.get("background"):
        parts.append(f"BACKGROUND:\n{rq['background']}")
    parts.append(f"\nHYPOTHESIS (H1):\n{hyp.get('hypothesis', '(not set)')}")
    if hyp.get("null_hypothesis"):
        parts.append(f"NULL HYPOTHESIS (H0):\n{hyp['null_hypothesis']}")

    fmt = dc.get("format", "unknown")
    if dc.get("url"):
        parts.append(f"\nDATA SOURCE (URL — fetch before analysis):\n{dc['url']}")
    elif raw_data:
        parts.append(f"\nDATA ({fmt.upper()}):\n{raw_data}")

    if dp.get("summary"):
        parts.append(f"\nDATASET PROFILE:\n{dp['summary']}")
    if dp.get("columns"):
        parts.append(f"Columns: {dp['columns']}")
    if dp.get("row_count"):
        parts.append(f"Rows: ~{dp['row_count']}")
    if dp.get("missing_values"):
        parts.append(f"Known gaps: {dp['missing_values']}")
    if dp.get("notes"):
        parts.append(f"Quality notes: {dp['notes']}")

    parts.append(f"\nANALYSIS METHOD:\n{am.get('method', '(not set)')}")
    if am.get("rationale"):
        parts.append(f"Rationale: {am['rationale']}")
    if am.get("independent_var"):
        parts.append(f"Independent variable(s): {am['independent_var']}")
    if am.get("dependent_var"):
        parts.append(f"Dependent variable(s): {am['dependent_var']}")
    if am.get("controls"):
        parts.append(f"Controls: {am['controls']}")

    parts.append(
        "\nTASK:\n"
        f"Perform a {am.get('method', 'descriptive')} analysis on this data.\n"
        "Produce:\n"
        "1. Statistical summary (key numbers, distributions, central tendency)\n"
        "2. Answer to the research question with supporting evidence\n"
        "3. Key findings (3–7 bullet points)\n"
        "4. Any notable patterns, correlations, or anomalies\n"
        "5. If you can generate a chart, save it to /tmp/ and include the path\n"
        "Be concise and precise. Numbers before narrative."
    )

    return "\n\n".join(parts)
