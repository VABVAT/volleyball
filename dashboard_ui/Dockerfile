# Build assets in-image so `docker compose up --build` works without a local dist/.
FROM node:20-alpine AS builder
WORKDIR /app
COPY dashboard_ui/package.json dashboard_ui/package-lock.json ./
RUN npm ci
COPY dashboard_ui/ ./
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY dashboard_ui/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
