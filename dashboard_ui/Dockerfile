# Stage 1: build
FROM node:20-alpine AS build
WORKDIR /app
COPY dashboard_ui/package.json dashboard_ui/package-lock.json ./
RUN npm ci
COPY dashboard_ui/ ./
RUN npm run build

# Stage 2: serve
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY dashboard_ui/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

