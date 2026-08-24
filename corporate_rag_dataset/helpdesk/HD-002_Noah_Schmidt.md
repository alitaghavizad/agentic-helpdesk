# Noah Schmidt

**Helpdesk ID:** HD-002  
**Role:** L2 Support Engineer  
**Primary specialization:** Windows endpoint support  
**Shift:** 12:00-20:00 CET  
**Escalation authority:** Standard  

## Support profile
Noah is a synthetic member of the Northstar corporate helpdesk. Their primary responsibility is windows endpoint support, with additional responsibility for general employee troubleshooting and ticket ownership. They work from a shared support queue and are expected to document diagnostics, actions, approvals, handoffs, and final resolution in ServiceNow.

## Systems
Regularly used administrative or diagnostic systems include Jira, GitHub Enterprise, Microsoft Entra ID, Okta. Administrative access is role-scoped and must not be interpreted as unlimited authority. High-risk operations such as privileged access changes, production actions, executive account recovery, or security-sensitive requests require identity verification and, where policy requires it, secondary approval.

## Routing guidance
Route tickets to HD-002 when the dominant issue concerns windows endpoint support. If an incident spans multiple systems, this member may perform initial triage and transfer the case to the relevant specialist. Urgent security events should be escalated immediately rather than handled as ordinary productivity issues.

## Retrieval hints
Useful terms include: windows endpoint support, l2 support engineer, hd-002, escalation, service desk, ticket routing, troubleshooting, diagnostics, support ownership. These terms make the profile useful for semantic-routing and helpdesk-assignment retrieval tests.

## Typical ticket workflow
HD-002 normally begins by reading the employee’s request, identifying the affected service, checking urgency, and determining whether the issue is an incident, access request, service request, or general question. The support member should verify the requester’s identity before performing account recovery, permission changes, or other sensitive actions.

After initial classification, the agent gathers evidence such as error messages, timestamps, affected systems, device type, recent changes, and whether other employees are experiencing the same problem. The goal is to avoid premature fixes and establish whether the issue is local, account-specific, network-related, application-specific, or part of a broader outage.

## Diagnostic approach
For problems within windows endpoint support, this helpdesk member should follow a structured diagnostic sequence. First, confirm the impact and scope. Second, check known incidents, service health, recent changes, and existing knowledge-base articles. Third, collect only the minimum information required for diagnosis. Fourth, perform low-risk corrective actions that are permitted by the support role.

Potential actions can include session resets, configuration checks, device-policy syncs, access-group verification, service-status review, log inspection, or routing to a system owner. Destructive or high-risk actions should not be attempted merely to “see if they work.”

## Escalation rules
The declared escalation authority for this profile is Standard. This value does not override company policy. Even senior support staff must obtain required approvals before performing privileged access changes, production interventions, or security-sensitive actions.

Escalation should occur when identity cannot be verified, the problem involves suspected compromise, the requested action exceeds role permissions, multiple business-critical users are affected, or the issue persists after standard diagnostics. The ticket should include a concise summary of what was checked, what was ruled out, and what evidence supports the escalation.

## Ticket documentation standards
Each ticket should record the requester, affected service, impact, symptoms, diagnostic actions, approvals, changes made, and resolution. If the issue is transferred, the receiving team should not have to repeat the entire investigation.

Support notes should separate observed facts from assumptions. For example, “VPN authentication failed at 10:14 CET with error X” is an observation, while “the identity provider is probably down” is a hypothesis until verified.

## Cross-team collaboration
Although the primary specialization is Windows endpoint support, this helpdesk member may collaborate with IAM, Security, Network Engineering, Cloud Platform, Application Engineering, HR Systems, Finance Systems, Sales Operations, or Workplace IT. Cross-team cases should retain a single ticket owner where possible so the employee is not forced to coordinate multiple internal teams.

When another team owns the final resolution, HD-002 may still be responsible for keeping the requester informed, collecting missing evidence, and verifying that the issue is actually resolved before closure.

## Security and privacy behavior
Support staff should never request passwords, MFA recovery codes, or secrets through chat or ticket comments. Sensitive identifiers should be handled according to company policy and redacted where appropriate. Account recovery must use approved verification steps.

A helpdesk member’s administrative access is functional, not personal. Tools such as identity-management consoles, endpoint-management platforms, observability systems, or SaaS admin panels should only be used to resolve legitimate business requests.

## Example routing scenarios
A query like “Who should handle this type of issue?” should retrieve HD-002 when the dominant problem matches windows endpoint support. Examples may include tickets that use different wording, abbreviations, or incomplete descriptions. The retriever should infer semantic similarity rather than depend entirely on exact keyword overlap.

Queries may also require multi-document retrieval. For example, a request can mention an employee by name and describe a technical issue. A good RAG system should retrieve both the employee profile and this helpdesk profile when the specialization is relevant.

## Shift and availability context
The normal shift for this helpdesk member is 12:00-20:00 CET. Shift information can help an orchestration layer decide whether to assign a new ticket immediately, route it to another available specialist, or place it in a queue. Shift information should not be treated as proof of real-time availability unless the system also checks a live scheduling or presence source.

For benchmarking purposes, the shift is static synthetic metadata. This allows evaluation of queries such as “Which specialist is assigned to this area and what shift do they normally work?”

## RAG evaluation notes
This profile is intentionally long enough to support chunk-level experiments. Chunking by Markdown section is recommended because the document contains different retrieval intents: specialization, routing, diagnostics, escalation, security, and availability.

When evaluating at document level, preserve `HD-002` and the source filename as parent-document metadata. If several chunks from the same helpdesk profile appear in the top-K results, collapse them before calculating document-level Precision@K, Recall@K, MRR, or nDCG. At chunk level, evaluate separately so repeated chunks do not distort the interpretation of retrieval quality.
