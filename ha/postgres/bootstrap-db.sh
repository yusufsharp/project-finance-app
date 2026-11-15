#!/bin/sh
set -eu
psql "$1" -v ON_ERROR_STOP=1 -c 'CREATE DATABASE finance OWNER finance'
