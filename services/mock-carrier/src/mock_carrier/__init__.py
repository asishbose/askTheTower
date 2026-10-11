"""CAMARA-conformant mock carrier with scenario engine, clock, subscriptions and admin API."""

__all__ = ["create_app"]


def __getattr__(name: str) -> object:
    if name == "create_app":
        from mock_carrier.app import create_app

        return create_app
    raise AttributeError(name)
