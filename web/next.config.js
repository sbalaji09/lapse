/** @type {import('next').NextConfig} */
// Keep relative downloads and browser fetches on the same API even when local development uses a non-default port.
const API_ORIGIN = (
  process.env.API_ORIGIN
  ?? process.env.NEXT_PUBLIC_API_URL
  ?? "http://localhost:8000"
).replace(/\/$/, "");

const nextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
};

module.exports = nextConfig;
