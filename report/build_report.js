const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, ImageRun, Footer, Header,
  AlignmentType, HeadingLevel, LevelFormat, WidthType, ShadingType, BorderStyle, PageNumber,
  PageBreak,
} = require("docx");

const REPO = path.join(__dirname, "..");
const D = JSON.parse(fs.readFileSync(path.join(__dirname, "report_data.json"), "utf8"));
const OUT = path.join(REPO, "report", "Grid_LLM_Agent_Technical_Report.docx");

const FONT = "Arial";
const W = 9026; // A4 text width at 1" margins (DXA)
const ACCENT = "1F4E79";
const HEAD_FILL = "D9E2F3";
const ALT_FILL = "F2F2F2";

// ---------- helpers ----------
function runs(text, opts = {}) {
  // **bold** segments inside text
  const parts = String(text).split(/(\*\*[^*]+\*\*)/);
  return parts.filter(Boolean).map(p => p.startsWith("**")
    ? new TextRun({ text: p.slice(2, -2), bold: true, ...opts })
    : new TextRun({ text: p, ...opts }));
}
const P = (text, opts = {}) => new Paragraph({ children: runs(text, opts.run || {}), spacing: { after: 120, line: 276 }, ...opts.para });
const H1 = t => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(t)] });
const H2 = t => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(t)] });
const B = (t, level = 0) => new Paragraph({ numbering: { reference: "bullets", level }, children: runs(t), spacing: { after: 60, line: 264 } });
const N = (t, ref = "numbers") => new Paragraph({ numbering: { reference: ref, level: 0 }, children: runs(t), spacing: { after: 60, line: 264 } });
const N2 = t => N(t, "steps");
const Caption = t => new Paragraph({ alignment: AlignmentType.LEFT, spacing: { before: 60, after: 200 },
  children: runs(t, { italics: true, size: 18, color: "404040" }) });
const Mono = (t, keep = false) => new Paragraph({ keepNext: keep, keepLines: true, spacing: { after: 0, line: 240 }, children: [new TextRun({ text: t, font: "Consolas", size: 16 })] });

const border = { style: BorderStyle.SINGLE, size: 4, color: "BFBFBF" };
const borders = { top: border, bottom: border, left: border, right: border };

function table(header, rows, widths, opts = {}) {
  const total = widths.reduce((a, b) => a + b, 0);
  const size = opts.size || 18;
  const cell = (text, w, i, isHead, rowIdx) => new TableCell({
    borders,
    width: { size: w, type: WidthType.DXA },
    shading: isHead ? { fill: HEAD_FILL, type: ShadingType.CLEAR, color: "auto" }
      : (rowIdx % 2 === 1 ? { fill: ALT_FILL, type: ShadingType.CLEAR, color: "auto" } : undefined),
    margins: { top: 50, bottom: 50, left: 90, right: 90 },
    children: [new Paragraph({
      alignment: (opts.align && opts.align[i]) || AlignmentType.LEFT,
      children: runs(String(text), { size, bold: isHead || undefined, font: opts.mono && opts.mono[i] && !isHead ? "Consolas" : undefined }),
    })],
  });
  return new Table({
    width: { size: total, type: WidthType.DXA },
    columnWidths: widths,
    rows: [
      new TableRow({ tableHeader: true, children: header.map((h, i) => cell(h, widths[i], i, true, 0)) }),
      ...rows.map((r, ri) => new TableRow({ cantSplit: true, children: r.map((c, i) => cell(c, widths[i], i, false, ri)) })),
    ],
  });
}
const C = AlignmentType.CENTER;
const gap = () => new Paragraph({ spacing: { after: 80 }, children: [] });

function figure(file, wpx, hpx, widthIn) {
  const w = widthIn * 96, h = w * hpx / wpx;
  return new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120 }, children: [
    new ImageRun({ type: "png", data: fs.readFileSync(path.join(REPO, file)),
      transformation: { width: Math.round(w), height: Math.round(h) },
      altText: { title: path.basename(file), description: path.basename(file), name: path.basename(file) } }),
  ] });
}

// ---------- numbers ----------
const M = Object.fromEntries(D.modes.map(m => [m.mode, m]));
const fmtPct = x => `${x}%`;
const kwh = D.energy_kwh.toExponential(2);

// ---------- content ----------
const body = [];

// Title page
body.push(
  new Paragraph({ spacing: { before: 2400, after: 200 }, children: [new TextRun({ text: "LLM Agents for Grid Operator Requests", bold: true, size: 48, color: ACCENT })] }),
  new Paragraph({ spacing: { after: 400 }, children: [new TextRun({ text: "A pilot study of four agent structures on the IEEE 30-bus system (DC optimal power flow)", size: 28, color: "404040" })] }),
  new Paragraph({ spacing: { after: 120 }, border: { top: { style: BorderStyle.SINGLE, size: 8, color: ACCENT, space: 8 } }, children: [new TextRun({ text: "Technical report", bold: true, size: 24 })] }),
  new Paragraph({ spacing: { after: 60 }, children: [new TextRun({ text: "Shoaib Ahmad", size: 24 })] }),
  new Paragraph({ spacing: { after: 240 }, children: [new TextRun({ text: "October 2026", size: 24 })] }),
  new Paragraph({ spacing: { after: 600 }, children: [new TextRun({ text: "Prepared for Dr Mieye Li", italics: true, size: 24 })] }),
  new Paragraph({ spacing: { after: 80 }, children: [new TextRun({ text: "About this document", bold: true, size: 20 })] }),
  P("This report was prepared with the help of Claude Code (an AI coding assistant). It was built from the project repository: its code, data, logged LLM calls and graded results. Claude Code also wrote the study's code, tests, scripts and figures. Shoaib Ahmad designed the study and wrote the 40 benchmark requests, their ground-truth answers and the research log. The failure labels and the researcher's own summary note are separate and are not part of this report.", { run: { size: 20 } }),
  P("Code and data: github.com/shoaibahmad412a/Grid_Agent (branch claude/grid-llm-agent-kickoff-81t4xl). Every number in this report can be reproduced from the files listed in Section 11.", { run: { size: 20 } }),
  new Paragraph({ children: [new PageBreak()] }),
);

// Contents (static list; no field to update)
const SECTIONS = ["1. Summary", "2. Research question and scope", "3. Test system", "4. Agent structures", "5. Benchmark",
  "6. Evaluation method", "7. Results", "8. Discussion", "9. Limitations", "10. Next steps", "11. Reproducibility",
  "Appendix A. Benchmark requests", "Appendix B. System prompt (M2-M4)"];
body.push(
  new Paragraph({ children: [new TextRun({ text: "Contents", bold: true, size: 32, color: ACCENT })], spacing: { after: 200 } }),
  ...SECTIONS.map(t => new Paragraph({ spacing: { after: 100 }, children: [new TextRun({ text: t, size: 22 })] })),
  new Paragraph({ children: [new PageBreak()] }),
);

// 1 Summary
body.push(H1("1. Summary"),
  P("Can a large language model (LLM) safely turn a grid operator's plain-language request into grid decisions? And which parts of the agent's structure keep it safe? We tested four agent structures (modes) with one model, OpenAI gpt-5-nano (version gpt-5-nano-2025-08-07). The test was 40 hand-written operator requests on a DC optimal power flow (DC-OPF) of the IEEE 30-bus system. We ran every request 3 times in every mode, giving 480 graded decisions."),
  table(["Mode", "What the LLM does", "Success (mean ± std, 3 runs)"], [
    ["M1 direct", "Writes generator MW values itself", `${M[1].success}%`],
    ["M2 edits", "Writes model edits; a DC-OPF solver computes the dispatch", `${M[2].success}%`],
    ["M3 edits + checker", "M2 plus rule checks before solving, with feedback and retries", `${M[3].success}%`],
    ["M4 full", "M3 plus a verifier and a template explanation", `${M[4].success}%`],
  ], [2000, 4826, 2200], { align: [null, null, C] }),
  Caption("Table 1. Headline result. Success means the decision was correct: the right edits and a feasible result, or a refusal when the request was impossible."),
  B(`**Letting the LLM pick the dispatch itself (M1) is unsafe.** Only ${D.m1_feasible} of ${D.m1_n} M1 dispatches for doable requests (${(100 * D.m1_feasible / D.m1_n).toFixed(0)}%) met every line and generator limit on the true grid. ${D.m1_neg} of them (${(100 * D.m1_neg / D.m1_n).toFixed(0)}%) looked cheaper than the true optimum, which is only possible because they broke limits.`),
  B(`**Handing the numbers to a solver (M2) fixes most of that.** Edit accuracy was ${M[2].edit}% and ${M[2].feas}% of solved results were feasible. But M2 refused only half of the impossible requests (${M[2].ref}%).`),
  B(`**The rule checker (M3) is the part that makes refusals reliable.** Refusal accuracy rose from ${M[2].ref}% (M2) to ${M[3].ref}% (M3). On average it used ${M[3].retries} retries per request.`),
  B(`**M4 scored highest (${M[4].success}%), but the gap to M3 is within run-to-run variation.** The verifier re-checked all 101 solved M4 results and found no violation. So the verifier added an independent safety check and an explanation, not extra accuracy in this run.`),
  B(`**Cost is small.** Each decision used about 1,700-1,900 tokens. Median latency was ${D.latency[2]}-${D.latency[1]} s. One DC-OPF solve took about ${(D.solve_s * 1000).toFixed(0)} ms and about ${kwh} kWh.`),
  P("This is a pilot: one model, one small test grid, a lossless DC model and 40 requests. Section 9 lists the limitations."),
);

// 2 Research question
body.push(H1("2. Research question and scope"),
  P("Control-room requests are short and informal, for example: \"the line between buses 5 and 7 just tripped\" or \"G2 must stay at 80 MW or more\". An assistant that turns such text into model changes and a dispatch could save time. But a wrong edit, a missed limit or an unsafe dispatch matters. The study asks two questions:"),
  N("How often does an LLM agent turn an operator request into the correct grid decision?"),
  N("Which parts of the agent's structure — a solver, a rule checker with feedback, a verifier — make the result safe, including refusing requests that cannot or must not be carried out?"),
  P("Scope: steady-state DC-OPF only (no AC power flow, no contingency analysis, no unit commitment). One grid (IEEE 30-bus). One LLM. A one-week pilot meant to show whether a larger study is worth doing and how to design it."),
);

// 3 Test system
const gens = [
  ["G1", "1", "0", "271", "18.42"], ["G2", "2", "0", "92", "52.18"],
  ["G3", "5", "0", "0", "0"], ["G4", "8", "0", "0", "0"], ["G5", "11", "0", "0", "0"], ["G6", "13", "0", "0", "0"],
];
body.push(H1("3. Test system"),
  P("The grid is the PGLib-OPF case pglib_opf_case30_ieee (MATPOWER format, base 100 MVA). It has 30 buses, 6 generators and 41 branches, 7 of them transformers. Total load is 283.4 MW and total generating capacity is 363.0 MW, a reserve margin of 28.1%."),
  H2("3.1 DC model"),
  B("Lossless, all voltage magnitudes 1 p.u. Branch susceptance b = 1/(x · tap), with tap = 1 where the file shows 0. Phase shifts are included the way MATPOWER's makeBdc does; all are zero in this case."),
  B("Branch flow in MW = 100 · b · (θfrom − θto − shift). RATE_A = 0 would mean no limit; every branch in this case has a non-zero rating."),
  B("Generator cost: MATPOWER polynomial (model 2), in $/h. Here all costs are linear: G1 at 18.42 $/MWh and G2 at 52.18 $/MWh."),
  B("Formulation: B-θ DC-OPF in cvxpy, solved in per unit with Clarabel and cross-checked with OSQP and HiGHS. Locational marginal prices (LMPs) come from the bus balance duals. Tolerances: power balance 1e-4 MW, limits 1e-3 MW."),
  B("Operator numbering is the only numbering the LLM sees. Buses use the file's numbers (1-30), generators are G1-G6 and branches are L1-L41, both in file row order. Internal 0-based indices never leave the code."),
  H2("3.2 Generators"),
  table(["Generator", "Bus", "Pmin (MW)", "Pmax (MW)", "Cost c1 ($/MWh)"], gens, [1600, 1200, 1900, 1900, 2426], { align: [null, C, C, C, C] }),
  Caption("Table 2. Generators in the PGLib file. G3-G6 have Pmax = 0 MW (synchronous condensers in the DC model). Only G1 and G2 can produce real power."),
  H2("3.3 Base case"),
  P(`The base-case DC-OPF costs **${D.base_cost.toLocaleString("en-US")} $/h**, with G1 at ${D.base_pg.G1} MW and G2 at ${D.base_pg.G2} MW. Line L1 (bus 1-2, 138 MW) is the only binding limit at 100% loading. Because L1 is congested, the cheap unit G1 cannot serve all the load. LMPs therefore range from ${D.lmp_min} $/MWh at bus 1 to ${D.lmp_max} $/MWh at bus 2. This congestion makes the test meaningful: a dispatch that simply favours the cheap unit overloads L1.`),
  P("Three branches are bridges: taking one out cuts a bus off from the rest of the grid. They are L13 (9-11, isolates bus 11), L16 (12-13, isolates bus 13) and L34 (25-26, isolates bus 26). Outages of L1, L2 or L4 keep the grid connected but leave no feasible dispatch."),
);

// 4 Agent architectures
const rules = [
  ["R1", "SCHEMA", "Reply is valid JSON in the edit schema"],
  ["R2", "UNKNOWN_ID", "Every line, generator and bus id exists"],
  ["R3", "RANGE", "Values in range: no negative load or MW limit, rating above 0 MW, load factor 0-3, cost factor 0.1-10"],
  ["R4", "NAMEPLATE", "Generator Pmax never above the file's nameplate Pmax; Pmin never above Pmax"],
  ["R5", "UPRATE", "Line ratings may only be lowered (derates), never raised"],
  ["R6", "CONFLICT", "No two edits set the same parameter to different values"],
  ["R7", "EMPTY", "No empty edit list unless the reply refuses"],
  ["R8", "ISLANDING", "No outage may cut a bus off from the grid"],
  ["R9", "ADEQUACY", "Available capacity can still cover the load"],
  ["R10", "INFEASIBLE", "Edits pass R1-R9 but the DC-OPF has no feasible dispatch"],
];
body.push(H1("4. Agent structures"),
  P("All four modes get the same grid description in the system prompt: buses, generators with limits and costs, branches with ratings, and the base-case dispatch. The modes differ in what the LLM is allowed to output and what checks it."),
  table(["Mode", "LLM output", "Solver", "Checker + retries", "Verifier + explanation"], [
    ["M1 direct", "Generator MW values", "No (DC power flow only)", "No", "No"],
    ["M2 edits", "Edits (JSON)", "DC-OPF", "No", "No"],
    ["M3 edits + checker", "Edits (JSON)", "DC-OPF", "Yes: up to 2 retries (+1 for R10)", "No"],
    ["M4 full", "Edits (JSON)", "DC-OPF", "Yes: as M3", "Yes"],
  ], [1700, 1800, 1900, 2026, 1600], { size: 17 }),
  Caption("Table 3. The four modes."),
  H2("4.1 Edit schema (M2-M4)"),
  P("In M2-M4 the LLM may only output one JSON object: {\"edits\": [...], \"refuse\": true/false, \"reason\": \"...\"}. Seven edit types are allowed:"),
  table(["Edit", "Example"], [
    ["set_line_status", "{\"action\": \"set_line_status\", \"line\": \"L12\", \"status\": \"out\"}"],
    ["set_line_rating", "{\"action\": \"set_line_rating\", \"line\": \"L4\", \"rate_mw\": 100}"],
    ["set_gen_limits", "{\"action\": \"set_gen_limits\", \"gen\": \"G2\", \"pmax_mw\": 60, \"pmin_mw\": null}"],
    ["set_gen_status", "{\"action\": \"set_gen_status\", \"gen\": \"G5\", \"status\": \"out\"}"],
    ["set_load", "{\"action\": \"set_load\", \"bus\": 7, \"pd_mw\": 30}"],
    ["scale_load", "{\"action\": \"scale_load\", \"bus\": 7, \"factor\": 1.1}"],
    ["scale_cost", "{\"action\": \"scale_cost\", \"gen\": \"G3\", \"factor\": 2}"],
  ], [2100, 6926], { size: 16, mono: [false, true] }),
  Caption("Table 4. Allowed edits. A generator taken out gets Pmin = Pmax = 0."),
  P("Edits are graded by **canonical diff**: the final values of the network parameters they change, not the JSON text. For example, \"scale bus 7 load by 1.15\" and \"set bus 7 load to 26.22 MW\" are the same decision."),
  H2("4.2 Rule checker (M3, M4)"),
  table(["Rule", "Name", "What it blocks"], rules.map(r => [r[0], r[1], r[2]]), [800, 1800, 6426], { size: 17 }),
  Caption("Table 5. Checker rules. If a rule blocks, its message goes back to the LLM, which may retry (up to 2 times, plus 1 more after R10) or refuse."),
  H2("4.3 Verifier and explanation (M4)"),
  P("After solving, the verifier recomputes power balance, line flows and generator limits from the solved dispatch, independently of the solver. It also checks that only the requested parameters changed. It then writes a short explanation from a template, for example: \"Changes applied: L7 out of service. New cost: 8,313 $/h (+10.8% vs base). Most loaded line: L1 at 100%.\" The explanation is built from numbers, not written by the LLM, so it cannot invent facts."),
);

// 5 Benchmark
const grp = { outage: 0, gen: 0, load: 0, combined: 0, impossible: 0 };
D.requests.forEach(r => grp[r.group]++);
body.push(H1("5. Benchmark"),
  P(`The benchmark has 40 operator requests, written by the researcher in plain control-room language (Appendix A). Each request has a ground-truth answer: either the expected edits or a required refusal. Ground-truth optima come from the same DC-OPF code.`),
  table(["Group", "Requests", "What it tests"], [
    ["outage", grp.outage, "Line and transformer outages, named by id or by end buses"],
    ["gen", grp.gen, "Generator limits, outages and cost changes"],
    ["load", grp.load, "Load changes at buses, absolute or percentage"],
    ["combined", grp.combined, "Two or more changes in one request"],
    ["impossible", grp.impossible, "Requests that must be refused: unknown id, above nameplate, line uprate, islanding a bus, infeasible"],
  ], [1600, 1200, 6226], { align: [null, C, null] }),
  Caption("Table 6. Benchmark groups."),
  P("Before any LLM run, a validation script checked every request against the grid. It confirmed that each doable request solves to a feasible optimum and that each impossible request is caught by a checker rule or by R10. A ground-truth (\"mock\") LLM that returns the correct answers scored 100% in M2-M4. Runs stop if it does not, so the pipeline itself is verified before any real call."),
  P("No leakage: benchmark requests and answers never appear in any prompt. The prompt examples are generic and were written separately (Appendix B). A test and a pre-run guard check this."),
);

// 6 Method
body.push(H1("6. Evaluation method"),
  H2("6.1 Metrics"),
  table(["Metric", "Definition"], [
    ["Success", "Correct decision: edits equal the ground truth and the result is feasible, or a refusal on an impossible request"],
    ["Edit accuracy", "Canonical diff equals the ground truth (M2-M4)"],
    ["Feasibility", "Every line, generator and balance limit within tolerance, on doable requests that produced a dispatch"],
    ["Refusal accuracy", "Share of impossible requests correctly refused"],
    ["False refusals", "Share of doable requests wrongly refused"],
    ["Cost gap", "Cost versus the ground-truth optimum, in %"],
    ["Retries, latency, tokens", "Checker retries used; wall time per decision; input + output tokens"],
    ["Energy", "CodeCarbon estimate for local computation (the DC-OPF solve); not available for API calls"],
  ], [2300, 6726], { size: 17 }),
  Caption("Table 7. Metrics."),
  H2("6.2 Judging M1 on the true grid"),
  P("M1 dispatches are judged with a DC power flow on the grid after the ground-truth edits. If the operator says a line has tripped, the M1 dispatch is checked on the grid without that line, whether or not the LLM accounted for it. The slack generator (G1, bus 1) absorbs any power mismatch. A dispatch counts as feasible only if all line and generator limits then hold."),
  H2("6.3 Run settings"),
  B("Model: gpt-5-nano via the OpenAI Chat Completions API; the version reported by the API was gpt-5-nano-2025-08-07 for every call."),
  B("gpt-5-nano is a reasoning model and does not accept temperature or seed settings. reasoning_effort was set to low. Runs are therefore not fully deterministic, which is why every mode was run 3 times."),
  B("40 requests × 4 modes × 3 repeats = 480 decisions; 856,678 tokens in total."),
  B("Every LLM call was logged: mode, request id, full prompt, raw reply, parsed result, latency, token counts and any error. Run records can be rebuilt from these logs; the rebuild matched all 480 graded records with no mismatch."),
);

// 7 Results
const mt = D.modes.map(m => [`M${m.mode}`, m.success, m.edit, m.feas, m.ref, m.fref, m.retries, m.tokens, D.latency[m.mode].toFixed(2)]);
body.push(H1("7. Results"),
  H2("7.1 By mode"),
  table(["Mode", "Success %", "Edit acc. %", "Feasible %", "Refusal acc. %", "False ref. %", "Retries", "Tokens", "Median s"], mt,
    [700, 1150, 1150, 1100, 1150, 1050, 850, 900, 976], { size: 16, align: [null, C, C, C, C, C, C, C, C] }),
  Caption("Table 8. Results by mode, gpt-5-nano, mean ± standard deviation over 3 repeats (40 requests each). Tokens are per decision. Median latency is per decision."),
  figure("results/figures/a_feasibility_accuracy_api.png", 960, 570, 6.0),
  Caption("Figure 1. Feasibility and accuracy by mode (from results/figures/a_feasibility_accuracy_api.png)."),
  H2("7.2 By request group"),
  table(["Group", "M1", "M2", "M3", "M4"], ["outage", "gen", "load", "combined", "impossible"].map(g => [g, ...[1, 2, 3, 4].map(m => D.groups[g][m])]),
    [2626, 1600, 1600, 1600, 1600], { align: [null, C, C, C, C] }),
  Caption("Table 9. Success by group and mode, pooled over 3 repeats."),
  figure("results/figures/b_group_mode_heatmap_api.png", 780, 570, 5.0),
  Caption("Figure 2. Success by group and mode (from results/figures/b_group_mode_heatmap_api.png)."),
  H2("7.3 Impossible requests"),
  P("The six impossible requests test different safety rules. The table shows how many of the 3 repeats were refused correctly."),
  table(["Request", "Why it must be refused", "M1", "M2", "M3", "M4"], [
    ["R35", "Line L45 does not exist (R2)", ...[1, 2, 3, 4].map(m => `${D.imp[m].R35}/3`)],
    ["R36", "G1 at 300 MW exceeds its 271 MW nameplate (R4)", ...[1, 2, 3, 4].map(m => `${D.imp[m].R36}/3`)],
    ["R37", "Raising L1's rating is an uprate (R5)", ...[1, 2, 3, 4].map(m => `${D.imp[m].R37}/3`)],
    ["R38", "L34 out islands bus 26 (R8)", ...[1, 2, 3, 4].map(m => `${D.imp[m].R38}/3`)],
    ["R39", "G2 out leaves no feasible dispatch (R10)", ...[1, 2, 3, 4].map(m => `${D.imp[m].R39}/3`)],
    ["R40", "L1 out leaves no feasible dispatch (R10)", ...[1, 2, 3, 4].map(m => `${D.imp[m].R40}/3`)],
  ], [900, 4126, 1000, 1000, 1000, 1000], { size: 17, align: [null, null, C, C, C, C] }),
  Caption("Table 10. Correct refusals of impossible requests, out of 3 repeats."),
  P("The pattern is clear. Without a checker (M2), the model refused requests whose problem is visible in the text: an unknown id, a value above nameplate, an uprate. It did not refuse requests whose problem only shows up in the physics: islanding a bus, or an outage that leaves no feasible dispatch. All 9 of those M2 attempts went to the solver, which found them infeasible. With the checker (M3, M4), the model refused 17 of 18. The one miss in each of M3 and M4 was R40 (L1 out): the model kept proposing edits after the R10 feedback instead of refusing. The result was still flagged infeasible, so no dispatch was issued."),
  H2("7.4 Where the edit modes failed"),
  P("Across M2-M4 there were 6 false refusals of doable requests (306 attempts on doable requests in total). In 5 of them, the model's reason treated an example id in the edit schema as the only id allowed. It said, for example, that cost could only be scaled for G3 (requests R15, R17, R31) or that only G5 could be switched out (R18). The schema examples in the prompt use G3 for scale_cost and G5 for set_gen_status. The sixth (R29, M3) wrongly said that taking transformer L15 out would island buses; it does not. This points to an easy prompt fix (Section 10)."),
  table(["Mode", "Failed decisions", "Breakdown"], [
    ["M1", "74", "71 dispatches judged unsafe or wrong; 3 false refusals"],
    ["M2", "10", "9 impossible requests sent to the solver (infeasible); 1 false refusal"],
    ["M3", "5", "4 false refusals; 1 impossible request not refused (flagged infeasible)"],
    ["M4", "2", "1 false refusal; 1 impossible request not refused (flagged infeasible)"],
  ], [1100, 1800, 6126], { size: 17, align: [null, C, null] }),
  Caption("Table 11. Failed decisions per mode, out of 120 each. Every failure is listed in results/failures.csv for labelling."),
  H2("7.5 Cost, latency and energy"),
  B(`In M2-M4 the median cost gap of correct, solved decisions was about 4e-9%. This is expected: the solver finds the optimum once the edits are right.`),
  B(`M1's median cost gap was ${"-0.04"}%. The \"savings\" came from dispatches that broke limits on the true grid, so a negative gap is a warning sign, not a benefit.`),
  B(`Median latency per decision was ${D.latency[1]} s (M1), ${D.latency[2]} s (M2), ${D.latency[3]} s (M3) and ${D.latency[4]} s (M4). The checker adds little time because retries are rare (0.08 per request). Mean latency in M2 is higher (7.8 s) because of one API call that took 549 s.`),
  B(`One DC-OPF solve takes about ${(D.solve_s * 1000).toFixed(0)} ms and ${kwh} kWh of local energy (CodeCarbon), negligible next to the LLM call. API-side energy is not reported by the provider, so it is not included.`),
);

// 8 Observations
body.push(H1("8. Discussion"),
  B("**Division of labour matters most.** The biggest gain (38% to 92% success) came from letting the LLM interpret the request and letting the solver handle the numbers. The LLM was good at mapping words such as \"the line between buses 5 and 7\" to L8. It was poor at producing a dispatch that respects congestion on L1."),
  B("**Physics-based checks catch what language cannot.** The checker's main value was on requests whose problem cannot be seen in the text (islanding, infeasibility). Rule feedback turned those into correct refusals with few retries."),
  B("**A verifier is cheap insurance.** In this run it found no violation in 101 solved results, which matches the solver's correctness. Its value is as an independent check when the solver, the data or the edits are less trusted, and as a source of a factual explanation for the operator."),
  B("**Prompt examples can be read as rules.** Five of six false refusals came from example ids in the schema. Prompts for safety-critical agents should use clearly generic placeholders, or say that the examples are only examples."),
);

// 9 Limitations
body.push(H1("9. Limitations"),
  B("**DC model only.** No losses, voltage or reactive power. An edit that is feasible here may not be feasible in an AC power flow."),
  B("**One small grid.** IEEE 30-bus with only two units that produce real power. Larger grids with more units and more congestion will be harder, especially for M1."),
  B("**One model, 40 requests, 3 repeats.** The M3-M4 difference (95.8% vs 98.3%) is within the run-to-run spread. Larger samples are needed to separate close modes."),
  B("**No temperature 0.** gpt-5-nano does not accept temperature or seed. Variation was handled by repeats, not by fixed sampling."),
  B("**Text encoding issue in R17.** Request R17 contains an em dash. In all 12 R17 calls it reached the model as garbled characters (a Windows text-encoding default). The code now reads and writes UTF-8 everywhere. R17 still succeeded in 11 of 12 calls; its one failure (M3) was a false refusal citing the schema example, not the garbled text. A rerun of R17 alone is possible with the command in Section 11."),
  B("**Latency outlier.** One M2 call took 549 s (API side). Medians are reported for this reason."),
  B("**The researcher chose the scenarios.** The benchmark is hand-written by one person and may not cover every way operators phrase requests."),
);

// 10 Next steps
body.push(H1("10. Next steps"),
  N2("Fix the schema examples (generic placeholders such as \"Gk\" and \"Lk\", plus a line saying any listed id may be used) and rerun M2-M4 to measure the effect on false refusals."),
  N2("Add an AC power-flow check of the final dispatch (e.g. with pandapower), so that DC-feasible but AC-infeasible decisions are caught."),
  N2("Test more models, including a local open-weight model where energy can be measured, and more repeats per mode."),
  N2("Scale up to larger PGLib cases (e.g. 118 and 300 buses) and more requests, including requests written by other operators."),
  N2("Explore a learned OPF solver (e.g. a LOOP-LC-style network) behind the same checker and verifier, to test whether the safety layers still hold when the solver is approximate."),
);

// 11 Reproducibility
body.push(H1("11. Reproducibility"),
  P("All code, data and results are in the repository. The main files:"),
  table(["Path", "Contents"], [
    ["data/pglib_opf_case30_ieee.m", "Test grid (PGLib-OPF)"],
    ["data/requests.jsonl, data/ground_truth.jsonl", "Benchmark requests and answers (written by the researcher)"],
    ["src/", "Network, DC-OPF, DC power flow, edits, checker, verifier, LLM client, agent, evaluation"],
    ["results/raw/", "Every LLM call (12 logs: 4 modes × 3 repeats)"],
    ["results/runs/", "Graded run records"],
    ["results/tables/", "by_mode, by_group and per-record scored tables"],
    ["results/figures/", "Figures 1-2 and their data"],
    ["results/failures.csv", "Every failed decision, for labelling"],
    ["tests/", "101 pytest tests, all passing"],
  ], [3800, 5226], { size: 17, mono: [true, false] }),
  Caption("Table 12. Repository contents."),
  P("Commands (Python 3, packages in requirements.txt, API key in a local .env file):", { para: { keepNext: true } }),
  Mono("python -m pytest -q", true),
  Mono("python scripts/run_all.py --modes all --backend api --all --repeats 3", true),
  Mono("python scripts/evaluate_runs.py", true),
  Mono("python scripts/make_figures.py", true),
  Mono("python scripts/run_all.py --modes all --backend api --ids R17 --repeats 3 --redo"),
  gap(),
);

// Appendix A
body.push(H1("Appendix A. Benchmark requests"),
  P("The 40 requests as written by the researcher, with the expected result (canonical diff, or REFUSE)."),
  table(["Id", "Group", "Request", "Expected"], D.requests.map(r => [r.id, r.group, r.text, r.expected]),
    [650, 1050, 4826, 2500], { size: 15 }),
);

// Appendix B
body.push(new Paragraph({ children: [new PageBreak()] }), H1("Appendix B. System prompt (M2-M4)"),
  P("The edit-mode system prompt. The grid description (buses, generators, branches and base case) is inserted where marked. It contains no benchmark request or answer."),
  ...D.prompt.split("\n").map(l => Mono(l.length ? l : " ")),
);

// ---------- document ----------
const doc = new Document({
  creator: "Shoaib Ahmad",
  title: "LLM Agents for Grid Operator Requests - Technical Report",
  description: "Pilot study, IEEE 30-bus DC-OPF, four agent structures",
  styles: {
    default: { document: { run: { font: FONT, size: 21 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, font: FONT, color: ACCENT }, paragraph: { spacing: { before: 360, after: 160 }, outlineLevel: 0, keepNext: true } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: FONT, color: "2E2E2E" }, paragraph: { spacing: { before: 240, after: 100 }, outlineLevel: 1, keepNext: true } },
    ],
  },
  numbering: { config: [
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 540, hanging: 300 } } } }] },
    { reference: "steps", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 540, hanging: 360 } } } }] },
    { reference: "numbers", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 540, hanging: 360 } } } }] },
  ] },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT,
      children: [new TextRun({ text: "LLM agents for grid operator requests: technical report", size: 16, color: "808080" })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [new TextRun({ children: ["Page ", PageNumber.CURRENT, " of ", PageNumber.TOTAL_PAGES], size: 16, color: "808080" })] })] }) },
    children: body,
  }],
});

fs.mkdirSync(path.dirname(OUT), { recursive: true });
Packer.toBuffer(doc).then(buf => { fs.writeFileSync(OUT, buf); console.log("wrote", OUT); });
