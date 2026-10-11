# Prior art

Dated **2026-10-06**. The deck keeps platform names generic ([`review-response.md`](review-response.md) P3); this file names them. Each entry has what it **does**, what it **doesn't** do compared with Ask the Tower, and a link.

Ask the Tower is the consumer's own voice agent asking about the consumer's own line, with per-line, per-tool, revocable consent and a consented second person who gets the alert.

Links were checked on 2026-10-06:
- **fetched**: the page was opened and read.
- **fetched (JS)**: the page returned 200 but renders client-side, so only search-result text could be read.
- **not fetched**: the site refused automated access (403) and the URL comes from search results.

None of these entries is a claim that the product lacks a feature beyond what its page shows.

**Summary.** Every MCP server over carrier network APIs that we found targets developers or businesses and authenticates with the application's client credentials. Every consumer protection we found is a lock, a reply-YES text, or an app on the device. None lets a person's own assistant ask the network about that person's line, or about a line someone has consented to share.

## 1. Carrier network-API platforms and their MCP servers

| Name | Does | Doesn't | Link |
|---|---|---|---|
| **Nokia Network as Code — MCP Server** | Gives AI coding tools and agents (Cursor, VS Code, Claude) MCP access to Network as Code's API catalogue. The catalogue includes SIM Swap, Call Forwarding Signal, Number Verification and Device Reachability Status. | Developer-side, on application credentials. No end-user consent per line; not a voice surface. | [networkascode.nokia.io/docs/mcp-server](https://networkascode.nokia.io/docs/mcp-server) (fetched (JS)) |
| **Telefónica + Nokia agentic network-API lab** (GSMA Open Gateway) | A testbed using "an MCP server provided by Nokia's Network Exposure Platform to expose Network APIs" (SIM Swap, Device Swap). The first use case is "a bank fraud prevention agent". | A lab, on the business side. Telefónica lists "clear privacy and consent mechanisms" as still to be addressed ([RCR Wireless, 19 Feb 2026](https://rcrwireless.com/20260219/carriers/telefonica-apis), fetched). | [telefonica.com press release, 10 Feb 2026](https://www.telefonica.com/en/communication-room/press-room/telefonica-nokia-collaborate-accelerate-network-api-adoption-agentic-ai/) (fetched) |
| **Telefónica Open Gateway — SIM Swap** | "Integrate SIM swap detection and management functionality into your applications." | B2B API; no MCP or consumer access mentioned. | [opengateway.telefonica.com/en/apis/sim-swap](https://opengateway.telefonica.com/en/apis/sim-swap) (fetched) |
| **Aduna** (Ericsson joint venture with AT&T, T-Mobile, Verizon, DT, Orange, Telefónica, Vodafone and others) | One aggregator for CAMARA Number Verification and SIM Swap across the three major US carriers. | No MCP server found. Enterprise-only. | [TelecomTV, 26 Feb 2026](https://www.telecomtv.com/content/apis/aduna-reaches-milestone-as-a-leading-provider-of-us-network-apis-54933) (fetched); official site not reachable on the day |
| **Orange — CAMARA MCP Provider Implementation (PI1)** | An open-source (Apache-2.0) MCP server with 27 tools over CAMARA APIs, including SIM swap and reachability, against Orange's Playground. Connects "Claude Desktop, Cline, Goose, or any MCP-compatible agent". | Sandbox, on developer client credentials. No end-user consent model, no Call Forwarding Signal tool, no alerting. The closest technical analogue we found. | [developer.orange.com blog, 17 Jul 2026](https://developer.orange.com/blog/orange-provider-implementation/) (fetched) · [github.com/camaraproject/MCPEnablement_PI1](https://github.com/camaraproject/MCPEnablement_PI1) (fetched) |
| **Ericsson Research + Vonage** | Describes MCP exposure of SIM Swap, Device Location, SMS and Voice to AI agents. | A research write-up; consent not addressed. | [ericsson.com blog, 25 Nov 2025](https://www.ericsson.com/en/blog/2025/11/network-apis-for-ai-agents) (fetched) |
| **T-Mobile US DevEdge — SIM Swap** | "Real-time information on SIM events" for businesses. | B2B; no MCP server found for DT or T-Mobile. | [devedge.t-mobile.com/sim-swap](https://devedge.t-mobile.com/sim-swap) (not fetched) |

## 2. The community CAMARA MCP server

The review named a community "camara-sdk" MCP server. On the day, we found no repository by that name.

The community implementation under the CAMARA project itself is **MCPEnablement_PI1** (Orange, row above). A glama.ai listing describes another FastMCP "CAMARA" server with a dummy backend and only QoD and edge-discovery tools ([glama.ai/mcp/servers/oxt2ucv0va](https://glama.ai/mcp/servers/oxt2ucv0va), not fetched). `vrtornisiello/mcp-camara` is unrelated: it serves Brazil's Câmara dos Deputados.

## 3. Vonage MCP servers

| Name | Does | Doesn't | Link |
|---|---|---|---|
| **Vonage MCP servers** (Documentation; Server API Bindings / tooling) | "Enables AI agents and developer tools to discover, describe, and use Vonage capabilities via the Model Context Protocol." Tools cover SMS, WhatsApp, RCS, voice, numbers and reports. The tooling server added `check-sim-swap` (Identity Insights, 240-hour window). | Business credentials. No end-user consent per line; no Call Forwarding Signal; not a voice assistant. | [developer.vonage.com/en/mcp-server/overview](https://developer.vonage.com/en/mcp-server/overview) (fetched) · [github.com/Vonage-Community/vonage-mcp-server-api-bindings](https://github.com/Vonage-Community/vonage-mcp-server-api-bindings) (fetched) · [`check-sim-swap` blog, 17 Dec 2025](https://developer.vonage.com/en/blog/contribute-to-the-open-source-vonage-mcp-tooling-server) (fetched) |
| **Vonage Network APIs** | SIM Swap and Number Verification for businesses, after pre-registration and approval. | B2B only. | [GA announcement, 27 Feb 2024](https://developer.vonage.com/en/blog/announcing-vonage-network-apis-available-now) (fetched) |

## 4. The CAMARA MCP position paper

**"In Concert: Bridging AI Systems & Network Infrastructure through MCP"**, a CAMARA project (Linux Foundation) position paper published in January 2026.

**Does:** it sets out the architecture Tower uses, an MCP server that turns CAMARA APIs into tools. Its fraud example is a *banking* agent that "checks for recent SIM swap activity".

**Doesn't:** it treats consent as work still to do: "CAMARA API calls must include the scope and purpose of each requested data item to ensure user consent is obtained when required". It also says "the MCP layer must abstract the complexities of consent management". Tower's per-line, per-tool, revocable consent is one concrete answer to that open point, on the consumer side.

Links: [PDF](https://camaraproject.org/wp-content/uploads/sites/12/2026/01/camara_wp_mcp_011226.pdf) (fetched) · [Linux Foundation press release, 12 Jan 2026](https://www.linuxfoundation.org/press/camara-charts-a-path-for-network-aware-ai-applications-with-mcp) (fetched).

## 5. SIM-swap checks for businesses

| Name | Does | Doesn't | Link |
|---|---|---|---|
| **Twilio Lookup v2 — SIM Swap** | "Retrieve details about the last Subscriber Identity Module (SIM) change". US and Canada covered; Canada needs special approval; carrier registration required. | For businesses (typically before an OTP). The person whose line it is cannot ask. | [twilio.com/docs/lookup/v2-api/sim-swap](https://www.twilio.com/docs/lookup/v2-api/sim-swap) (fetched). We found no standalone Twilio *Verify* SIM-swap page on the day. |
| **Prove — Trust Score** | A real-time phone-risk score that includes a SIM-swap check, for businesses. | No consumer self-query. | [prove.com blog, 25 Mar 2021](https://www.prove.com/blog/prove-trust-score-ready-to-detect-and-stop-new-fraud-vector) (fetched) |

## 6. Carrier SIM locks (US)

These locks prevent. Tower detects and tells a second person, so it is a complement, not a replacement (deck, prior-art slide).

| Name | Does | Doesn't | Link |
|---|---|---|---|
| **AT&T Wireless Account Lock** | "Disables specific transactions and account changes for all devices and lines" on the account; switched on and off in the AT&T app. | No status query ("was I swapped?"), no voice, no alert to a second person. | [att.com support](https://www.att.com/support/article/wireless/000102016) (fetched) |
| **T-Mobile SIM Protection** | Free postpaid feature against common SIM-swap fraud, set in the account's privacy settings. | Same as above. | [t-mobile.com support](https://www.t-mobile.com/customers/6305378821) (not fetched) |
| **Verizon Number Lock / SIM Protection** | Number Lock "blocks anyone from moving your mobile number to another carrier"; SIM Protection blocks moving it "to another device". | Same as above. | [verizon.com account security](https://www.verizon.com/about/account-security/how-does-verizon-protect-my-account) (fetched) |

## 7. Canadian carriers

| Name | Does | Doesn't | Link |
|---|---|---|---|
| **Rogers** | On a transfer request, texts the line: "To approve this request, please reply YES"; it is cancelled after 90 minutes without a reply. | The text goes to the line being attacked. No on-demand check, and nothing reaches a second person. | [rogers.com — port fraud and SIM swaps](https://www.rogers.com/support/cyber-security/fraud-scams/port-fraud-and-sim-swaps) (fetched) |
| **Bell, TELUS** | Account PINs and SIM-change confirmation texts are described in secondary sources. | We did not find a primary Bell page on the day. The TELUS Wise article was not fetchable. | [TELUS Wise — SIM swap fraud](https://www.telus.com/en/wise/resources/content/article/understanding-sim-swap-fraud-and-how-to-protect-yourself) (not fetched) |
| **Industry / CRTC** | The CWTA's 2020 update to the CRTC on unauthorised number transfers and SIM swapping (file 8665-C12-202000280); its fraud-prevention details are redacted. | We found no CRTC *decision* on SIM swaps. | [CWTA update PDF](https://canadatelecoms.ca/wp-content/uploads/2023/03/2020.09.17.Update-CRTC-file-8665-C12-202000280-Unauthorized-telephone-number-transfers-and-SIM-swapping-in-Canada.pdf) (fetched) |

## 8. Family locators

These differ from Tower in two ways ([`review-response.md`](review-response.md) P2). They need an app on the other person's phone, and they show a location. Tower needs nothing on the phone and returns a yes or no.

| Name | Does | Doesn't | Link |
|---|---|---|---|
| **Apple Find My** | "Share your location with friends and family in real time." | Needs Apple devices signed in on both ends; returns a position, not network state; no SIM-swap or forwarding facts. | [apple.com/icloud/find-my](https://www.apple.com/icloud/find-my/) (fetched) |
| **Verizon Family** (formerly Smart Family) | Location sharing and controls through the Verizon Family app; the child's device needs the companion app. | Same as above. | [verizon.com support](https://www.verizon.com/support/verizon-smart-family/) (fetched) |
| **T-Mobile FamilyMode** | Real-time location and geofences, paid add-on. | Same as above. | [t-mobile.com/apps/t-mobile-family-mode](https://www.t-mobile.com/apps/t-mobile-family-mode) (not fetched) |

## 9. Carrier skills on Alexa

| Name | Does | Doesn't | Link |
|---|---|---|---|
| **Verizon Skill for Alexa** | Balance, bill due date, device payoff, usage, order status, plan details. Its page says it "will be retired on September 30, 2026". | No network facts and no security questions. | [verizon.com/support/alexa-skill](https://www.verizon.com/support/alexa-skill/) (fetched) |
| **AT&T** (send a text from an Echo, 2016) · **T-Mobile with Alexa** (calling) | Messaging and calling. | Same as above. | [about.att.com](https://about.att.com/story/att_customers_can_now_send_a_text_message_with_amazon_echo.html) (not fetched) · [t-mobile.com](https://www.t-mobile.com/support/plans-features/t-mobile-with-alexa) (not fetched) |
| Rogers, Bell | No Alexa skill found on the day. | | |

## 10. The standards and the surface Tower builds on

- **CAMARA APIs** (all fetched):
  - [SIM Swap](https://github.com/camaraproject/SimSwap) (r3.3: sim-swap 2.1.0, subscriptions 0.3.0);
  - [Call Forwarding Signal](https://github.com/camaraproject/CallForwardingSignal) (r3.3: 0.4.0);
  - [Device Reachability Status](https://github.com/camaraproject/DeviceReachabilityStatus) (r1.2: 1.1.0, subscriptions 0.8.0);
  - [Number Verification](https://github.com/camaraproject/NumberVerification) (r3.2: 2.1.0; three-legged auth over the device's mobile data).

  Tower's vendored copies and their versions are in [`specs/camara/`](../specs/camara/).
- **Alexa+ MCP Toolkit**: "connect your MCP server to Alexa+, giving customers access to your capabilities through voice and visual interactions". US only; Streamable HTTP; MCP spec 2025-11-25. [developer.amazon.com — MCP Toolkit overview](https://developer.amazon.com/en-US/docs/alexaplus/add-ons/mcp-toolkit-overview.html) (fetched).

## What is new — and it is a narrow slice

From the deck's prior-art slide, unchanged:

- A consumer-agent surface for network facts — the person's own assistant asking
- A network fact changing what the assistant is willing to do
- Consent and line binding for lines you don't own

## Numbers

These are the figures quoted in the root README, checked on 2026-10-06 against primary sources.

| Figure | Source (fetched) |
|---|---|
| 971 SIM-swap complaints and $17,366,758 in reported losses in 2025. The same report's three-year comparison gives 1,075 complaints and $48.8 M in 2023, and 982 complaints and $26.0 M in 2024. | [FBI IC3 2025 Internet Crime Report](https://www.ic3.gov/AnnualReport/Reports/2025_IC3Report.pdf), crime-type tables |
| Age 60+: 205 SIM-swap complaints, $6,342,329 in losses (2024) | [FBI IC3 2024 Internet Crime Report](https://www.ic3.gov/AnnualReport/Reports/2024_IC3Report.pdf) |
| "Nearly 3,000 cases of unauthorised SIM swaps" filed to the UK National Fraud Database in 2024, up 1,055 % | [Cifas, 7 May 2025](https://www.cifas.org.uk/newsroom/huge-surge-see-sim-swaps-hit-telco-and-mobile) |

Reported US complaints and losses *fell* from 2023 to 2025, while UK filings rose. The deck's tracks slide says SIM-swap fraud is "real and rising". That holds for the UK figure, not for the US complaint counts; it is recorded as a deck-consistency finding.
