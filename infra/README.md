# infra

Infrastructure configuration. No application code lives here.

| Directory | Contents |
| --- | --- |
| `docker/` | Dockerfiles and container entrypoints |
| `compose/` | Compose files for local development and production overrides |
| `nginx/` | Reverse proxy configuration |
| `env/` | `.env.example` templates -- never real secrets |

Docker and Compose land in #6; CI in #7.
