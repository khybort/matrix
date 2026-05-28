import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // OrbStack proxies the container under a custom domain; without this Next 16
  // will refuse _next/* asset requests as cross-origin in dev.
  allowedDevOrigins: ["matrix.local", "*.matrix.local"],
};

export default nextConfig;
