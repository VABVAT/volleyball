# Serve prebuilt assets (built locally via npm run build)
FROM nginx:alpine
COPY dashboard_ui/dist /usr/share/nginx/html
COPY dashboard_ui/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

