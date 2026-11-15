/**
 * The text an ApiError carries for a failed response. A proxy in front of
 * the backend (Cloudflare, Envoy) answers some failures with a full HTML
 * page, which must not end up rendered as an error message.
 */
export function errorBody(status: number, contentType: string | null, body: string): string {
  const isHtml = /text\/html/i.test(contentType ?? "") || /^\s*<(!doctype|html)/i.test(body);
  if (!isHtml) return body;
  return `The server is temporarily unavailable (HTTP ${status}). Try again in a moment.`;
}
