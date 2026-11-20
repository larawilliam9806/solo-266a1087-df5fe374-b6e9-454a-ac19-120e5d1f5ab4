"""Old-newspaper price-list entry tool."""

__all__ = ["create_app"]


def create_app(config=None):
    # Imported lazily so that importing pure helpers (pricing.layout,
    # pricing.csvparse) never constructs an app or touches a database.
    from .app import create_app as factory

    return factory(config)
