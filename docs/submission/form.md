# Submission form — drafted text

Hackathon: **Build, Ship, Shape: Amazon Developer Hackathon** on Devpost,
[amazonappdev2026.devpost.com](https://amazonappdev2026.devpost.com/), with its
[rules](https://amazonappdev2026.devpost.com/rules). The deadline is **23 Oct 2026, 12:00 PDT**. The pages were
read on 2026-10-06.

The "Enter a Submission" form itself is behind a login and was not visible. The requirements below are therefore
quoted from the overview page and the rules. **TODO(human):** open the form, copy each field's exact label and
character limit into the table at the end, and trim the text to fit.

## Requirements as the pages state them (verbatim)

"What to Submit" (overview page):

- "A text description of your project: explain what it does and how it works."
- "A GitHub code repository: must contain all source code, assets, and instructions needed to run your project. The repository must be either public and open source by including an open source license file OR private and shared with testing@devpost.com and the Amazon team"
- "For Alexa+, Bee, and Ring: your repo needs to actually call your track's required technology in code, an import, an entry point, a loaded agent/flow/MCP config, not just a mention in the README."
- "Alexa+: show your Agent Skill or MCP server (spec 2025-11-25+, Streamable HTTP) in action."
- "A demo video under 3 minutes: YouTube or Vimeo, public and in English. Judges aren't required to watch past the 3-minute mark… Don't include third-party trademarks or copyrighted music/footage unless you have permission."
- "Product feedback on every tool, API, or SDK you used: what you used it for, what worked well, what needs work, how onboarding felt, and whether you'd build with it again. Building with AWS services? Describe which ones and how, right here in your feedback answer."
- "Which track(s) and mini challenge(s) you're entering."
- "If your project existed before the hackathon, a clear explanation of what you built or changed during the submission window."
- "Optional: Feature requests: what you'd want built, why it matters, and how urgent it is (critical / important / nice-to-have)."
- "Optional: Friction log entries for each one: the task you attempted, the steps you took, what you expected vs. what actually happened, a severity rating, any workaround you used, and an actionable suggestion. Submissions with friction logs can earn up to a 10% judging bonus."

Rules, Submission Requirements (verbatim excerpts):

- Alexa+ track: "A working Agent Skill or a self-hosted MCP server, implementing MCP spec version 2025-11-25 (or a later version, once confirmed) over Streamable HTTP."
- Public repo: "this license should be detectable and visible at the top of the repository page (in the About section)."
- Video: "should be less than three (3) minutes… should include footage that shows the Project functioning on the device for which it was built… must be uploaded to and made publicly visible on YouTube or Vimeo".
- Product Feedback questions:
  - "Which developer tools, apis and SDKs did you use and for what?"
  - "What worked well?"
  - "What needs work?"
  - "How was your onboarding experience (getting from zero to hello world)"
  - "Would you build with these devices and services again? Yes/No and please tell us why"
- AWS Builder: "Any primary track project that incorporates AWS services (i.e. Amazon Bedrock, AgentCore, Strands SDK, Kiro Crew, SageMaker, etc.) with documented integrations."
- Open Source: "Required: contribution URL, project repository URL, GitHub username, and a description of what you did, how it works, and why it matters." "PRs do not need to be merged".

Eligibility: the rules exclude residents of "Brazil, Quebec, Russia, Crimea, Cuba, Iran, and North Korea". **TODO(human):** confirm that neither team member lives in Quebec.

## Drafted fields

### Project name
Ask the Tower

### Tagline (≤ 10 words)
Ask your carrier about your line, from your Echo.

This is 9 words. Alternative, 10 words: "Your own Alexa+ agent asking the carrier about your line."

### Description

> Ask the Tower is an MCP server that connects to Alexa+ through the **Alexa+ MCP Toolkit**. With it, you can ask your carrier about your own phone line: "My phone just lost signal. Alexa, is my line OK?" A SIM swap feels like bad signal at first. The Echo is on home Wi-Fi, so after the phone goes dead it is the one device in the house that can still ask.
>
> Tower has three tools over standard CAMARA network APIs. `line_is_ok` covers SIM Swap and Call Forwarding Signal. `is_reachable` covers Device Reachability Status. `watch_line` texts a consented second person when one of those facts changes. Alexa+ cannot speak first, so alerts go by SMS.
>
> Consent comes first. A line is bound with one tap on the phone over mobile data (Number Verification). Grants are per line and per tool, revocable, and audited. Results are booleans and timestamps only: no location and no content. Policy is deterministic code, and no model sits in the request path; Alexa+ phrases the structured result.
>
> The demo runs against a CAMARA-conformant mock carrier: 15 operations, 1,036 generated conformance cases, 0 failing. `make up && make demo` reproduces it on a clean Docker machine in about two minutes. The same images run on local compose, on AWS (AgentCore Runtime, Gateway and Identity, Lambda, Fargate) and on Kubernetes from Helm.

**Status line for the end of the description.** Edit this at submission so it says what actually ran:

> Verified so far: local compose and a kind cluster (transcripts match golden, 4/4); 1,218 tests passing across unit, integration and end-to-end. Deployed to AWS: TODO(human). Alexa+ simulator run: TODO(human).

### Built with
The same list as the README's "Built with" section. Keep the two identical.

- Alexa+ MCP Toolkit
- Amazon Bedrock AgentCore Runtime
- Amazon Bedrock AgentCore Gateway
- Amazon Bedrock AgentCore Identity
- Amazon Bedrock + Strands Agents
- Amazon DynamoDB
- Amazon SNS
- Amazon EventBridge Scheduler
- AWS Lambda
- Amazon API Gateway
- AWS Fargate
- Amazon EKS
- AWS KMS
- CAMARA OpenAPI (GSMA Open Gateway)
- FastMCP
- Terraform
- Helm
- Python 3.12

### Tracks and mini challenges
- **Alexa+ (primary).** A self-hosted MCP server over Streamable HTTP, built to be connected through the Alexa+ MCP Toolkit.
- **AWS Builder mini.** Tower on AgentCore Runtime. Carrier calls go through AgentCore Gateway (CAMARA OpenAPI targets) with Identity for outbound OAuth. The rest of the stack is DynamoDB, KMS, SNS, EventBridge Scheduler, Lambda, API Gateway, Fargate and EKS. Bedrock appears only in the Strands reference client, off the request path.
- **Open Source mini.** The consent-and-line-binding kit, `packages/tower-consent` plus `services/binding-page`, under Apache-2.0. **TODO(human):** the rules require a *contribution URL* and a GitHub username. Decide what the contribution is: the repo itself, or a PR to an upstream project such as a CAMARA repository. Fill both in.

### Built during the submission window
The design and the deck were written on 2026-10-05. All code, tests, charts and Terraform were written from 2026-10-06 onwards, in the commits on `main`. **TODO(human):** confirm against the hackathon's start date.

### Product feedback (required)
Paste from [`product-feedback.md`](product-feedback.md) at submission time, one block per tool. The form asks five questions, and each section of that file answers them in order: Used for / Worked well / Needs work / Onboarding / Build again. Put the AWS services first, with how each was used, as the form asks.

**Blocked:** as of 2026-10-06, these sections are still ☐ because they have not been exercised:
- Alexa+ MCP Toolkit;
- AgentCore Runtime, Gateway, Identity and Observability;
- Bedrock + Strands;
- SNS, EventBridge Scheduler, Lambda + API Gateway, Fargate + ALB, EKS;
- KMS / Secrets Manager / CloudWatch.

They become ☑ only after the AWS deploy and the Alexa+ simulator run. Do not submit a Built With entry whose section is still ☐: finish the section or drop the entry.

### Friction log (optional, up to 10 % bonus)
Paste the live entries from [`../architecture/alexa/friction-log.md`](../architecture/alexa/friction-log.md) using the fields the page asks for: task, steps, expected vs actual, severity, workaround, suggestion. There are none yet.

### Feature requests (optional)
Draft. Confirm these after the simulator run.

- **Critical:** document the identity Alexa+ passes to an MCP server on each tool call (account-linking token shape, which claim is the user). The whole consent model depends on that one field (friction log, preparation finding 1).
- **Important:** a sanctioned proactive path for MCP add-ons, or at least a documented "notify the user" hand-off. Today alerts have to leave Alexa entirely (SMS).
- **Important:** availability of the toolkit and the web simulator outside the US. The team is in Canada.
- **Nice-to-have:** a way for a tool result to mark its `summary` as "read verbatim". Safety sentences ("call your carrier now") should not be paraphrased.

### Links
- **Repository:** TODO(human), the public GitHub URL. The licence (Apache-2.0) must show in the repo's About section.
- **Video:** TODO(human), YouTube or Vimeo, public, under 3 minutes, with the "Alexa+ MCP Toolkit" caption.
- **Deck PDF:** TODO(human), exported from `docs/Decks/Ask the Tower — Pitch.pptx`.

### Team
Asish Bose and Badhrinath Padmanabhan, Canada.

## Field table (fill from the live form)

| Form field (exact label) | Limit | Source above | Fits? |
|---|---|---|---|
| TODO(human) | | | |
