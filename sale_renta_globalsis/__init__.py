import logging

from . import models

_logger = logging.getLogger(__name__)


def uninstall_hook(env):
    """Recompute sale order line amounts on uninstall."""
    try:
        lines = env['sale.order.line'].search([], limit=500)
        if lines:
            lines._compute_amount()
    except Exception as e:
        _logger.warning("Error recomputing amounts: %s", e)