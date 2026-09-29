FROM nginx:1.27-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/index.html docker/styles.css docker/app.js /usr/share/nginx/html/
EXPOSE 80
