# Risk Severity Rubric

> Authoritative reference for grading the severity of an extracted risk signal.
> The Risk Analysis Agent retrieves the relevant section(s) of this rubric and
> must ground every severity judgment in it (design doc Section 8f). Severity is
> assigned **before** temporal decay; the effective severity used for scoring is
> `base_severity × temporal_weight`.

## Severity levels (definitions)

- **CRITICAL** — Immediate threat to business continuity or legal/financial
  viability. An active, unresolved event a reasonable acquirer/procurement team
  would treat as a potential deal-breaker. Reserve for the most serious findings;
  in a typical assessment ≤ 10% of signals should be CRITICAL.
- **HIGH** — Significant risk requiring prompt attention. A concrete adverse
  event with material exposure, but not necessarily existential.
- **MEDIUM** — Notable concern warranting monitoring. Real but bounded, or older
  / partially remediated.
- **LOW** — Minor issue, largely informational. Common, low-impact, or
  industry-wide.
- **INFO** — Neutral or context-setting; not a risk per se. Routine corporate
  events and most POSITIVE signals (strengths) land here.

## Calibration guardrails

- Do **not** inflate severity. "Everything is HIGH" destroys user trust.
- An *alleged* or *pending* matter is generally one level below a *confirmed*
  adverse outcome of the same kind (e.g., pending suit = HIGH where a final
  large judgment would be CRITICAL).
- A *remediated* historical event is graded lower than an *active* one.
- POSITIVE signals (certifications, awards, strong liquidity) are graded INFO or
  LOW unless they are unusually material.
- **Company-size proportionality.** The absolute dollar figures in this rubric
  (e.g., "fine >$1M") assume a large enterprise. When the company's scale is
  known or inferable, grade the *relative* impact: a penalty around or above
  1% of annual revenue behaves like the rubric's "large" tier even if the
  absolute number is small, and a fine well under 0.1% of revenue is
  procedural even if the headline number sounds large. A $2M fine can be
  existential for a startup and noise for a mega-cap.
- **Jurisdiction does not change the severity logic.** An action by a non-US
  regulator (FCA, ICO, SFO, EU Commission, RBI, SEBI, ED — see the
  international regulatory reference) is graded on the same ladder as its US
  analog: restriction/revocation of the ability to operate > large fine >
  small fine > procedural notice.

---

## FINANCIAL

- **CRITICAL** — Going-concern doubt raised by auditors; insolvency / bankruptcy
  filing; default on debt; >50% revenue collapse; credit rating cut to default
  (D / SD).
- **HIGH** — >30% revenue decline; covenant breach; credit downgrade into junk
  (e.g., to BB- or below); sustained negative operating cash flow; large
  impairment.
- **MEDIUM** — 10–30% revenue decline; rising leverage; single-customer
  concentration >40% of revenue; margin compression.
- **LOW** — Minor revenue softness; modest debt increase; industry-wide
  headwinds affecting all players equally.
- **INFO / POSITIVE** — Strong liquidity / large net cash position; profitable
  growth; investment-grade rating; new funding round on healthy terms.

## LEGAL

- **CRITICAL** — Active fraud or criminal investigation; finalized judgment /
  settlement that threatens solvency; injunction halting core operations.
- **HIGH** — Pending major litigation; securities class action; large settlement
  ($ millions) recently paid; IP injunction risk to a core product.
- **MEDIUM** — Pending commercial disputes of bounded value; historical settled
  litigation; routine contract disputes with material counterparties.
- **LOW** — Minor or nuisance suits; small-claims matters; ordinary-course legal
  activity.
- **INFO** — Disclosure of standard legal proceedings with no material exposure.

## REGULATORY

- **CRITICAL** — Inclusion on a sanctions list (e.g., OFAC SDN); license
  revocation; ongoing enforcement action with potential to shut down operations;
  criminal regulatory referral.
- **HIGH** — Regulatory fine >$1M; FDA Warning Letter; consent order / consent
  decree; formal enforcement proceeding; license suspension.
- **MEDIUM** — Minor regulatory citations; smaller fines; remediation
  undertakings; warning with no penalty.
- **LOW** — Routine compliance findings resolved without penalty; administrative
  notices.
- **INFO / POSITIVE** — License granted/renewed; clean audit; certification of
  compliance.

## REPUTATIONAL

- **CRITICAL** — CEO/CFO resignation amid fraud allegations; product recall tied
  to deaths/serious injury; viral scandal causing customer/partner exodus.
- **HIGH** — Executive misconduct; large product recall; widespread negative
  national coverage; major customer terminations over conduct.
- **MEDIUM** — Negative press of bounded scope; localized recalls; elevated
  customer complaints; Glassdoor/employee sentiment sharply negative.
- **LOW** — Isolated negative reviews; minor PR missteps.
- **INFO / POSITIVE** — Industry award; "best employer" recognition; positive
  brand coverage.

## OPERATIONAL

- **CRITICAL** — Loss of the sole supplier/site for a core input with no
  alternative; collapse of a key dependency that halts delivery.
- **HIGH** — Single supplier for >80% of a critical component; all operations in
  one facility (concentration risk); loss of a key person with no succession.
- **MEDIUM** — Supplier concentration 40–80%; limited geographic diversification;
  capacity constraints.
- **LOW** — Minor supply-chain friction; ordinary operational variability.
- **INFO / POSITIVE** — Long, stable operating history; diversified supply base;
  business-continuity certification.

## CYBERSECURITY

- **CRITICAL** — Active/unresolved breach exposing >1M records or sensitive data;
  ransomware halting operations; breach under active regulatory investigation.
- **HIGH** — Confirmed data breach (tens of thousands to ~1M records); repeated
  incidents; exposure of regulated data (health, payment, biometric).
- **MEDIUM** — Smaller breach (thousands of records), remediated; disclosed
  vulnerabilities not yet known to be exploited.
- **LOW** — Minor security lapse with no confirmed data loss.
- **INFO / POSITIVE** — ISO 27001 / SOC 2 Type II certification; strong security
  posture; no known incidents.

## ESG

- **CRITICAL** — Egregious labor abuses (forced/child labor) confirmed; major
  environmental disaster with regulatory action; governance failure enabling
  fraud (no independent board, related-party self-dealing).
- **HIGH** — Material environmental penalty (e.g., EPA fine in the millions);
  confirmed labor-law violations with fines; serious governance red flags.
- **MEDIUM** — Smaller environmental citations; isolated labor complaints;
  limited board independence.
- **LOW** — Minor ESG criticisms; voluntary-disclosure gaps.
- **INFO / POSITIVE** — B Corp certification; strong sustainability program;
  diverse, independent board.

---

## Quick reference: what pushes a signal up or down a level

| Up a level | Down a level |
|---|---|
| Active / ongoing | Remediated / resolved |
| Confirmed / finalized | Alleged / pending |
| Large magnitude (>$1M, >1M records, >30%) | Small magnitude |
| Regulated/sensitive data or safety impact | Internal, non-sensitive |
| Recurring pattern | Isolated, one-off |
| Core business affected | Peripheral / non-core |
