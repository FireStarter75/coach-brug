#!/bin/bash
set -e
cd /mnt/user/appdata/garmin-coach
docker build -t garmin-coach:latest .
bash start.sh
