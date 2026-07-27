# PostgreSQL's versioned Alpine packages default to this socket directory.
# /run is a fresh tmpfs on every guest boot, and every slot has a distinct
# unprivileged Unix user, so recreate a shared sticky directory at runtime.
mkdir -p /run/postgresql
chmod 1777 /run/postgresql
