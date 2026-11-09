"""ecom-multi-agent-lab: LangGraph-based e-commerce campaign decision system."""

__version__ = "0.1.0"

__all__ = ["__version__", "run_campaign"]


def run_campaign(*args, **kwargs):
    """Lazy import so importing the package never requires graph dependencies."""
    from .runner import run_campaign as _run

    return _run(*args, **kwargs)
