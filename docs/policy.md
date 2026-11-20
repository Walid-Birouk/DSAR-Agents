# Policy and its sources

The rules are in [`src/article15/policy/policy.yaml`](../src/article15/policy/policy.yaml). Check them with
`uv run a15 policy-lint`, and see what an agent is told with `uv run a15 policy-render <agent>`.

## Sources

- **GDPR:** Regulation (EU) 2016/679, official text on
  [EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679). The same wording is on
  [gdpr-info.eu](https://gdpr-info.eu/).
- **EDPB:** [Guidelines 01/2022 on data subject rights, Right of access, v2.1](https://www.edpb.europa.eu/system/files/documents/2023-04/edpb_guidelines_202201_data_subject_rights_access_v2_en.pdf).

| Topic | Rule | What the source says |
|---|---|---|
| Deadline | R-DEADLINE | "without undue delay and in any event within one month of receipt of the request"; extendable "by two further months" for complexity or number of requests, announced within the first month with reasons (Art. 12(3)) |
| Counting the month | R-DEADLINE | A request received on 31 August is due 30 September. A deadline on a weekend or public holiday moves to the next working day (EDPB paras. 160-161) |
| Not acting | R-NO-ACTION | Inform within one month, with the reasons and the right to complain and seek a judicial remedy (Art. 12(4)) |
| Identity | R-IDENTITY | With reasonable doubts, ask for "additional information necessary to confirm the identity" (Art. 12(6)). "It is disproportionate to require a copy of an identity document" when the requester is already authenticated (EDPB paras. 73-74) |
| Contents | R-SCOPE | Purposes, categories, recipients, retention, rights, complaint, source, automated decision-making (Art. 15(1)(a)-(h)) |
| Copy | R-COPY | A copy of the personal data; further copies may carry a fee; a commonly used electronic form for electronic requests (Art. 15(3)) |
| Others' rights | R-OTHERS | The copy "shall not adversely affect the rights and freedoms of others" (Art. 15(4)). The EDPB says this means leaving out or making illegible the affected parts, not refusing the request |
| Excessive requests | R-EXCESSIVE | "Manifestly unfounded or excessive" requests: fee or refusal, with the burden of proof on the controller (Art. 12(5)). The EDPB says these concepts are read narrowly |
| Special categories | R-SPECIAL | Racial or ethnic origin, political opinions, religious or philosophical beliefs, trade union membership, genetic, biometric, health, sex life or sexual orientation (Art. 9(1)) |

Rules marked "project rule" are this project's own operating choices, not legal requirements.

This project is a demonstration, not legal advice.
