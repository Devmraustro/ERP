#!/usr/bin/env bash
set -euo pipefail

container="${ODOO_CONTAINER:-dz-odoo18}"
db_container="${PG_CONTAINER:-dz-db}"
database="${TEST_DATABASE:-dz_account_fix_language_test}"
tools_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
container_tools="/tmp/l10n_dz_account_fix_language_test"

case "$database" in
    dz_account_fix_*_test) ;;
    *) echo "Refusing unsafe test database name: $database" >&2; exit 2 ;;
esac

docker exec "$db_container" psql -U odoo -d postgres -v ON_ERROR_STOP=1 \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$database' AND pid <> pg_backend_pid()"
docker exec "$db_container" dropdb --if-exists -U odoo "$database"
docker exec "$db_container" createdb -U odoo "$database"
docker exec "$container" mkdir -p "$container_tools"
docker cp "$tools_dir/verify_languages.py" "$container:$container_tools/verify_languages.py"

docker exec "$container" odoo -c /etc/odoo/odoo.conf -d "$database" \
    -i base --load-language=ar_001,fr_FR --without-demo=True \
    --stop-after-init --no-http --log-level=warn
docker exec "$container" odoo -c /etc/odoo/odoo.conf -d "$database" \
    -i l10n_dz_account_fix --without-demo=True \
    --stop-after-init --no-http --log-level=warn
printf "exec(open('%s').read())\n" "$container_tools/verify_languages.py" | \
    docker exec -i "$container" odoo shell -c /etc/odoo/odoo.conf -d "$database" \
        --no-http --shell-interface=python

echo "LANGUAGE_TEST_OK database=$database"
