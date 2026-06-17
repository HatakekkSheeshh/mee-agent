# Frontend image — build the React/Vite app to static assets, serve with nginx.
#
# DEV does NOT use this: run `npm run dev` on the host for HMR. This image is for
# prod/demo and is defined-but-not-started in dev (compose `frontend` profile).
#
# VITE_* vars are baked at BUILD time (Vite inlines them), so pass any you need
# as build args, e.g. --build-arg VITE_KICKOFF_ROLE="AI Applied".

FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG VITE_KICKOFF_ROLE
ENV VITE_KICKOFF_ROLE=$VITE_KICKOFF_ROLE
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
