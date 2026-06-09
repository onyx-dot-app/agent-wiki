/** @type {import('next').NextConfig} */
const { version } = require("./package.json");
const nextConfig = {
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_APP_VERSION: version,
  },
  output: "standalone",
  transpilePackages: ["@onyx-ai/opal"],
  async rewrites() {
    return [
      // In dev, proxy API calls to the backend container so the frontend
      // doesn't need to know about CORS. In prod, nginx handles this.
      {
        source: "/api/:path*",
        destination: process.env.BACKEND_URL
          ? `${process.env.BACKEND_URL}/api/:path*`
          : "http://backend:8080/api/:path*",
      },
    ];
  },
};
module.exports = nextConfig;
