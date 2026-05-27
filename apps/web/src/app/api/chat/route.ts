/**
 * POST /api/chat — thin SSE pass-through to services/brain.
 *
 * The LLM lives in Python on the subscription path; the Next runtime never
 * talks to a model. This route just pipes the brain's text/event-stream body
 * straight to the browser, so we don't buffer or re-encode the stream.
 *
 * The brain container is reachable inside the compose network at
 * http://brain:3032; unreachable from the browser, which is why this proxy
 * exists (same pattern as /api/strategy/preview → backtest-api).
 */

export const dynamic = "force-dynamic";

const BRAIN_URL = process.env.BRAIN_URL ?? "http://brain:3032";

export async function POST(req: Request) {
  const body = await req.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${BRAIN_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      // Tool loops can run a while; cap so a stuck brain doesn't pile up.
      signal: AbortSignal.timeout(120000),
    });
  } catch (e) {
    return new Response(
      `event: error\ndata: ${JSON.stringify({ message: `brain unreachable: ${e}` })}\n\n`,
      { status: 502, headers: { "content-type": "text/event-stream" } },
    );
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
    },
  });
}
