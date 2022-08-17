# Expander

[![CI](https://github.com/science-computing/expander/actions/workflows/container-ci.yml/badge.svg)](https://github.com/science-computing/expander/actions/workflows/container-ci.yml)
[![Container Images](https://github.com/science-computing/expander/actions/workflows/container-image-publish.yml/badge.svg)](https://github.com/science-computing/expander/actions/workflows/container-image-publish.yml)

[![](docs/expander-logo.svg)](docs/expander-logo.svg)

This is a Karton-based archive extractor, job submitter and tracker as well as
results correlator for use with Peekaboo.

[![](docs/expander-schematic.svg)](docs/expander-schematic.svg?raw=true)

Submit jobs e.g. using curl:

``` shell
curl -F file=@wheels.zip http://127.0.0.1:8200/v1/scan
```

Check for and retrieve job reports using the job UUID returned by the upload to
the scan endpoint:

``` shell
curl http://127.0.0.1:8200/v1/report/c71cda68-6e15-4051-a55e-4ccb93f26329
```

Spinning up all the required components by hand is tedious. Use the supplied
compose file to do that all at once:

``` shell
docker-compose up --build
```

See the dev subdirectory on how to get a dummy Peekaboo for testing.
