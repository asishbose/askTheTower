# The idea spark — why "is my line OK?"

Ask the Tower started from reading court filings and fraud statistics, not from a product brief. The pattern in
them is the same every time, and the gap it reveals is the one this project fills.

## What the cases say

| Source | What it describes |
|---|---|
| U.S. Department of Justice, Central District of California: ["'SIM Swapper' Sentenced to Eight Years in Prison for Campaign of Fraud and Deception"](https://www.justice.gov/usao-cdca/pr/sim-swapper-sentenced-eight-years-prison-campaign-fraud-and-deception-including) | A Los Angeles man sentenced to 96 months for a series of frauds, including SIM-swapping victims, one of them a social-media influencer. |
| U.S. Department of Justice, Eastern District of Louisiana: ["California Resident Pleads Guilty for His Role in SIM Swap Scam Targeting at Least 40 People"](https://www.justice.gov/usao-edla/pr/california-resident-pleads-guilty-his-role-sim-swap-scam-targeting-least-40-people) | A guilty plea in a SIM-swap scheme with at least forty victims. |
| Thomson Reuters Institute: ["A deep dive into the growing threat of SIM swap fraud"](https://www.thomsonreuters.com/en/institute/articles/sim-swap-fraud) | SIM-swap fraud growing at scale; the weakness is in telecom authentication processes; regulators are being asked to intervene. |
| SecurityWeek: ["T-Mobile Coughed Up $33 Million in SIM Swap Lawsuit"](https://www.securityweek.com/t-mobile-coughed-up-33-million-in-sim-swap-lawsuit/) | A $33 million private arbitration award over a SIM-swap attack that led to cryptocurrency theft. |
| DeepStrike: ["SIM Swap Scam Statistics 2025: Losses & Prevention"](https://deepstrike.io/blog/sim-swap-scam-statistics-2025) | 2025 figures: tens of millions of dollars in U.S. losses and a four-digit percentage rise in UK cases over recent years. |

Four things are true in every one of these:

1. **The attack starts at the carrier, not at the victim's computer.** A SIM is reissued or ported, or calls are
   forwarded, by social-engineering or bribing carrier staff. No password is broken.
2. **The victim's first signal is their phone going dead.** Loss of signal, then a flood of password-reset
   texts arriving somewhere else.
3. **The window is minutes.** The theft (crypto, bank, social accounts) is done before the victim finds a working
   phone, reaches a carrier agent and proves who they are.
4. **The standard advice is wrong for the moment.** "Call your carrier" means calling from a phone the attacker
   now holds, or a line whose calls are being forwarded to them.

## What already exists, and what doesn't

The carriers hold the facts. The CAMARA open APIs (SIM Swap, Call Forwarding Signal, Device Reachability) answer
"was this SIM changed recently?", "are calls on this line forwarded?", "is this device on the network?" in one
call each. Banks use them before a high-value transfer; some carriers sell a SIM lock. That is all on the
institution's side.

Nobody had put the question in the hands of the person, at the moment it matters, from a device that still
works: *"Alexa, is my line OK?"* And nobody had done it for the people who get hit hardest, older relatives, in a
way their family could watch over **with consent**, so that a swap on Mom's line texts her son's phone, not the
phone that was just stolen.

## What that turned into

- **One question, three tools.** `line_is_ok` asks the carrier and answers with reason codes and a fixed
  sentence; `watch_line` turns on proactive alerts; the consent flow binds a line once on the holder's own phone
  and lets them grant a named person a revocable watch.
- **The right next step.** When the SIM has moved, Tower does not say "call 611". It says *don't call from your
  phone* and hands the surface a `call_carrier` step so the Echo places the call.
- **Consent that bites.** Every proactive text re-checks the grant; a revoked grant drops the alert and audits it.
- **No phone number anywhere it could leak.** Numbers are HMAC'd into line ids; the audit chain is per line and
  tamper-evident.
- **Carrier-agnostic.** The CAMARA calls go through Amazon Bedrock AgentCore Gateway; a real operator replaces the
  mock with no change to Tower.

The three moments in the demo (`README.md`, "The problem, in three moments") are those four facts from the cases,
replayed with a happy ending: the swap is seen in seconds, the compromised phone is never trusted, and Mom
decides who gets told.

## Sources

- https://www.justice.gov/usao-cdca/pr/sim-swapper-sentenced-eight-years-prison-campaign-fraud-and-deception-including
- https://www.justice.gov/usao-edla/pr/california-resident-pleads-guilty-his-role-sim-swap-scam-targeting-least-40-people
- https://www.thomsonreuters.com/en/institute/articles/sim-swap-fraud
- https://www.securityweek.com/t-mobile-coughed-up-33-million-in-sim-swap-lawsuit/
- https://deepstrike.io/blog/sim-swap-scam-statistics-2025
