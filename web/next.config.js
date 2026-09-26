/** @type {import('next').NextConfig} */
// The browser calls /api/* on this server and Next forwards it to FastAPI, so pages need no absolute URL,
// no env var and no CORS. Point API_ORIGIN elsewhere to use a remote API.
const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";

const nextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
};

module.exports = nextConfig;
