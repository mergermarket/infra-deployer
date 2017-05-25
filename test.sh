#!/bin/sh

set -e

docker build -t cdflow-commands.test -f Dockerfile.test .

docker run \
    --name cdflow-commands.test \
    --rm \
    -i $(tty -s && echo -t) \
    -v $(pwd)/.hypothesis/:/infra/.hypothesis/ \
    cdflow-commands.test py.test \
        --cov=. \
        --cov-report term-missing \
        "$@"

docker run --rm cdflow-commands.test flake8 --max-complexity=4

