# ClaimGuard: Layer-2 Agent Prompts

Prompts and output schemas of the three Layer-2 agents used in the paper
*ClaimGuard: A Hybrid Pre-filtering and Multi-Agent LLM Framework for Health
Insurance Anomaly Investigation in Corporate Settings* (IEEE ITIS 2026).

The prompts are published exactly as they were run. They are written in
Indonesian because the claims data and the auditors are Indonesian. No claims,
MCU, or member data are included in this repository.

## Contents

| Path | Description |
| --- | --- |
| `prompts/01_rule_auditor.md` | Rule Auditor prompt and its ICD-10 section |
| `prompts/02_mcu_expert.md` | MCU Expert prompt |
| `prompts/03_decision_maker.md` | Decision Maker prompt, including the risk-score calibration rules |
| `prompts/institutional_rules.txt` | The eight institutional rules inserted as `{self.rules_text}` |
| `code/agent_definitions.py` | Source code that builds the prompts and the LangGraph flow |

## Pipeline and settings

- Flow (LangGraph): Rule Auditor → MCU Expert → Decision Maker. The MCU Expert
  is skipped when a candidate has no MCU record.
- Model: `gemini-3.1-flash-lite` via `langchain_google_genai.ChatGoogleGenerativeAI`.
- Temperature: 0.3. Maximum output tokens: 8192.
- Each prompt is sent as a single user message and must return JSON; the
  expected schema is written at the end of each prompt.
- ICD-10 descriptions come from the 2019 ICD-10-CM release
  (`https://raw.githubusercontent.com/Bobrovskiy/ICD-10-CSV/master/2019/diagnosis.csv`);
  the prompt labels this section "ICD-10 WHO".

## Placeholders

| Placeholder | Meaning |
| --- | --- |
| `{self.rules_text}` | The eight rules in `prompts/institutional_rules.txt` |
| `{stat.get(...)}`, `{nilai_rj}` | Candidate's 2024 totals: outpatient (RJ) and inpatient (RI) claim counts, outpatient value (IDR), distinct facilities, distinct diagnoses, active months |
| `{rule.get('skor')}`, `{rule.get('detail')}` | Rule violation score (0–8) and the violated rules |
| `{icd10_section}` / `{icd10_ctx}` | ICD-10 code and description for each of the candidate's diagnoses |
| `{json.dumps(p.get('klaim_per_bulan')[-6:])}` | Last six monthly summaries: month, number of claims, total value, RJ and RI counts, distinct facilities, up to five ICD-10 codes |
| `{json.dumps(mcu_detail)}` | Latest MCU values (blood glucose, blood pressure, total cholesterol, uric acid, HbA1c, BMI, Hb, creatinine) and categories |
| `{diagnoses}` | Up to ten distinct ICD-10 codes claimed by the candidate |
| `{lap_rule...}`, `{lap_mcu...}` | Fields of the Rule Auditor and MCU Expert JSON reports |
| `{prob}` | Layer-1 score: rule violation score, or the Isolation Forest anomaly score for IF-only candidates |
| `{state.get('skenario_id')}` | Run label (`produksi` in the reported experiment) |

## Notes

- `code/agent_definitions.py` also contains a single-agent variant
  (`agent_single`). It was prepared for an ablation study that was not run, and
  no results from it are reported in the paper.
- Prompts contain no member identifiers; identifiers in the underlying data were
  masked with salted hashing before analysis.
