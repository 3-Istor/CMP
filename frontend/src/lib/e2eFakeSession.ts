export function isE2eFakeSessionRequest(hostname: string): boolean {
  return (
    process.env.NODE_ENV !== "production" &&
    process.env.CMP_E2E_FAKE_SESSION === "1" &&
    hostname === "localhost"
  );
}
