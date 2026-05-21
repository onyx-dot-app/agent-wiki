FROM node:20-alpine AS deps
WORKDIR /app
COPY --from=oven/bun:1-alpine /usr/local/bin/bun /usr/local/bin/bun
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS run
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/node_modules ./node_modules
EXPOSE 3000
CMD ["npm", "run", "start"]
