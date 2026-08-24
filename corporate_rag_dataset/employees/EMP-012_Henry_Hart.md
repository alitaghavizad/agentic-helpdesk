# Henry Hart

**Employee ID:** EMP-012  
**Department:** Finance  
**Role:** Controller  
**Location:** London  
**Employment type:** Full-time  
**Manager ID:** EMP-002  
**Corporate email:** henry.hart12@northstar.example

## Profile
Henry works in the Finance organization as a Controller. The employee primarily works from London and collaborates with cross-functional teams through company-managed systems. This profile is entirely synthetic and is intended only for retrieval-augmented generation testing, benchmarking, and demo environments. The employee should be treated as active unless another synthetic document explicitly states otherwise.

## Systems and access
Primary tools: SAP S/4HANA, Excel, Power BI. Access classification: Sensitive business-data access. The employee uses a company-managed Dell Latitude 7440 running macOS 15. Authentication is handled through corporate SSO with MFA. Access requests must follow least-privilege rules and require manager or system-owner approval where applicable.

## Support context
A representative recurring support issue for this employee is: Printer or office-network connectivity problem. Helpdesk agents should first verify identity, device status, account state, and service impact before changing credentials or permissions. Production, payroll, HR, legal, and finance access must never be granted solely because the requester asks for it.

## Retrieval hints
Useful search concepts include: finance, controller, london, dell latitude 7440, macos 15, printer or office-network connectivity problem. These terms are intentionally repeated in natural language to provide stable semantic anchors for embedding and keyword-based retrieval experiments.

## Daily responsibilities
This employee’s normal workday includes a mixture of individual execution, team coordination, and use of shared corporate systems. As a Controller in Finance, the employee is expected to maintain accurate work records, follow internal security policies, and use only approved tools for company business. Typical activities may include reviewing assigned tasks, participating in planning or status meetings, updating tickets, collaborating through chat and documentation platforms, and responding to operational or business requests from colleagues.

The employee is expected to keep Jira, ServiceNow, CRM, HR, finance, or other department-specific systems current where those systems are part of the role. Work should be traceable enough that another authorized employee can understand the state of a task without relying on private conversations. Sensitive data should only be copied into systems approved for that data classification.

## Collaboration context
EMP-012 commonly collaborates with colleagues inside Finance and may also work with Product, Engineering, Finance, HR, Legal, Operations, Sales, or Marketing depending on the task. Requests involving access, credentials, financial records, employee records, legal documents, or production systems must follow the owning team’s approval process.

The employee’s manager reference is EMP-002. When a helpdesk or operations workflow requires managerial approval, the requester should not be treated as self-approving. The approval should be obtained from the manager, system owner, or another authorized approver according to policy.

## Access and authorization boundaries
The employee may have access to multiple systems, but access to one system does not imply access to another. Existing membership in a project, repository, application, or department group is not sufficient evidence that a higher privilege should be granted. Requests for administrator rights, production access, payroll access, HR records, security tooling, or legal repositories require explicit authorization.

Authentication issues should normally be handled through approved identity-recovery procedures. Helpdesk personnel should not bypass MFA, disable security controls permanently, or provide temporary credentials through unapproved channels. If identity cannot be verified, the ticket should be escalated.

## Device and endpoint context
The employee works using a company-managed endpoint. Corporate devices are expected to have endpoint protection, encryption, device-management policies, and approved software. If a support ticket concerns device performance or connectivity, the support workflow should first distinguish between local device problems, identity problems, network problems, and service-side incidents.

Common first-line checks include confirming the device is enrolled, verifying the operating-system version, checking whether the user recently changed a password, validating VPN status, checking storage capacity, confirming system time, and determining whether the issue affects only one employee or multiple users.

## Example support history
A realistic support history for EMP-012 may include password synchronization issues, failed MFA challenges, VPN reconnect problems, application permission changes after an internal transfer, expired SSO sessions, local certificate problems, or access requests for new project responsibilities.

For retrieval testing, these examples are intentionally described in natural language. A semantic retriever should still be able to associate phrases such as “cannot log in after password change,” “VPN stopped working,” “MFA code never arrives,” or “lost Jira permissions” with the relevant employee and helpdesk specialties even when the wording differs from the original profile.

## Business process behavior
Requests from this employee should be evaluated based on business context rather than identity alone. A legitimate employee can still submit a request that requires additional approval. For example, a finance employee may need extra authorization for production database access, and an engineer may need security review before receiving privileged identity-management permissions.

When uncertainty exists, the system should prefer escalation over unauthorized action. RAG systems using this profile should retrieve the employee record as supporting context, but the final agent should apply the organization’s policy documents before taking sensitive actions.

## Location and communication context
The employee is associated with London. Location can matter for office-network troubleshooting, working hours, local device support, regional compliance, or routing to an appropriate helpdesk shift. Location should not be treated as a security credential and must not be used as the sole basis for approving access.

The corporate contact identifier for this employee is henry.hart12@northstar.example. This synthetic address exists only for test data. Retrieval systems can use it as an exact-match field, while semantic systems may rely more heavily on the employee name, ID, department, role, and issue context.

## RAG evaluation notes
This document is intentionally verbose so that chunking strategies can be tested. A retriever may index the whole document or split it by Markdown heading. When chunking, keep `EMP-012` and the source filename as metadata on every chunk so that retrieved chunks can be mapped back to the correct employee record.

Useful evaluation cases include exact employee lookup, department search, role search, location filtering, tool access questions, manager lookup, support routing, and compound queries that require both an employee document and a helpdesk document. The document contains overlapping semantic cues on purpose so that embedding quality, chunk-size choices, reranking, and top-K behavior can be evaluated.
