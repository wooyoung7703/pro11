# Frontend multi-stage build
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
## Deterministic, fail-fast install. Ensure devDependencies are present for Vite build (msw is required at build time).
## Force include=dev to ignore any external NPM_CONFIG_PRODUCTION/NODE_ENV settings.
RUN apk add --no-cache libc6-compat \
 && if [ -f package-lock.json ]; then npm ci --include=dev; else npm install --include=dev; fi \
 && test -d node_modules/dompurify || (echo "dompurify is missing after install. Check package.json/lockfile." && npm ls dompurify --depth=0; exit 1) \
 && test -d node_modules/msw || (echo "msw is missing after install. Vite cannot resolve 'msw/browser'. Check devDependencies and lockfile." && npm ls msw --depth=0; exit 1)
COPY . .
# Normalize CRLF in bin scripts and ensure executability (some CI hosts produce CRLF wrappers)
RUN find node_modules/.bin -maxdepth 1 -type f -exec sed -i 's/\r$//' {} + \
 && chmod +x node_modules/.bin/* \
 && node ./node_modules/vite/bin/vite.js build

FROM nginx:1.27-alpine AS prod
ENV BACKEND_ORIGIN=http://app:8000
COPY --from=build /app/dist /usr/share/nginx/html
# Inject backend origin for simple runtime override (rewrite small JS stub)
RUN echo 'window.__BACKEND_BASE="'$BACKEND_ORIGIN'";' > /usr/share/nginx/html/backend-origin.js \
 && sed -i '1i <script src="/backend-origin.js"></script>' /usr/share/nginx/html/index.html || true
COPY nginx.prod.conf /etc/nginx/nginx.conf
RUN echo 'OK' > /usr/share/nginx/html/health.txt
EXPOSE 80
CMD ["nginx","-g","daemon off;"]
