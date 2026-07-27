#!/bin/sh
set -eu

config_file=${TTD_DEV_CONFIG_FILE:-/etc/ttd-dev-agent/controller.env}
if [ -f "$config_file" ]; then
	set -a
	. "$config_file"
	set +a
fi

agent_dir=$(dirname "$0")
exec "$agent_dir/node_modules/.bin/tsx" "$agent_dir/src/index.ts"
