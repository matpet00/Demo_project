#!/bin/bash
set -euo pipefail
SERVER_HOME="${WSO2_SERVER_HOME:-/home/wso2carbon/wso2am-4.5.0}"
CONF="${SERVER_HOME}/repository/conf/deployment.toml"
if ! grep -q '\[oauth.grant_type.password\]' "$CONF"; then
  printf '\n[oauth.grant_type.password]\nenable = true\n' >> "$CONF"
fi
exec /home/wso2carbon/docker-entrypoint.sh "$@"
