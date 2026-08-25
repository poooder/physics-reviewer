#!/bin/sh
set -eu

mkdir -p /data
chown -R reviewer:reviewer /data

exec gosu reviewer "$@"
