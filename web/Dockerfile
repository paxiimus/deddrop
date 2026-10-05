FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
# Vite bakes env vars into the built JS at build time, not runtime — changing
# any of these means rebuilding this image, not just restarting it.
ARG VITE_API_URL
ARG VITE_DEPLOYMENT_MODE
ARG VITE_MAX_PHOTOS
ARG VITE_MAX_VIDEOS
ARG VITE_MAX_DROP_LIFETIME_HOURS
ENV VITE_API_URL=${VITE_API_URL}
ENV VITE_DEPLOYMENT_MODE=${VITE_DEPLOYMENT_MODE}
ENV VITE_MAX_PHOTOS=${VITE_MAX_PHOTOS}
ENV VITE_MAX_VIDEOS=${VITE_MAX_VIDEOS}
ENV VITE_MAX_DROP_LIFETIME_HOURS=${VITE_MAX_DROP_LIFETIME_HOURS}
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
