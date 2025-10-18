# Frontend multi-stage build
FROM node:20 AS build
WORKDIR /app
COPY package.json package-lock.json* ./
## Deterministic, fail-fast install. Ensure devDependencies are present for Vite build (msw is required at build time).
## Force include=dev to ignore any external NPM_CONFIG_PRODUCTION/NODE_ENV settings.
RUN if [ -f package-lock.json ]; then npm ci --include=dev; else npm install --include=dev; fi \
 && test -d node_modules/dompurify || (echo "dompurify is missing after install. Check package.json/lockfile." && npm ls dompurify --depth=0; exit 1) \
 && test -d node_modules/msw || (echo "msw is missing after install. Vite cannot resolve 'msw/browser'. Check devDependencies and lockfile." && npm ls msw --depth=0; exit 1) \
 && ls -la node_modules/.bin || true \
 && npx --yes vite --version || true
COPY . .
# Normalize possible CRLF in npm-installed bin scripts and ensure exec perms, then build via node to avoid shell wrapper issues
RUN mkdir -p node_modules/.bin \
 && find node_modules/.bin -type f -exec sed -i 's/\r$//' {} \; \
 && chmod -R +x node_modules/.bin \
 && /usr/local/bin/npm run build

FROM nginx:1.27-alpine AS prod
ARG BUILD_SHA="unknown"
ARG BUILD_TIME="unknown"
ENV BACKEND_ORIGIN=http://app:8000
COPY --from=build /app/dist /usr/share/nginx/html
# Inject backend origin for simple runtime override (rewrite small JS stub)
RUN echo 'window.__BACKEND_BASE="'$BACKEND_ORIGIN'";' > /usr/share/nginx/html/backend-origin.js \
 && sed -i '1i <script src="/backend-origin.js"></script>' /usr/share/nginx/html/index.html || true
# Inject build metadata (sha, time) - use provided args for determinism
RUN echo "window.__BUILD_SHA='${BUILD_SHA}'; window.__BUILD_TIME='${BUILD_TIME}';" > /usr/share/nginx/html/build-meta.js \
 && sed -i '1i <script src="/build-meta.js"></script>' /usr/share/nginx/html/index.html || true
COPY nginx.prod.conf /etc/nginx/nginx.conf
RUN echo 'OK' > /usr/share/nginx/html/health.txt
EXPOSE 80
CMD ["nginx","-g","daemon off;"]
