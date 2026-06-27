# Regulatory Reference — Sanctions, Data Protection, Securities Enforcement

> Curated reference for grading REGULATORY, CYBERSECURITY, and LEGAL signals that
> touch sanctions, data-protection penalties, or securities enforcement. Working
> summaries for severity grounding — consult primary sources for legal use.
> Sources: OFAC (treasury.gov), EDPB (edpb.europa.eu), SEC (sec.gov/enforcement).

---

## 1. OFAC Sanctions and the SDN List

The U.S. Treasury's Office of Foreign Assets Control (OFAC) administers economic
sanctions. The **Specially Designated Nationals and Blocked Persons (SDN) List**
names individuals and entities whose assets are blocked and with whom U.S.
persons are generally **prohibited from dealing**.

**What inclusion means**
- Assets within U.S. jurisdiction are frozen; transactions are prohibited.
- Sanctions can be list-based (SDN) or comprehensive (whole jurisdictions).
- "50 Percent Rule": entities owned ≥50% by SDNs are themselves blocked, even if
  not separately listed.

**Severity implications**
- **CRITICAL** — Target entity (or its owner/key principal) appears on the SDN
  list, or is in a comprehensively sanctioned jurisdiction. Engaging is likely
  illegal for U.S. persons and a clear deal-breaker.
- **HIGH** — Credible sanctions-evasion allegations or an active OFAC
  investigation; ties to sanctioned parties below the 50% threshold.
- **MEDIUM/LOW** — Historical, resolved sanctions matters; exposure to sanctioned
  regions mitigated by controls.
- Note: secondary-sanctions and non-U.S. regimes (EU, UK OFSI, UN) carry similar
  weight; treat confirmed listing on any major regime as CRITICAL.

---

## 2. GDPR Penalty Guidelines (EU General Data Protection Regulation)

GDPR governs personal-data processing for EU residents. Fines are set by
supervisory authorities and the EDPB's calculation guidelines consider the
nature, gravity, and duration of the infringement, intent, mitigation, and
cooperation.

**Two fine tiers (whichever is higher)**
- **Lower tier** — up to **€10 million or 2% of total worldwide annual turnover**.
  For obligations such as records of processing, security of processing
  (Art. 32), breach notification (Arts. 33–34), and data-protection-by-design.
- **Upper tier** — up to **€20 million or 4% of total worldwide annual turnover**.
  For violations of core principles (Art. 5), lawful basis/consent (Arts. 6–9),
  data-subject rights (Arts. 12–22), and international-transfer rules.

**Breach notification**
- Controllers must notify the supervisory authority within **72 hours** of
  becoming aware of a personal-data breach (Art. 33), and affected individuals
  without undue delay when high risk (Art. 34). Late/absent notification is
  itself a violation.

**Severity implications**
- **CRITICAL** — Upper-tier fine levied (or likely) in the tens of millions;
  systemic unlawful processing of sensitive data; large breach with failure to
  notify.
- **HIGH** — Lower-tier fine in the millions; confirmed breach of regulated
  personal data; significant data-subject-rights failures.
- **MEDIUM/LOW** — Smaller fines, remediated; administrative reprimands without
  monetary penalty.
- **POSITIVE** — Documented GDPR compliance program, DPO appointed, clean
  regulator history.

---

## 3. SEC Enforcement Actions (U.S. Securities and Exchange Commission)

The SEC enforces federal securities laws against issuers, executives, and
intermediaries. Matters proceed administratively or in federal court.

**Common action types (roughly increasing severity)**
- **Wells Notice** — Staff indicates intent to recommend enforcement; the target
  may respond. Signals a likely action.
- **Cease-and-desist order** — Orders the respondent to stop violations; may
  include other relief.
- **Civil monetary penalties** — Fines; size scales with conduct and benefit.
- **Disgorgement** — Repayment of ill-gotten gains, plus prejudgment interest.
- **Injunctions** — Court orders barring future violations.
- **Officer-and-director bars** — Prohibits individuals from serving as officers/
  directors of public companies.
- **Referral for criminal prosecution** — Most serious; parallel DOJ action.

**What the SEC pursues** — Accounting/financial-reporting fraud, disclosure
failures, insider trading, market manipulation, FCPA (foreign bribery),
auditor-independence and internal-controls failures.

**Severity implications**
- **CRITICAL** — Active fraud investigation; finalized large penalty/disgorgement
  threatening solvency; criminal referral; officer-and-director bar of a key
  principal.
- **HIGH** — Formal enforcement proceeding; Wells Notice; settled action with
  multimillion-dollar penalty; material internal-controls / disclosure failures.
- **MEDIUM** — Smaller settled administrative matters; remediated control
  deficiencies.
- **LOW / INFO** — Routine comment letters; minor, resolved disclosure items.

---

## Cross-cutting grading notes

- **Confirmed vs. alleged**: a finalized sanction/penalty outranks an
  investigation of the same matter by ~one severity level.
- **Magnitude anchors**: fines >$1M and breaches >1M records skew HIGH→CRITICAL;
  sanctions-list inclusion is CRITICAL irrespective of magnitude.
- **Recency**: apply temporal decay — a remediated 2018 matter weighs less than a
  2025 active one, though regulatory violations decay more slowly than
  reputational ones because compliance issues tend to recur.
