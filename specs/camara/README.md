# CAMARA specifications — vendored

These six OpenAPI files are the **only** source of CAMARA paths, version segments, schemas and
error codes in this repository. The mock carrier (`services/mock-carrier`) serves them, routes from
them and is conformance-tested against them; the carrier client (`packages/camara-client`) and the
AgentCore Gateway registration read paths from the same files. Nothing here is hand-edited.

## Meta-release

**CAMARA Fall25** (released September 2025; Commonalities 0.6). Chosen on 2026-10-06 as the most
recent meta-release whose files could be fetched; the Spring26 line (`r4.x`) was still at
release-candidate versions (`2.2.0-rc.3`, `0.5.0-rc.1`, …) on that date. Every file below carries
`x-camara-commonalities: 0.6`, so the error-code vocabulary is uniform across the set.

## Files

| File | API version | Path version segment | Source (raw.githubusercontent.com/camaraproject/…) | Tag |
|---|---|---|---|---|
| `sim-swap.yaml` | sim-swap 2.1.0 | `/sim-swap/v2` | `SimSwap/r3.3/code/API_definitions/sim-swap.yaml` | `r3.3` |
| `sim-swap-subscriptions.yaml` | sim-swap-subscriptions 0.3.0 | `/sim-swap-subscriptions/v0.3` | `SimSwap/r3.3/code/API_definitions/sim-swap-subscriptions.yaml` | `r3.3` |
| `call-forwarding-signal.yaml` | call-forwarding-signal 0.4.0 | `/call-forwarding-signal/v0.4` | `CallForwardingSignal/r3.3/code/API_definitions/call-forwarding-signal.yaml` | `r3.3` |
| `number-verification.yaml` | number-verification 2.1.0 | `/number-verification/v2` | `NumberVerification/r3.2/code/API_definitions/number-verification.yaml` | `r3.2` |
| `device-reachability-status.yaml` | device-reachability-status 1.1.0 | `/device-reachability-status/v1` | `DeviceReachabilityStatus/r1.2/code/API_definitions/device-reachability-status.yaml` | `r1.2` |
| `device-reachability-status-subscriptions.yaml` | device-reachability-status-subscriptions 0.8.0 | `/device-reachability-status-subscriptions/v0.8` | `DeviceReachabilityStatus/r1.2/code/API_definitions/device-reachability-status-subscriptions.yaml` | `r1.2` |

The repository tag is the Fall25 release tag of each CAMARA sub-project (each repo numbers its own
release cycle; `r3.3` of SimSwap and `r1.2` of DeviceReachabilityStatus are the same meta-release —
see each repo's `CHANGELOG.md`, which labels the tag "Fall25"). Device reachability lives in the
`DeviceReachabilityStatus` repository; the older `DeviceStatus` repository stopped at
`device-reachability-status` 1.0.0 (Spring25) and is not used.

SHA-256 of the vendored bytes (verify with `sha256sum specs/camara/*.yaml`):

```
553015e6d917ad0c137e284747ca33d8d2558ef4af1bdc344f1c4c4b76ffbaf4  call-forwarding-signal.yaml
8535406dabfd71ebfcc3106a55933b8f194ed4156dc3c0d207ffe430bbef669b  device-reachability-status-subscriptions.yaml
522514fe2af8c4fafaa008a4eaf386259ce870ca579b50ac38250794b6b7e456  device-reachability-status.yaml
45a2d3344275e4682d291ad010a9aad3250f590194a4ca60b22a56ba5afafd40  number-verification.yaml
e3d2d6132cf87e7739a567a1e7ca6e3dc5baa729d501b9b211a72946223aff86  sim-swap-subscriptions.yaml
b0cda1408fc88b366fc16d04f157478205a1736e81393332172a67f20832648c  sim-swap.yaml
```

Commit SHAs behind the tags (lightweight tags; `git ls-remote https://github.com/camaraproject/<repo> refs/tags/<tag>`,
re-checked 2026-10-06 — the bytes above were re-downloaded from the same raw URLs and are identical):

| Repository | Tag | Commit |
|---|---|---|
| `camaraproject/SimSwap` | `r3.3` | `6008716efcbe055a6ef71ef74df59b2d53e70b30` |
| `camaraproject/CallForwardingSignal` | `r3.3` | `e7629fa72b58f6c912fd5b7e3f4dd6b083a1a955` |
| `camaraproject/NumberVerification` | `r3.2` | `5659864ab36d6b5280870ff2bfd753b6bbb6e590` |
| `camaraproject/DeviceReachabilityStatus` | `r1.2` | `d958ab75b347d50d9eefd20142e6e6093b74cb62` |

Full source URLs are `https://raw.githubusercontent.com/camaraproject/<Repository>/<Tag>/code/API_definitions/<file>`.
On 2026-10-06 the newest tags were `r4.1` (SimSwap, CallForwardingSignal, NumberVerification) and
`r2.1` (DeviceReachabilityStatus), all carrying `-rc.N` versions (Spring26 not yet public), so
Fall25 remains the most recent fetchable public meta-release.

## Operations (what the mock implements)

| Operation id | Method and full path |
|---|---|
| `checkSimSwap` | `POST /sim-swap/v2/check` |
| `retrieveSimSwapDate` | `POST /sim-swap/v2/retrieve-date` |
| `createSimSwapSubscription` | `POST /sim-swap-subscriptions/v0.3/subscriptions` |
| `retrieveSubscriptionList` | `GET /sim-swap-subscriptions/v0.3/subscriptions` |
| `retrieveSubscription` | `GET /sim-swap-subscriptions/v0.3/subscriptions/{subscriptionId}` |
| `deleteSubscription` | `DELETE /sim-swap-subscriptions/v0.3/subscriptions/{subscriptionId}` |
| `retrieveUnconditionalCallForwarding` | `POST /call-forwarding-signal/v0.4/unconditional-call-forwardings` |
| `retrieveCallForwarding` | `POST /call-forwarding-signal/v0.4/call-forwardings` |
| `phoneNumberVerify` | `POST /number-verification/v2/verify` |
| `phoneNumberShare` | `GET /number-verification/v2/device-phone-number` |
| `getReachabilityStatus` | `POST /device-reachability-status/v1/retrieve` |
| `createDeviceReachabilityStatusSubscription` | `POST /device-reachability-status-subscriptions/v0.8/subscriptions` |
| `retrieveDeviceReachabilityStatusSubscriptionList` | `GET /device-reachability-status-subscriptions/v0.8/subscriptions` |
| `retrieveDeviceReachabilityStatusSubscription` | `GET /device-reachability-status-subscriptions/v0.8/subscriptions/{subscriptionId}` |
| `deleteDeviceReachabilityStatusSubscription` | `DELETE /device-reachability-status-subscriptions/v0.8/subscriptions/{subscriptionId}` |

## Error codes present in this meta-release

`INVALID_ARGUMENT`, `OUT_OF_RANGE`, `INVALID_PROTOCOL`, `INVALID_CREDENTIAL`, `INVALID_TOKEN`,
`INVALID_SINK` (400) · `UNAUTHENTICATED` (401) · `PERMISSION_DENIED`,
`NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK`, `SUBSCRIPTION_MISMATCH` (403) ·
`NOT_FOUND`, `IDENTIFIER_NOT_FOUND` (404) · `ABORTED`, `ALREADY_EXISTS` (409) ·
`SERVICE_NOT_APPLICABLE`, `MISSING_IDENTIFIER`, `UNNECESSARY_IDENTIFIER`, `UNSUPPORTED_IDENTIFIER`,
`MULTIEVENT_SUBSCRIPTION_NOT_SUPPORTED` (422) · `QUOTA_EXCEEDED`, `TOO_MANY_REQUESTS` (429) ·
`NOT_IMPLEMENTED` (501) · `UNAVAILABLE` (503).

The older codes `DEVICE_NOT_FOUND` and `UNIDENTIFIABLE_DEVICE` (Commonalities 0.4) are **not** in
Fall25; their roles are taken by `IDENTIFIER_NOT_FOUND` and, for Number Verification, by
`NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK`.

## Load-time patches (code, not files)

The mock does not edit these files. `services/mock-carrier/src/mock_carrier/specs.py` applies two
changes to the in-memory copy it serves at `/openapi.json` and `/openapi/<api>.json`:

1. `servers[0].variables.apiRoot.default` is set to the mock's own base URL (the file says
   `http://localhost:9091`), so Swagger UI and schemathesis target the mock.
2. `components.securitySchemes.openId.openIdConnectUrl` is pointed at the mock's own
   `/oauth2/.well-known/openid-configuration` (the file says `https://example.com/…`).

`cloudevents/` is absent on purpose: none of the six files references an external schema (checked
with `grep '$ref' | grep -v '#/'`).
