#!/usr/bin/env bash
set -euo pipefail

container="${ODOO_CONTAINER:-dz-odoo18}"
db_container="${PG_CONTAINER:-dz-db}"
database="${TEST_DATABASE:-dz_account_fix_upgrade_test}"
odoo_conf="/etc/odoo/odoo.conf"
host_tools="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
container_tools="/tmp/l10n_dz_account_fix_upgrade_test"

case "$database" in
    dz_account_fix_*_test) ;;
    *) echo "Refusing unsafe test database name: $database" >&2; exit 2 ;;
esac

docker exec "$db_container" psql -U odoo -d postgres -v ON_ERROR_STOP=1 \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$database' AND pid <> pg_backend_pid()"
docker exec "$db_container" dropdb --if-exists -U odoo "$database"
docker exec "$db_container" createdb -U odoo "$database"
docker exec "$container" mkdir -p "$container_tools"
docker cp "$host_tools/." "$container:$container_tools/"

docker exec "$container" odoo -c "$odoo_conf" -d "$database" -i l10n_dz \
    --without-demo=True --stop-after-init --no-http --log-level=warn
printf "exec(open('%s').read())\n" "$container_tools/prepare_upgrade.py" | \
    docker exec -i "$container" odoo shell -c "$odoo_conf" -d "$database" \
        --no-http --shell-interface=python
docker exec "$container" odoo -c "$odoo_conf" -d "$database" -i l10n_dz_account_fix \
    --without-demo=True --stop-after-init --no-http --log-level=warn
printf "exec(open('%s').read())\n" "$container_tools/verify_upgrade.py" | \
    docker exec -i "$container" odoo shell -c "$odoo_conf" -d "$database" \
        --no-http --shell-interface=python
docker exec "$container" odoo -c "$odoo_conf" -d "$database" -u l10n_dz_account_fix \
    --stop-after-init --no-http --log-level=warn
printf "exec(open('%s').read())\n" "$container_tools/verify_upgrade.py" | \
    docker exec -i "$container" odoo shell -c "$odoo_conf" -d "$database" \
        --no-http --shell-interface=python
printf "exec(open('%s').read())\n" "$container_tools/uninstall.py" | \
    docker exec -i "$container" odoo shell -c "$odoo_conf" -d "$database" \
        --no-http --shell-interface=python
docker exec "$container" odoo -c "$odoo_conf" -d "$database" \
    -u base --stop-after-init --no-http --log-level=warn
printf "exec(open('%s').read())\n" "$container_tools/verify_uninstall.py" | \
    docker exec -i "$container" odoo shell -c "$odoo_conf" -d "$database" \
        --no-http --shell-interface=python

echo "UPGRADE_FLOW_OK database=$database"
