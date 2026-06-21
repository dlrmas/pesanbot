"""Panel admin — impor semua submodul agar handler terdaftar di router."""
from app.features.admin import (  # noqa: F401
    banner,
    broadcast,
    economy,
    manage,
    panel,
    review,
    roles,
    stats,
    updater,
    users,
)
