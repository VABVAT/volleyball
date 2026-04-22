# Build assets in-image so `docker compose up --build` works without a local dist/.
# Use AWS Public ECR mirrors of Docker Official Images to avoid Docker Hub TLS/rate issues
# (registry-1.docker.io handshake timeouts). Same bits as library/node and library/nginx.
FROM public.ecr.aws/docker/library/node:20-alpine AS builder
WORKDIR /app
COPY dashboard_ui/package.json dashboard_ui/package-lock.json ./
RUN npm ci
COPY dashboard_ui/ ./
RUN npm run build

FROM public.ecr.aws/docker/library/nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY dashboard_ui/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
