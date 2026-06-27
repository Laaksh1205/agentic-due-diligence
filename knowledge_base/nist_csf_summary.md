# NIST Cybersecurity Framework (CSF) 2.0 — Curated Summary

> Curated summary of NIST CSF 2.0 (published 2024), focused on what matters for
> grading the cybersecurity risk of a vendor/counterparty. This is a working
> summary, not the verbatim publication. Source: NIST,
> <https://www.nist.gov/cyberframework>.

## Why CSF for vendor due diligence

CSF is the de facto industry standard for organizing cybersecurity risk. When a
risk signal touches a vendor's security posture (a breach, a missing control, a
certification), we ground severity in CSF: a gap in a **core protective control**
or a failure in **detection/response** is more severe than a peripheral issue,
and a vendor demonstrating maturity across the functions is lower risk.

CSF 2.0 also adds explicit emphasis on **Cybersecurity Supply Chain Risk
Management (C-SCRM)** — directly relevant to third-party/vendor assessment.

## The six Functions

CSF 2.0 organizes outcomes into six Functions (2.0 newly elevates **Govern**):

1. **GOVERN (GV)** — Establishes and monitors the organization's cybersecurity
   risk-management strategy, expectations, and policy. Includes roles &
   responsibilities, risk-management strategy, and **supply-chain risk
   management (GV.SC)**. A vendor with no security governance, no named owner,
   or no third-party risk program is a meaningful red flag.
2. **IDENTIFY (ID)** — Understand assets, data, suppliers, and risks. Asset
   management, risk assessment, and improvement. Weakness here (unknown assets,
   no risk assessment) underlies most downstream failures.
3. **PROTECT (PR)** — Safeguards to prevent/limit incidents: identity & access
   management (PR.AA), awareness & training, data security (PR.DS), platform
   security, and technology infrastructure resilience. Missing MFA, weak access
   control, unencrypted sensitive data are common, material gaps.
4. **DETECT (DE)** — Find and analyze possible attacks/compromises: continuous
   monitoring and adverse-event analysis. Inability to detect (no logging/SIEM)
   means breaches go unnoticed — raises severity of any incident.
5. **RESPOND (RS)** — Act on a detected incident: incident management, analysis,
   mitigation, reporting & communication. A vendor with no incident-response
   plan handles breaches poorly and notifies customers late.
6. **RECOVER (RC)** — Restore assets/operations after an incident: recovery plan
   execution and communication. Weakness extends downtime and customer impact.

## Mapping CSF maturity → severity of a cyber signal

Use these heuristics alongside the CYBERSECURITY section of the severity rubric:

- **CRITICAL indicators** — Active breach with no evidence of DETECT/RESPOND
  capability; exposure of large volumes of regulated data (PROTECT/Data Security
  failure); ransomware (RECOVER failure) halting operations; no governance over
  third parties (GV.SC absent) combined with a live incident.
- **HIGH indicators** — Confirmed breach of regulated data; repeated incidents
  (systemic PROTECT/DETECT weakness); missing fundamental controls (no MFA, no
  encryption) on sensitive systems.
- **MEDIUM indicators** — Remediated smaller incident; disclosed but unexploited
  vulnerabilities; partial control coverage; immature monitoring.
- **LOW indicators** — Minor lapse, no confirmed data loss; isolated
  misconfiguration corrected.
- **POSITIVE / INFO indicators** — Independent attestation of control maturity:
  ISO/IEC 27001 certification, SOC 2 Type II, alignment to CSF or NIST 800-53;
  demonstrated incident-response testing; mature C-SCRM program.

## Cybersecurity Supply Chain Risk Management (C-SCRM, GV.SC)

CSF 2.0 stresses managing risk from suppliers and third parties: set
requirements, assess suppliers before engagement, monitor them over time, and
plan for supplier incidents. For our purposes, a vendor that itself has weak
C-SCRM compounds risk for **its** customers — a fourth-party exposure worth
flagging when present.

## Implementation Tiers (context)

CSF describes Tiers 1–4 (Partial → Risk Informed → Repeatable → Adaptive)
characterizing how rigorous and adaptive an organization's practices are. A
vendor operating at "Partial" with sensitive data is higher risk than one
demonstrating "Repeatable/Adaptive" practices via certification.

## Key takeaways for the analyst

- Weigh **regulated/sensitive data exposure** heavily — it drives both severity
  and regulatory consequences (see GDPR / breach-notification rules).
- Distinguish **prevention gaps (PROTECT)** from **detection/response gaps
  (DETECT/RESPOND/RECOVER)**: the latter make any incident worse and slower to
  contain.
- Treat **independent certification (ISO 27001, SOC 2 Type II)** as a genuine
  positive signal of maturity, not marketing fluff.
