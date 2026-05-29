/**
 * GET /api/chat/session/:id — proxy to brain's GET /sessions/:id
 * Returns { messages: [{role, content, tool_calls}], ... }
 */

export const dynamic = "force-dynamic";

const BRAIN_URL = process.env.BRAIN_URL ?? "http://brain:3032";

export async function GET(
  _req: Request,
  { params }: { params: { id: string } },
) {
  try {
    const res = await fetch(`${BRAIN_URL}/sessions/${params.id}`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return new Response(null, { status: res.status });
    const data = await res.json();
    return Response.json(data);
  } catch {
    return new Response(null, { status: 502 });
  }
}
