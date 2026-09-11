# syntax=docker/dockerfile:1

FROM node:22-alpine AS build

WORKDIR /app

# Vite inlines `import.meta.env.*` at BUILD time -- there is no runtime
# lookup -- so the API base has to be a build arg, not a container env var.
# It is the URL the BROWSER uses, so it must be reachable from the host
# (localhost:8080), never the compose service name, which only resolves
# inside the network.
ARG VITE_API_BASE=http://localhost:8080
ENV VITE_API_BASE=$VITE_API_BASE

# .npmrc carries `legacy-peer-deps` (see its own comment: openapi-typescript
# still peer-pins typescript 5.x), without which `npm ci` refuses to install.
COPY package.json package-lock.json .npmrc ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

FROM nginx:1.29-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# 127.0.0.1, not localhost: Alpine resolves localhost to ::1 first, and
# nginx.conf only listens on IPv4, so `localhost` is refused forever.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
