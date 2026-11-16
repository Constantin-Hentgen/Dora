# syntax=docker/dockerfile:1
# BRouter with Dora's custom profiles next to its built-in ones (profiles2/, where lookups.dat is). Baked into the image
# rather than bind-mounted, so the stack also works when only this build context is available, e.g. a remote build:
#   build: https://github.com/Constantin-Hentgen/Dora.git#v0.4.0   (dockerfile: brouter.Dockerfile)
# The base image is set by BROUTER_IMAGE in docker-compose.yml (pinned by digest; see the wiki, "Self-hosting").
ARG BROUTER_IMAGE
FROM ${BROUTER_IMAGE}
COPY profiles/*.brf /profiles2/
