const path = require("path");
const webpack = require("webpack");

const opalDist = path.resolve(__dirname, "node_modules/@onyx-ai/opal/dist");
const emptyCss = path.resolve(__dirname, "src/app/css/_empty.css");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  transpilePackages: ["@onyx-ai/opal"],
  webpack: (config) => {
    // Opal's published JS still references its internal `@opal/*` source
    // alias (tsup didn't rewrite them). Map those to the dist tree.
    config.resolve.alias = {
      ...(config.resolve.alias || {}),
      "@opal": opalDist,
    };
    // The published artifact's JS files also import per-component CSS by
    // path, but only the bundled `styles.css` ships in dist/. We import
    // that bundle in globals.css; redirect the per-component CSS imports
    // to an empty stub so the bundler doesn't fail resolving them.
    config.plugins.push(
      new webpack.NormalModuleReplacementPlugin(/\.css$/, (resource) => {
        if (
          resource.request.startsWith("@opal/") &&
          resource.request.endsWith(".css")
        ) {
          resource.request = emptyCss;
        }
      }),
    );
    return config;
  },
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
