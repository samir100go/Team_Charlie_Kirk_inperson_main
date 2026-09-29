// Build metadata injected at image build time (see web/Dockerfile).
export function GET() {
  return Response.json({
    service: "web",
    version: process.env.npm_package_version ?? "0.1.0",
    git_sha: process.env.GIT_SHA ?? "unknown",
    build_time: process.env.BUILD_TIME ?? "unknown",
    image_tag: process.env.IMAGE_TAG ?? "dev",
  });
}
