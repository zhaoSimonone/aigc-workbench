FROM node:20-bookworm-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY schema.sql ./schema.sql
COPY src ./src
COPY scripts ./scripts
ENV NODE_ENV=production
EXPOSE 18080
CMD ["node", "src/index.js"]
