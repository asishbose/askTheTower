"""Alerts service (docs/architecture/components/06): the proactive path.

Carrier webhooks and scheduled polls converge on `evaluate.evaluate`; `send.deliver` re-resolves the grant,
rate-limits, audits and sends; `escalation` walks the chain. Entry points: `handler.lambda_handler` (AWS) and
`local.create_app` / `python -m alerts.local` (local).
"""
